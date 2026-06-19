#!/usr/bin/env python3
"""Visual geometry probe: overlay every fill_geometry rect on the official PDF.

Maintainer aid for visual geometry review. For each widget rect in a form's
fill_geometry.json we draw a translucent outline and a sample value seated the
same way fill_pdf.py seats text, then rasterise each page to PNG. Reviewers (or
agents) can eyeball whether text rects collide with printed labels/rules, run
off the page, or sit on the wrong line — without needing a hand-authored case.

    python3 tools/stress_render.py --form DE-101 --out-dir /tmp/probe
    python3 tools/stress_render.py --form AD-011 --source codex.pdf --dpi 130

Not part of the runtime fill path; it never writes AcroForm widgets.
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


def _sample(field_id: str, rect, ftype: str) -> str:
    w = rect[2] - rect[0]
    if ftype in ("choice", "checkbox") or (rect[3] - rect[1]) <= 14 and w <= 16:
        return "X"
    if ftype == "date" or field_id.endswith("_date"):
        return "09/09/2025"
    n = max(3, int(w / 5.0))
    return (field_id.replace("_", " ").title() + " " + "Mg" * 40)[:n]


def render(form_id: str, source: pathlib.Path | None, out_dir: pathlib.Path,
           dpi: int) -> list[pathlib.Path]:
    pkg = ROOT / "repo" / "forms" / form_id
    geom = json.loads((pkg / "fill_geometry.json").read_text())
    src = source or fetch_source(form_id)
    doc = fitz.open(str(src))
    for page in doc:  # flatten any existing widgets so only our overlays show
        for wdg in list(page.widgets() or []):
            page.delete_widget(wdg)
    for fid, spec in geom.get("fields", {}).items():
        ftype = spec.get("type", "text")
        for wdg in spec.get("widgets", []) or []:
            r = fitz.Rect(wdg["rect"])
            page = doc[wdg["page"]]
            suppressed = spec.get("geometry_source") == "suppressed"
            color = (1, 0, 0) if suppressed else (0, 0.35, 0.9)
            page.draw_rect(r, color=color, width=0.6)
            if not suppressed:
                page.insert_textbox(r, _sample(fid, wdg["rect"], ftype),
                                    fontsize=8, color=(0.85, 0, 0),
                                    fontname="helv", align=0)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, page in enumerate(doc):
        out = out_dir / f"{form_id.replace('/', '_')}_p{i + 1}.png"
        page.get_pixmap(dpi=dpi).save(str(out))
        paths.append(out)
    doc.close()
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--source")
    ap.add_argument("--out-dir", default="/tmp/probe")
    ap.add_argument("--dpi", type=int, default=120)
    args = ap.parse_args()
    src = pathlib.Path(args.source) if args.source else None
    paths = render(args.form, src, pathlib.Path(args.out_dir), args.dpi)
    print("\n".join(str(p) for p in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
