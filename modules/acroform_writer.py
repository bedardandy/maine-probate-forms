"""Stage 6: Write AcroForm interactive fields to PDF files."""

import logging
from pathlib import Path

import fitz  # PyMuPDF

import config
from modules.schema import (
    FieldType,
    FormNaming,
    FormOutput,
    GroupRole,
    NamedField,
    Rect,
    WrittenField,
)

logger = logging.getLogger(__name__)

# Map our FieldType to PyMuPDF widget types
WIDGET_TYPE_MAP = {
    FieldType.TEXT: fitz.PDF_WIDGET_TYPE_TEXT,
    FieldType.CHECKBOX: fitz.PDF_WIDGET_TYPE_CHECKBOX,
    FieldType.RADIO: fitz.PDF_WIDGET_TYPE_RADIOBUTTON,
    FieldType.SIGNATURE: fitz.PDF_WIDGET_TYPE_TEXT,  # signature as text; true sig needs cert
    FieldType.DATE: fitz.PDF_WIDGET_TYPE_TEXT,
    FieldType.CURRENCY: fitz.PDF_WIDGET_TYPE_TEXT,
}


_WIDGET_TYPE_TO_FIELD_TYPE = {
    "Text": FieldType.TEXT,
    "CheckBox": FieldType.CHECKBOX,
    "RadioButton": FieldType.RADIO,
    "Signature": FieldType.SIGNATURE,
}


def _load_sample_naming(form_id: str) -> FormNaming | None:
    """Load field definitions from a hand-corrected sample PDF, if one exists."""
    samples_dir = config.SAMPLES_DIR
    if not samples_dir.is_dir():
        return None

    # Find a PDF whose filename starts with the form_id followed by a space
    match = None
    for pdf in samples_dir.glob("*.pdf"):
        stem = pdf.stem
        if stem.startswith(form_id) and (
            len(stem) == len(form_id) or stem[len(form_id)] == " "
        ):
            match = pdf
            break
    if match is None:
        return None

    doc = fitz.open(str(match))
    fields: list[NamedField] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        for w in page.widgets():
            if w.field_type_string == "Button":
                continue
            ft = _WIDGET_TYPE_TO_FIELD_TYPE.get(w.field_type_string, FieldType.TEXT)
            fields.append(
                NamedField(
                    page=page_num,
                    rect=Rect(x0=w.rect.x0, y0=w.rect.y0, x1=w.rect.x1, y1=w.rect.y1),
                    field_type=ft,
                    field_name=w.field_name,
                    nearby_label=w.field_name,
                    confidence=1.0,
                )
            )
    doc.close()

    if not fields:
        return None

    return FormNaming(
        form_id=form_id,
        filename=match.name,
        category="",  # not needed for writing
        fields=fields,
    )


def _calculate_font_size(field_height: float) -> float:
    """Auto-size font based on field height."""
    size = field_height * config.FONT_SIZE_AUTO_SCALE
    # Clamp to reasonable range
    return max(6.0, min(size, 14.0))


def _create_widget(page: fitz.Page, field: NamedField,
                   shared_field_name: str | None = None) -> fitz.Widget:
    """Create a PyMuPDF Widget for a named field.

    `shared_field_name` is set for radio-group kids: all kids in one group
    share a parent field name (the group_id) so the AcroForm enforces
    mutual exclusion. The per-kid export value comes from `group_option`.
    """
    widget = fitz.Widget()
    widget.field_name = shared_field_name or field.field_name
    widget.field_type = WIDGET_TYPE_MAP.get(field.field_type, fitz.PDF_WIDGET_TYPE_TEXT)
    widget.rect = fitz.Rect(field.rect.x0, field.rect.y0, field.rect.x1, field.rect.y1)
    widget.border_width = config.WIDGET_BORDER_WIDTH
    widget.border_color = getattr(config, "WIDGET_BORDER_COLOR", (0, 0, 0))

    if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
        font_size = _calculate_font_size(field.rect.y1 - field.rect.y0)
        widget.text_fontsize = font_size
        widget.text_color = config.WIDGET_TEXT_COLOR

    if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        widget.field_value = "Off"

    if widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        # Each radio kid declares its export ("on") value via button_caption.
        # The parent field's value is set to one kid's caption to indicate
        # which option is selected. Default to unchecked.
        on_value = field.group_option or field.field_name
        widget.button_caption = on_value
        widget.field_value = "Off"

    if config.WIDGET_FILL_COLOR is not None:
        widget.fill_color = config.WIDGET_FILL_COLOR

    return widget


