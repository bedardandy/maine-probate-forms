"""Visual debug overlay: render PDF pages with detected fields highlighted."""

import logging
from pathlib import Path

import fitz  # PyMuPDF

import config
from modules.schema import FieldType, FormNaming, NamedField

logger = logging.getLogger(__name__)

# Color map: field_type → (R, G, B) with alpha
FIELD_COLORS = {
    FieldType.TEXT: (0, 0.4, 1),  # blue
    FieldType.CHECKBOX: (0, 0.8, 0),  # green
    FieldType.RADIO: (0.8, 0, 0.8),  # purple
    FieldType.SIGNATURE: (1, 0, 0),  # red
    FieldType.DATE: (1, 0.5, 0),  # orange
    FieldType.CURRENCY: (0, 0.7, 0.3),  # teal
}

FILL_ALPHA = 0.15
BORDER_WIDTH = 1.0
LABEL_FONT_SIZE = 6.0


def render_preview(
    naming: FormNaming,
    source_pdf: str | Path,
    output_path: str | Path | None = None,
    pages: list[int] | None = None,
) -> str:
    """Render a PDF with colored overlays showing detected fields.

    Args:
        naming: Named field data for the form.
        source_pdf: Path to the original PDF.
        output_path: Where to save. Defaults to output/{form_id}_preview.pdf.
        pages: If provided, only render these page numbers (0-indexed).

    Returns:
        Path to the preview PDF.
    """
    source_pdf = Path(source_pdf)
    if output_path is None:
        out_dir = config.OUTPUT_DIR / "previews"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{naming.form_id}_preview.pdf"
    output_path = Path(output_path)

    doc = fitz.open(str(source_pdf))

    # Group fields by page
    fields_by_page: dict[int, list[NamedField]] = {}
    for field in naming.fields:
        fields_by_page.setdefault(field.page, []).append(field)

    for page_num in range(len(doc)):
        if pages is not None and page_num not in pages:
            continue

        page = doc[page_num]
        page_fields = fields_by_page.get(page_num, [])

        for field in page_fields:
            color = FIELD_COLORS.get(field.field_type, (0.5, 0.5, 0.5))
            rect = fitz.Rect(field.rect.x0, field.rect.y0, field.rect.x1, field.rect.y1)

            # Draw filled rectangle with transparency
            shape = page.new_shape()
            shape.draw_rect(rect)
            shape.finish(
                color=color,
                fill=color,
                fill_opacity=FILL_ALPHA,
                width=BORDER_WIDTH,
            )
            shape.commit()

            # Add field name as small label above the rect
            label_text = field.field_name
            if len(label_text) > 30:
                label_text = label_text[:27] + "..."

            label_point = fitz.Point(rect.x0, rect.y0 - 1)
            try:
                page.insert_text(
                    label_point,
                    label_text,
                    fontsize=LABEL_FONT_SIZE,
                    color=color,
                )
            except Exception:
                pass  # skip if text insertion fails (e.g., off-page)

    # Add a legend on the first page
    if len(doc) > 0:
        page = doc[0]
        legend_x = page.rect.width - 140
        legend_y = 15
        for ft, color in FIELD_COLORS.items():
            rect = fitz.Rect(legend_x, legend_y, legend_x + 10, legend_y + 8)
            shape = page.new_shape()
            shape.draw_rect(rect)
            shape.finish(color=color, fill=color, fill_opacity=0.5, width=0.5)
            shape.commit()
            page.insert_text(
                fitz.Point(legend_x + 14, legend_y + 7),
                ft.value,
                fontsize=7,
                color=(0, 0, 0),
            )
            legend_y += 12

    field_count = len(naming.fields)
    num_pages = len(doc)
    doc.save(str(output_path))
    doc.close()

    logger.info(
        "Preview: %d fields on %d pages → %s", field_count, num_pages, output_path.name
    )
    return str(output_path)


def render_all_previews(
    form_ids: list[str] | None = None,
    force: bool = False,
) -> list[str]:
    """Render previews for all named forms.

    Args:
        form_ids: If provided, only render these form IDs.
        force: Overwrite existing previews.

    Returns:
        List of output preview PDF paths.
    """
    from modules.taxonomy import load_naming
    from download import list_downloaded_forms

    out_dir = config.OUTPUT_DIR / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_paths = []

    forms = list_downloaded_forms()
    pdf_map = {f["form_id"]: f["path"] for f in forms}

    if form_ids is None:
        naming_files = sorted(config.NAMING_DIR.glob("*.json"))
        form_ids = [f.stem for f in naming_files]

    for form_id in form_ids:
        naming = load_naming(form_id)
        if naming is None:
            continue

        pdf_path = pdf_map.get(form_id)
        if not pdf_path:
            continue

        preview_path = out_dir / f"{form_id}_preview.pdf"
        if preview_path.exists() and not force:
            output_paths.append(str(preview_path))
            continue

        logger.info("Rendering preview: %s", form_id)
        result = render_preview(naming, pdf_path, preview_path)
        output_paths.append(result)

    return output_paths
