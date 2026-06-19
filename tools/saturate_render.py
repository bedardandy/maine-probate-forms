#!/usr/bin/env python3
"""Saturated geometry probe: fill every fillable widget to capacity and render.

Unlike tools/stress_render.py (which draws an outline + a short sample), this
seats text with the *production* path (fill_pdf._add_text: same fontsize,
justification, multiline and baseline logic the real fill uses) and packs each
box with as many characters as it holds. That surfaces two failure modes a
short sample hides:

  * horizontal overflow — saturated text spilling past the box into a neighbour
    widget or printed label/rule;
  * vertical misalignment — text sitting above/below the printed line it should
    sit on (visible once the box is full and the baseline is unambiguous).

Wet-ink signature fields and court-only / suppressed fields are left empty, like
the real fill. Output: per-page PNGs and an optional merged PDF, rasterised with
fitz so the widget appearance streams (true seating) are what you see.

    python3 tools/saturate_render.py --form AF-105 --out-dir /tmp/sat --dpi 150
    python3 tools/saturate_render.py --all --pdf /tmp/saturated_packet.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402
from fill_pdf import (  # noqa: E402
    _ALIGN_CONST, _add_checkbox, _add_text, _load_alignment, _strip_widgets,
    _value_for_printed_context,
)

STRESS = "MWXgjpqy0123456789ABCdefghk()[]/-$., "


def _value(field_id: str, rect, multiline: bool) -> str:
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    chars = max(8, int(w / 4.3))          # ~saturate a 10pt line across the box
    if multiline or h > 24:
        chars *= max(2, int(h / 11))
    seed = f"{field_id} {STRESS}"
    return (seed * (chars // len(seed) + 2))[:chars]


def saturate(form_id: str):
    pkg = ROOT / "repo" / "forms" / form_id
    geom = json.loads((pkg / "fill_geometry.json").read_text(encoding="utf-8"))
    schema = json.loads((pkg / "schema.json").read_text(encoding="utf-8"))
    contracts = {f["field_id"]: f for f in schema.get("fields", [])}
    doc = fitz.open(str(fetch_source(form_id)))
    _strip_widgets(doc)
    align = _load_alignment(form_id, ROOT)
    for fid, spec in geom.get("fields", {}).items():
        c = contracts.get(fid, {})
        if (c.get("category") == "signature"
                or c.get("fill_strategy", {}).get("source") in ("wet_ink", "left_blank")
                or c.get("court_only") is True
                or spec.get("geometry_source") == "suppressed"):
            continue
        for i, w in enumerate(spec.get("widgets") or []):
            name = fid if i == 0 else f"{fid}__{i}"
            if spec.get("type") == "enabler":
                _add_checkbox(doc[w["page"]], w["rect"], name)
                continue
            ml = bool(w.get("multiline"))
            val = _value_for_printed_context(
                doc[w["page"]], w["rect"], fid, _value(fid, w["rect"], ml))
            _add_text(doc[w["page"]], w["rect"], name, val,
                      _ALIGN_CONST.get(align.get(fid)),
                      border=bool(w.get("border")), force_multiline=ml)
        for i, opt in enumerate(spec.get("options") or []):
            _add_checkbox(doc[opt["page"]], opt["rect"],
                          f"{fid}__{opt.get('value') or i}")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out-dir")
    ap.add_argument("--pdf")
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args()

    if args.all:
        forms = sorted(p.parent.name for p in
                       (ROOT / "repo" / "forms").glob("*/fill_geometry.json"))
    else:
        forms = [args.form]

    master = fitz.open() if args.pdf else None
    for form_id in forms:
        try:
            doc = saturate(form_id)
        except Exception as exc:
            print(f"SKIP {form_id}: {exc}")
            continue
        for page in doc:
            page.insert_textbox(fitz.Rect(0, 0, 260, 15), form_id,
                                fontsize=10, color=(0, 0.5, 0), fontname="hebo")
        if args.out_dir:
            out = pathlib.Path(args.out_dir) / form_id.replace("/", "_")
            out.mkdir(parents=True, exist_ok=True)
            for i, page in enumerate(doc):
                page.get_pixmap(dpi=args.dpi).save(str(out / f"{form_id.replace('/','_')}_p{i+1}.png"))
        if master is not None:
            master.insert_pdf(doc)
        doc.close()
        print(form_id)
    if master is not None:
        master.save(args.pdf, garbage=4, deflate=True)
        print(f"wrote {args.pdf} ({master.page_count} pages)")
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