def write_form(naming: FormNaming, source_pdf: str | Path) -> FormOutput:
    """Add AcroForm fields to a PDF and save to the output directory.

    Args:
        naming: Named field data for the form.
        source_pdf: Path to the original (flat) PDF.

    Returns:
        FormOutput with metadata about what was written.
    """
    source_pdf = Path(source_pdf)

    # Determine output path
    out_dir = config.OUTPUT_DIR / naming.category
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = source_pdf.stem
    out_filename = f"{stem}_fillable.pdf"
    out_path = out_dir / out_filename

    doc = fitz.open(str(source_pdf))

    # Strip any pre-existing widgets from the source PDF so the output is
    # a clean snake_case AcroForm. Some publisher-provided forms (adoption,
    # guardian_minor, name_change, affidavits, notices) ship with their
    # own fillable widgets in inconsistent Title Case; leaving them in place
    # produces duplicate fields and breaks normalization downstream.
    stripped = 0
    for page in doc:
        for w in list(page.widgets() or []):
            try:
                page.delete_widget(w)
                stripped += 1
            except Exception:
                pass
    if stripped:
        logger.debug("  stripped %d pre-existing widget(s) from source", stripped)

    written_fields: list[WrittenField] = []
    used_names: set[str] = set()
    # Track each radio group's claimed parent name so all kids share it.
    radio_group_names: dict[str, str] = {}
    # Per-kid radio post-processing list: (xref, export_value).
    # PyMuPDF gives every radio kid the same default on-state ("Yes"), so
    # mutual exclusion via the parent /V doesn't work — setting the parent
    # to "Yes" matches every kid. We rewrite each kid's AP/N entry from
    # /Yes -> /<group_option> after add_widget, giving each kid a unique
    # export name and restoring proper radio-group semantics.
    radio_kid_patches: list[tuple[int, str]] = []

    for field in naming.fields:
        if field.page >= len(doc):
            logger.warning(
                "Field '%s' references page %d but PDF only has %d pages",
                field.field_name,
                field.page,
                len(doc),
            )
            continue

        is_radio_kid = (
            field.field_type == FieldType.RADIO
            and field.group_role == GroupRole.RADIO
            and bool(field.group_id)
        )

        if is_radio_kid:
            # All kids in a group share one PDF field_name (the group_id);
            # the AcroForm enforces mutual exclusion via that shared identity.
            # Export ("on") value per kid is the snake_case option label.
            shared_name = radio_group_names.get(field.group_id)
            if shared_name is None:
                # Reserve the group_id as the parent name. If it collides with
                # a previously-written non-radio name, suffix it.
                shared_name = field.group_id
                if shared_name in used_names:
                    suffix = 2
                    while f"{shared_name}_{suffix}" in used_names:
                        suffix += 1
                    shared_name = f"{shared_name}_{suffix}"
                radio_group_names[field.group_id] = shared_name
                used_names.add(shared_name)
            adjusted_field = field.model_copy()
            page = doc[field.page]
            try:
                widget = _create_widget(page, adjusted_field,
                                        shared_field_name=shared_name)
                annot = page.add_widget(widget)
                export_value = field.group_option or field.field_name
                radio_kid_patches.append((annot.xref, export_value))
                written_fields.append(
                    WrittenField(
                        field_name=shared_name,
                        field_type=field.field_type,
                        page=field.page,
                        rect=field.rect,
                        group_id=field.group_id,
                        group_role=field.group_role,
                        group_option=export_value,
                        parent_group_id=field.parent_group_id,
                    )
                )
            except Exception as e:
                logger.error(
                    "Failed to add radio kid '%s' (group %s) on page %d: %s",
                    field.field_name, field.group_id, field.page, e
                )
            continue

        # Ensure unique field names within the PDF
        name = field.field_name
        if name in used_names:
            suffix = 2
            while f"{name}_{suffix}" in used_names:
                suffix += 1
            name = f"{name}_{suffix}"
        used_names.add(name)

        # Update field name if it was changed for uniqueness
        adjusted_field = field.model_copy()
        adjusted_field.field_name = name

        page = doc[field.page]
        try:
            widget = _create_widget(page, adjusted_field)
            page.add_widget(widget)
            written_fields.append(
                WrittenField(
                    field_name=name,
                    field_type=field.field_type,
                    page=field.page,
                    rect=field.rect,
                    group_id=field.group_id,
                    group_role=field.group_role,
                    group_option=field.group_option,
                    parent_group_id=field.parent_group_id,
                )
            )
        except Exception as e:
            logger.error(
                "Failed to add widget '%s' on page %d: %s", name, field.page, e
            )

    # Patch each radio kid's appearance dict so its on-state has a unique
    # export name (default "Yes" → group_option). This is what makes the
    # radio group actually mutually exclusive: setting the parent /V to one
    # kid's export value uniquely identifies which kid is "on".
    #
    # Also clear the kid's /AS to /Off and its /V to /Off — PyMuPDF's
    # widget.update(field_value="Off") doesn't propagate through to /AS or
    # /V, so kids ship with /AS=/Yes which doesn't match either /Off or the
    # patched on-state. The result is undefined-state rendering: some
    # readers display every kid as checked.
    import re as _re
    for xref, export_value in radio_kid_patches:
        try:
            typ, val = doc.xref_get_key(xref, "AP/N")
            if typ == "dict":
                new_val = _re.sub(r"/Yes(\b)", f"/{export_value}\\1", val)
                if new_val != val:
                    doc.xref_set_key(xref, "AP/N", new_val)
            doc.xref_set_key(xref, "AS", "/Off")
            doc.xref_set_key(xref, "V", "/Off")
        except Exception as e:
            logger.warning("Failed to patch radio kid xref %d: %s", xref, e)

    # Save the document
    doc.save(str(out_path), garbage=3, deflate=True)
    doc.close()

    # Verify: re-open and count fields
    verify_doc = fitz.open(str(out_path))
    actual_count = 0
    for page in verify_doc:
        actual_count += sum(1 for _ in page.widgets())
    verify_doc.close()

    if actual_count != len(written_fields):
        logger.warning(
            "Verification mismatch for %s: wrote %d, found %d",
            naming.form_id,
            len(written_fields),
            actual_count,
        )
    else:
        logger.info("Verified: %d fields in %s", actual_count, out_path.name)

    return FormOutput(
        form_id=naming.form_id,
        filename=naming.filename,
        source_path=str(source_pdf),
        output_path=str(out_path),
        field_count=len(written_fields),
        fields=written_fields,
    )


