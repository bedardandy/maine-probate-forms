#!/usr/bin/env python3
"""Build saturated per-form PDFs and one packet for geometry review."""
from __future__ import annotations

import json
import pathlib
import sys

import fitz
from pypdf import PdfReader, PdfWriter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402
from fill_pdf import (  # noqa: E402
    _ALIGN_CONST,
    _add_checkbox,
    _add_text,
    _load_alignment,
    _strip_widgets,
    _value_for_printed_context,
)

STRESS = "MWXgjpqy 0123456789 ABCdef ()[] /-$., "


def _value(field_id: str, rect: list[float]) -> str:
    chars = max(12, int((rect[2] - rect[0]) / 4.5))
    if rect[3] - rect[1] > 24:
        chars *= max(2, int((rect[3] - rect[1]) / 12))
    seed = f"{field_id}: {STRESS}"
    return (seed * (chars // len(seed) + 2))[:chars]


def _table_value(field_id: str, rect: list[float], field_type: str) -> str:
    if field_type == "date" or field_id.endswith("_date"):
        return "09/09/2025"
    chars = max(18, int((rect[2] - rect[0]) / 5.5) * 2)
    seed = f"{field_id}: {STRESS}"
    return (seed * (chars // len(seed) + 2))[:chars]


def build_form(form_id: str, out_dir: pathlib.Path) -> dict:
    package = ROOT / "repo" / "forms" / form_id
    geometry = json.loads((package / "fill_geometry.json").read_text(encoding="utf-8"))
    schema = json.loads((package / "schema.json").read_text(encoding="utf-8"))
    contracts = {f["field_id"]: f for f in schema.get("fields", [])}
    doc = fitz.open(str(fetch_source(form_id)))
    _strip_widgets(doc)
    alignments = _load_alignment(form_id, ROOT)
    text_count = choice_count = 0

    for field_id, spec in geometry["fields"].items():
        contract = contracts.get(field_id, {})
        wet_ink = (
            contract.get("category") == "signature"
            or contract.get("fill_strategy", {}).get("source") == "wet_ink"
        )
        non_user_fillable = (
            contract.get("court_only") is True
            or contract.get("suppress_geometry") is True
            or contract.get("fill_strategy", {}).get("source") == "left_blank"
        )
        if wet_ink or non_user_fillable:
            continue
        for index, widget in enumerate(spec.get("widgets") or []):
            if spec.get("type") == "enabler":
                _add_checkbox(doc[widget["page"]], widget["rect"],
                              field_id if index == 0 else f"{field_id}__{index}")
                choice_count += 1
                continue
            name = field_id if index == 0 else f"{field_id}__{index}"
            value = (_table_value(field_id, widget["rect"], spec.get("type", ""))
                     if widget.get("border") else _value(field_id, widget["rect"]))
            value = _value_for_printed_context(
                doc[widget["page"]], widget["rect"], field_id, value
            )
            _add_text(doc[widget["page"]], widget["rect"], name, value,
                      _ALIGN_CONST.get(alignments.get(field_id)),
                      border=bool(widget.get("border")),
                      force_multiline=bool(widget.get("multiline")))
            text_count += 1
        for index, option in enumerate(spec.get("options") or []):
            _add_checkbox(doc[option["page"]], option["rect"],
                          f"{field_id}__{option.get('value') or index}")
            choice_count += 1

    out = out_dir / f"{form_id}.alignment-stress.pdf"
    doc.save(str(out), garbage=4, deflate=True)
    result = {"form_id": form_id, "file": str(out), "pages": doc.page_count,
              "text_widgets": text_count, "choice_widgets": choice_count}
    doc.close()
    return result


def main() -> int:
    out_root = ROOT / "output" / "pdf"
    forms_out = out_root / "alignment_stress_forms"
    forms_out.mkdir(parents=True, exist_ok=True)
    form_ids = sorted(p.parent.name for p in
                      (ROOT / "repo" / "forms").glob("*/fill_geometry.json"))
    results, failures = [], []
    for form_id in form_ids:
        try:
            results.append(build_form(form_id, forms_out))
            print(form_id)
        except Exception as exc:
            failures.append({"form_id": form_id, "error": str(exc)})

    packet = out_root / "maine_probate_forms_alignment_stress_packet.pdf"
    writer = PdfWriter()
    for item in results:
        for page in PdfReader(item["file"]).pages:
            writer.add_page(page)
    with packet.open("wb") as stream:
        writer.write(stream)
    report = {
        "packet": str(packet), "forms_requested": len(form_ids),
        "forms_built": len(results), "total_pages": sum(x["pages"] for x in results),
        "text_widgets": sum(x["text_widgets"] for x in results),
        "choice_widgets": sum(x["choice_widgets"] for x in results),
        "failures": failures, "forms": results,
    }
    (out_root / "alignment_stress_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
