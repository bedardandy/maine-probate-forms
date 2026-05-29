"""Stage 5: Consistent field naming taxonomy across all forms."""

import logging
import re
from collections import Counter

import config
from modules.schema import (
    FieldType,
    FormNaming,
    NamedField,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# ── Standard name map for common cross-form labels ────────────────────────
#
# Snake_case values per the L2 standard documented in the federation
# (shared-contracts ARCHITECTURE.md, maine-forms-loop CLAUDE.md). PDF
# field names must be snake_case and cross-form consistent so downstream
# rubrics / data-fillers can address fields by stable identifiers.

STANDARD_NAMES: dict[str, str] = {
    "county": "county_probate_court",
    "county of": "county_probate_court",
    "county probate court": "county_probate_court",
    "docket no": "docket_no",
    "docket no.": "docket_no",
    "docket number": "docket_no",
    "docket": "docket_no",
    "case no": "case_no",
    "case no.": "case_no",
    "case number": "case_no",
    "decedent": "decedent_name",
    "estate of": "decedent_name",
    "date of death": "date_of_death",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "dod": "date_of_death",
    "ssn": "ssn",
    "social security": "ssn",
    "dated": "date_signed",
    "date": "date_signed",
    "signature": "signature",
}


# Post-pass canonical map applied after the label/standardize/clean steps.
# Catches cases where the VLM produced a snake_case name that bypassed
# `_standardize` (which only matches against raw labels).
CANONICAL_ALIASES: dict[str, str] = {
    "county": "county_probate_court",
    "county_name": "county_probate_court",
    "decedent": "decedent_name",
    "name_of_decedent": "decedent_name",
    "docket_number": "docket_no",
    "case_number": "case_no",
    "petitioner_signature": "signature",
    "signature_petitioner": "signature",
    "attorney_phone_number": "attorney_phone",
    "date_dated": "date_signed",
    "dated": "date_signed",
}


_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _is_snake_case(s: str) -> bool:
    return bool(_SNAKE_CASE_RE.match(s))


def _to_snake_case(label: str) -> str:
    """Convert any label string to snake_case.

    - Strip trailing punctuation/whitespace
    - Replace non-alphanumeric runs with single underscores
    - Lowercase
    - Collapse repeated underscores
    - Strip leading underscores / leading digits
    """
    s = label.strip()
    s = re.sub(r"[:.\s]+$", "", s).strip()
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    # PDF field names can't start with a digit safely
    if s and s[0].isdigit():
        s = "f_" + s
    return s


def _clean_label(label: str) -> str:
    """Convert a label to a snake_case PDF field name. If the input is
    already snake_case (e.g. produced by the VLM naming step), preserve it
    as-is."""
    s = label.strip().rstrip(":. ").strip()
    if _is_snake_case(s):
        return s
    return _to_snake_case(s)


def _standardize(label: str) -> str | None:
    """Check if a cleaned label maps to a standard name."""
    key = label.lower().strip().rstrip(":. ")
    return STANDARD_NAMES.get(key)


def name_fields(validation: ValidationResult) -> FormNaming:
    """Assign human-readable names to all fields in a validated form.

    Strategy:
    - Label-first: use the nearby_label directly (cleaned + title-cased)
    - Standard name map for common cross-form fields
    - Checkboxes: Box1, Box2, ... (sequential per form)
    - Signatures: Signature, Signature 2, ...
    - Empty-label text: Field1, Field2, ...
    - Deduplication with space + number: Address, Address 2, Address 3
    """
    named_fields: list[NamedField] = []
    name_counter: Counter[str] = Counter()
    checkbox_counter = 0
    field_counter = 0

    # Sort fields by page, then top-to-bottom, then left-to-right
    sorted_fields = sorted(
        validation.fields,
        key=lambda f: (f.page, f.rect.y0, f.rect.x0),
    )

    for field in sorted_fields:
        label = field.nearby_label.strip()

        # Field-type-specific patterns (all snake_case)
        if field.field_type == FieldType.CHECKBOX:
            # If the VLM gave a meaningful label, prefer it; else sequential.
            std = _standardize(label) if label else None
            if std:
                base_name = std
            elif label and _clean_label(label):
                base_name = _clean_label(label)
            else:
                checkbox_counter += 1
                base_name = f"checkbox_{checkbox_counter}"
        elif field.field_type == FieldType.SIGNATURE:
            std = _standardize(label) if label else None
            base_name = std or (_clean_label(label) if label else "") or "signature"
        elif not label:
            field_counter += 1
            base_name = f"field_{field_counter}"
        else:
            # Check standard name map first
            std = _standardize(label)
            if std:
                base_name = std
            else:
                base_name = _clean_label(label)

            # Fallback if cleaning produced empty string
            if not base_name:
                field_counter += 1
                base_name = f"field_{field_counter}"

        # Apply canonical alias map (catches VLM-produced snake_case that
        # bypassed _standardize).
        base_name = CANONICAL_ALIASES.get(base_name, base_name)

        # Disambiguate duplicates (skip for sequential placeholders).
        if re.match(r"^(checkbox|field)_\d+$", base_name):
            field_name = base_name
        else:
            name_counter[base_name] += 1
            count = name_counter[base_name]
            if count > 1:
                field_name = f"{base_name}_{count}"
            else:
                field_name = base_name

        named_fields.append(
            NamedField(
                page=field.page,
                rect=field.rect,
                field_type=field.field_type,
                field_name=field_name,
                nearby_label=label,
                confidence=field.confidence,
                group_id=field.group_id,
                group_role=field.group_role,
                group_option=field.group_option,
                parent_group_id=field.parent_group_id,
            )
        )

    return FormNaming(
        form_id=validation.form_id,
        filename=validation.filename,
        category=validation.category,
        fields=named_fields,
    )


def name_all_forms(form_ids: list[str] | None = None, force: bool = False) -> list[str]:
    """Run field naming on all validated forms.

    Args:
        form_ids: If provided, only name these form IDs.

    Returns:
        List of output JSON file paths.
    """
    from modules.vlm_validator import load_validation
    from modules.field_detector import load_detection

    config.NAMING_DIR.mkdir(parents=True, exist_ok=True)
    output_paths = []

    if form_ids is None:
        # Prefer validation output; fall back to detection
        val_files = sorted(config.VALIDATION_DIR.glob("*.json"))
        det_files = sorted(config.DETECTION_DIR.glob("*.json"))
        form_ids = list({f.stem for f in val_files} | {f.stem for f in det_files})
        form_ids.sort()

    for form_id in form_ids:
        out_path = config.NAMING_DIR / f"{form_id}.json"

        if out_path.exists() and not force:
            logger.debug("Skipping naming (exists): %s", form_id)
            output_paths.append(str(out_path))
            continue

        # Try validation first, fall back to detection
        validation = load_validation(form_id)
        if validation is None:
            detection = load_detection(form_id)
            if detection is None:
                logger.warning("No validation or detection found for %s", form_id)
                continue
            # Wrap detection as a minimal ValidationResult
            validation = ValidationResult(
                form_id=detection.form_id,
                filename=detection.filename,
                category=detection.category,
                fields=detection.fields,
            )

        logger.info("Naming fields: %s", form_id)
        naming = name_fields(validation)
        out_path.write_text(naming.model_dump_json(indent=2))
        output_paths.append(str(out_path))
        logger.info("  → %d named fields", len(naming.fields))

    return output_paths


def load_naming(form_id: str) -> FormNaming | None:
    """Load a previously saved naming result."""
    path = config.NAMING_DIR / f"{form_id}.json"
    if not path.exists():
        return None
    return FormNaming.model_validate_json(path.read_text())