def write_all_forms(
    form_ids: list[str] | None = None, force: bool = False
) -> list[str]:
    """Write AcroForm fields for all named forms.

    Args:
        form_ids: If provided, only write these form IDs.

    Returns:
        List of output PDF file paths.
    """
    from modules.taxonomy import load_naming
    from download import list_downloaded_forms

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_paths = []

    # Build form_id → pdf_path map
    forms = list_downloaded_forms()
    pdf_map = {f["form_id"]: f["path"] for f in forms}

    if form_ids is None:
        naming_files = sorted(config.NAMING_DIR.glob("*.json"))
        form_ids = [f.stem for f in naming_files]

    for form_id in form_ids:
        # Try sample override first
        sample_naming = _load_sample_naming(form_id)
        if sample_naming is not None:
            # Get category from regular naming data
            regular_naming = load_naming(form_id)
            if regular_naming is not None:
                sample_naming.category = regular_naming.category
            naming = sample_naming
            logger.info("Using sample override for %s", form_id)
        else:
            naming = load_naming(form_id)
            if naming is None:
                logger.warning("No naming found for %s, skipping", form_id)
                continue

        pdf_path = pdf_map.get(form_id)
        if not pdf_path:
            logger.warning("No PDF found for %s, skipping", form_id)
            continue

        # Check if output already exists
        out_dir = config.OUTPUT_DIR / naming.category
        stem = Path(pdf_path).stem
        out_path = out_dir / f"{stem}_fillable.pdf"
        if out_path.exists() and not force:
            logger.debug("Skipping write (exists): %s", form_id)
            output_paths.append(str(out_path))
            continue

        logger.info("Writing AcroForm fields: %s", form_id)
        result = write_form(naming, pdf_path)
        output_paths.append(result.output_path)
        logger.info(
            "  → %d fields written to %s", result.field_count, result.output_path
        )

    return output_paths
