#!/usr/bin/env python3
"""Apply a fill plan to a flat probate PDF -> a real filled PDF.

Combines `tools/fill_plan.py` (field_id -> value) with the per-form
`fill_geometry.json` (field_id -> widget rects) to inject AcroForm widgets named
by field_id onto the fetched flat source and write the resolved values. This is
the probate analog of the court repo's `fill_form` PDF output.

    python3 tools/fill_pdf.py --form DE-101 --case case.json --out /tmp/DE-101.filled.pdf
    python3 tools/fill_pdf.py --form DE-101 --case case.json \
        --source "DE-101 (flat from source_url).pdf" --out /tmp/DE-101.filled.pdf

Flat PDFs are not shipped; with no --source the official PDF is fetched from
metadata.json.source_url (cached, manifest-verified — see tools/fetch.py). Text
fields and checkbox/radio options the plan resolved are written; narrative
fields the agent composed (placed under narrative_facts[field_id]) fold into the
resolved text. Not legal advice — verify against the official form.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

import fitz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from canonical_adapter import to_case_object       # noqa: E402
from fill_plan import build_plan                     # noqa: E402
import verify                                         # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _strip_widgets(doc: fitz.Document) -> None:
    for page in doc:
        for w in list(page.widgets() or []):
            page.delete_widget(w)


# --- Consistent text rendering (A: font size, B: justification) ---------------
# A target size used for ALL text fields, decoupled from rect height so filled
# text is visually uniform across a form. We only shrink to fit (never grow),
# and only cap for unusually short boxes.
TARGET_FONTSIZE = 10.0
MIN_FONTSIZE = 6.0
_PAD = 2.0                 # horizontal padding assumed inside the widget, per side
_MULTILINE_MIN_H = 24.0    # a box taller than this is treated as a paragraph area

# Field-name tokens that denote money -> right-justify (digits read better flush
# right and line up in value columns). Word-boundary anchored to avoid matching
# substrings like "valid" or "evaluate".
_CURRENCY_RE = re.compile(
    r"(?:^|_)(?:value|val|amount|amt|fee|fees|penal_sum|penal|balance|income|"
    r"expense|expenses|salary|wage|wages|disbursement|disbursements|sum_numeric|"
    r"gross_value|net_value|estimated_maine_estate_tax)(?:$|_)", re.I)


_ALIGN_CONST = {"left": fitz.TEXT_ALIGN_LEFT, "center": fitz.TEXT_ALIGN_CENTER,
                "right": fitz.TEXT_ALIGN_RIGHT}


def _text_align(name: str) -> int:
    """Fallback name heuristic, used only when the declared map has no entry."""
    n = name.lower()
    if "caption" in n:
        return fitz.TEXT_ALIGN_CENTER
    if _CURRENCY_RE.search(n):
        return fitz.TEXT_ALIGN_RIGHT
    return fitz.TEXT_ALIGN_LEFT


def _load_alignment(form_id: str, root: pathlib.Path) -> dict[str, str]:
    """Declared per-field justification from catalog/field_alignment.json.

    Authoritative (derived from the schema data_type by author_field_align.py).
    Returns {field_id: 'center'|'right'}; absent fields default to left.
    """
    p = root / "catalog" / "field_alignment.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("forms", {}).get(form_id, {})
    except Exception:
        return {}


def _fontsize_for(value: str, r: fitz.Rect, multiline: bool) -> float:
    # Start at the target, capped only so a very short box can't clip vertically.
    fs = min(TARGET_FONTSIZE, max(MIN_FONTSIZE, r.height - 2))
    if multiline:
        return round(fs, 1)                      # let long text wrap; keep size
    avail = max(1.0, r.width - 2 * _PAD)
    try:
        text_w = fitz.get_text_length(value, fontname="helv", fontsize=fs)
    except Exception:
        text_w = len(value) * fs * 0.5
    if text_w > avail:                           # single line overflow -> shrink to fit
        fs = max(MIN_FONTSIZE, fs * avail / text_w)
    return round(fs, 1)


def _add_text(page: fitz.Page, rect, name: str, value: str,
              align: int | None = None) -> None:
    w = fitz.Widget()
    w.field_name = name
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    r = fitz.Rect(rect)
    w.rect = r
    sval = str(value)
    w.field_value = sval
    multiline = r.height > _MULTILINE_MIN_H
    if multiline:
        try:
            w.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
        except Exception:
            w.field_flags = 1 << 12              # multiline flag bit
    # A: uniform target size, decoupled from box height; shrink only to fit width.
    w.text_fontsize = _fontsize_for(sval, r, multiline)
    annot = page.add_widget(w)
    # B: type-aware justification via /Q (1=center, 2=right). PyMuPDF bakes a
    # left-aligned appearance and omits /Q, so set it low-level; fill_pdf() flags
    # NeedAppearances so conforming viewers re-render aligned (verified: both
    # poppler and PyMuPDF's own renderer honor it). `align` comes from the
    # declared map; falls back to the name heuristic when not supplied.
    if align is None:
        align = _text_align(name)
    if align != fitz.TEXT_ALIGN_LEFT and annot is not None:
        try:
            page.parent.xref_set_key(annot.xref, "Q", str(int(align)))
        except Exception:
            pass


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
             root: str | pathlib.Path = ROOT,
             verify_mode: str | None = None) -> dict:
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

    # Guard: the source PDF must be the revision this form's geometry was
    # measured against (catalog/pdf_manifest.json). Otherwise the coordinates
    # can land text in the wrong place. Mismatch warns by default; set
    # MCF_VERIFY_BLANK=strict to refuse, =off to skip. `verify_mode` overrides
    # the env (e.g. the enhance pipeline verifies once at fetch time and fills
    # step-rewritten intermediates that can never match the manifest).
    mode = verify_mode or os.environ.get("MCF_VERIFY_BLANK", "warn")
    source_verified, verify_detail = verify.guard_pdf_detail(
        form_id, source_pdf, mode=mode)

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
    align_map = _load_alignment(form_id, root)
    written_text = checked = 0
    skipped_no_geom = []
    for fid, val in resolved.items():
        spec = geom.get(fid)
        if not spec:
            skipped_no_geom.append(fid); continue
        if spec.get("widgets"):                       # text field(s)
            align = _ALIGN_CONST.get(align_map.get(fid))   # None -> name heuristic
            for i, wdg in enumerate(spec["widgets"]):
                # full value in the first widget; extra widgets are continuation
                _add_text(doc[wdg["page"]], wdg["rect"],
                          fid if i == 0 else f"{fid}__{i}",
                          val if i == 0 else "", align=align)
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

    # B: flag NeedAppearances so viewers regenerate field appearances honoring
    # the /Q justification set per-field above (PyMuPDF's baked appearance is
    # left-aligned). Viewers that don't regenerate fall back to left — same as
    # before, so this is safe.
    if written_text:
        try:
            doc.need_appearances(True)
        except Exception:
            pass

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return {
        "ok": True, "form_id": form_id, "out": str(out_path),
        "text_written": written_text, "options_checked": checked,
        "source_verified": source_verified,
        "source_verify_detail": verify_detail,
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
    ap.add_argument("--source", help="flat source PDF (from source_url); omit to "
                    "auto-fetch from metadata.json.source_url")
    ap.add_argument("--fetch", action="store_true",
                    help="re-download the flat source from source_url (bypass "
                    "the cache); implied when --source is omitted")
    ap.add_argument("--out", required=True)
    ap.add_argument("--geometry", help="override fill_geometry.json path")
    a = ap.parse_args()
    case = to_case_object(json.loads(pathlib.Path(a.case).read_text()))
    source = a.source
    if not source:
        from fetch import fetch_source            # manifest-verified fetch+cache
        try:
            source = str(fetch_source(a.form, fresh=a.fetch))
        except Exception as e:
            print(json.dumps({"ok": False,
                              "error": f"could not fetch source PDF: {e}"},
                             indent=2))
            return 1
    res = fill_pdf(a.form, case, source, a.out, a.geometry)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
