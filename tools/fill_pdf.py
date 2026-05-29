#!/usr/bin/env python3
"""Apply a fill plan to a flat probate PDF -> a real filled PDF.

Combines `tools/fill_plan.py` (field_id -> value) with the per-form
`fill_geometry.json` (field_id -> widget rects) to inject AcroForm widgets named
by field_id onto the fetched flat source and write the resolved values. This is
the probate analog of the court repo's `fill_form` PDF output.

    python3 tools/fill_pdf.py --form DE-101 --case case.json \
        --source "DE-101 (flat from source_url).pdf" --out /tmp/DE-101.filled.pdf

Flat PDFs are not shipped; fetch each form's metadata.json.source_url. Text
fields and checkbox/radio options the plan resolved are written; narrative
fields the agent composed (placed under narrative_facts[field_id]) fold into the
resolved text. Not legal advice — verify against the official form.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import fitz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from canonical_adapter import to_case_object       # noqa: E402
from fill_plan import build_plan                     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _strip_widgets(doc: fitz.Document) -> None:
    for page in doc:
        for w in list(page.widgets() or []):
            page.delete_widget(w)


def _add_text(page: fitz.Page, rect, name: str, value: str) -> None:
    w = fitz.Widget()
    w.field_name = name
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    r = fitz.Rect(rect)
    w.rect = r
    w.field_value = str(value)
    # Cap at 11pt: fontsize 0 ("auto") fills the rect height, so a short value in
    # a tall blank renders absurdly large. Shrink for short rects, never grow past
    # ordinary form text.
    w.text_fontsize = max(6.0, min(11.0, r.height - 3))
    page.add_widget(w)


def _add_checkbox(page: fitz.Page, rect, name: str) -> None:
    w = fitz.Widget()
    w.field_name = name
    w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    w.rect = fitz.Rect(rect)
    w.field_value = True
    page.add_widget(w)


def fill_pdf(form_id: str, case: dict, source_pdf: str | pathlib.Path,
             out_path: str | pathlib.Path,
             geometry_path: str | pathlib.Path | None = None,
             root: str | pathlib.Path = ROOT) -> dict:
    root = pathlib.Path(root)
    geometry_path = pathlib.Path(geometry_path) if geometry_path else (
        root / "repo" / "forms" / form_id / "fill_geometry.json")
    if not geometry_path.exists():
        return {"ok": False, "error": f"no fill_geometry.json for {form_id} "
                "(plan-only form — cannot write a PDF)"}
    geom = json.loads(geometry_path.read_text())["fields"]
    plan = build_plan(form_id, case, root=root)
    if not plan.get("ok"):
        return plan
    resolved = plan["resolved"]

    doc = fitz.open(str(source_pdf))
    # The source PDF must have every page the geometry references. If it doesn't,
    # the source is the wrong/outdated document (forms get re-paginated upstream);
    # fail with a diagnosable message instead of an opaque IndexError on doc[page].
    need = -1
    for spec in geom.values():
        for w in (spec.get("widgets") or []):
            if isinstance(w.get("page"), int):
                need = max(need, w["page"])
        for o in (spec.get("options") or []):
            if isinstance(o.get("page"), int):
                need = max(need, o["page"])
    if need >= doc.page_count:
        pc = doc.page_count
        doc.close()
        return {"ok": False, "error": f"source PDF has {pc} page(s) but "
                f"{form_id} geometry references page {need} — the source is "
                "likely outdated or the wrong document; re-fetch from "
                "metadata.json.source_url"}
    _strip_widgets(doc)
    written_text = checked = 0
    skipped_no_geom = []
    for fid, val in resolved.items():
        spec = geom.get(fid)
        if not spec:
            skipped_no_geom.append(fid); continue
        if spec.get("widgets"):                       # text field(s)
            for i, wdg in enumerate(spec["widgets"]):
                # full value in the first widget; extra widgets are continuation
                _add_text(doc[wdg["page"]], wdg["rect"],
                          fid if i == 0 else f"{fid}__{i}",
                          val if i == 0 else "")
                written_text += 1
        elif spec.get("options"):                     # choice field
            wants = {str(v).lower() for v in (
                val if isinstance(val, list) else [val])}
            single = len(spec["options"]) == 1
            for j, o in enumerate(spec["options"]):
                ov = str(o.get("value") or "").lower()
                hit = (ov in wants) or (single and str(val).lower() in
                                        ("true", "yes", "1", ov, "on"))
                if hit:
                    _add_checkbox(doc[o["page"]], o["rect"],
                                  f"{fid}__{o.get('value') or j}")
                    checked += 1

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return {
        "ok": True, "form_id": form_id, "out": str(out_path),
        "text_written": written_text, "options_checked": checked,
        "resolved_without_geometry": skipped_no_geom,
        "coverage": plan["coverage"],
        "narrative": [n["field_id"] for n in plan["narrative"]],
        "note": "Draft. Narrative fields not yet composed stay blank; place them "
                "under narrative_facts[field_id] and re-run. Verify before filing.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--source", required=True, help="flat source PDF (from source_url)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--geometry", help="override fill_geometry.json path")
    a = ap.parse_args()
    case = to_case_object(json.loads(pathlib.Path(a.case).read_text()))
    res = fill_pdf(a.form, case, a.source, a.out, a.geometry)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
