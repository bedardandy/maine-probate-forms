"""Run the VLM validator on candidates synthesized from an existing PDF's widgets.

The recursive pipeline produced a fillable PDF with 148 widgets (66 of them
CheckBoxes for the appointment-type triple, the section-letter A/B/C, the
GAL qualification list, etc). The current intermediate/detection/PB-007.json
was rerun with stricter settings and only kept text/date/currency, so the
standard validate_form path never sees the checkbox candidates.

This script reads widgets from a fused PDF, builds a synthetic
DetectedField list, and runs validate_form on it. The VLM gets full
candidate context (every widget) and can emit group_id / group_role /
parent_group_id annotations that promote_to_radio_group can then drive.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

config.VLM_API_BASE = os.environ.setdefault("VLM_API_BASE", "http://localhost:8088/v1")
config.VLM_MODEL = "Qwen3.6-27B-FP8"
os.environ.setdefault("VLM_MODE", "naming")

from modules import vlm_validator  # noqa: E402
from modules.schema import (  # noqa: E402
    DetectedField,
    FieldType,
    FormDetection,
    Rect,
)

WIDGET_TYPE_TO_FIELD = {
    "Text": FieldType.TEXT,
    "CheckBox": FieldType.CHECKBOX,
    "RadioButton": FieldType.RADIO,
    "Signature": FieldType.SIGNATURE,
}


def widgets_to_detection(pdf_path: pathlib.Path, form_id: str,
                         category: str, filename: str) -> FormDetection:
    """Read every widget on every page; return a DetectedField list."""
    doc = fitz.open(pdf_path)
    fields: list[DetectedField] = []
    for pno in range(doc.page_count):
        page = doc[pno]
        for w in (page.widgets() or []):
            ftype = WIDGET_TYPE_TO_FIELD.get(w.field_type_string, FieldType.TEXT)
            r = w.rect
            fields.append(DetectedField(
                page=pno,
                rect=Rect(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                field_type=ftype,
                nearby_label=w.field_name or "",
                section_header="",
                confidence=0.7,
                detection_source="from_widgets",
            ))
    doc.close()
    return FormDetection(form_id=form_id, filename=filename,
                         category=category, fields=fields)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path,
                    help="Fused/fillable PDF whose widgets become candidates.")
    ap.add_argument("--form-id", default="PB-007")
    ap.add_argument("--category", default="guardian_minor")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr)
        return 2

    detection = widgets_to_detection(
        args.pdf, args.form_id, args.category, args.pdf.name,
    )
    print(f"synthesized {len(detection.fields)} candidates from {args.pdf.name}")

    print(f"validating via {config.VLM_API_BASE} model={config.VLM_MODEL}")
    result = vlm_validator.validate_form(detection, str(args.pdf))
    args.out.write_text(result.model_dump_json(indent=2))
    print(f"\nwrote {args.out}")
    print(result.review_summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
