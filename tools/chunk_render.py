#!/usr/bin/env python3
"""High-resolution, overlapping chunked render of a form for granular inspection.

Full-page renders at ~150dpi hide 2-5pt alignment nudges. This renders each page
at high dpi and slices it into overlapping vertical tiles so small text and
baselines are crisp enough to judge alignment. Optionally renders an alternate
geometry (e.g. origin/main, or pre-snap) for before/after comparison.

    python3 tools/chunk_render.py --form DE-104 --out-dir /tmp/chunks
    python3 tools/chunk_render.py --form DE-104 --ref origin/main --out-dir /tmp/chunks
"""
from __future__ import annotations
import argparse, json, pathlib, subprocess, sys
import fitz
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa
from fill_pdf import (_ALIGN_CONST, _add_checkbox, _add_text, _load_alignment,  # noqa
                      _strip_widgets, _value_for_printed_context)
from saturate_render import _value  # noqa


def _geom(form, ref):
    if ref:
        txt = subprocess.run(["git", "show", f"{ref}:repo/forms/{form}/fill_geometry.json"],
                             capture_output=True, text=True).stdout
        return json.loads(txt) if txt.strip() else {"fields": {}}
    return json.loads((ROOT / "repo" / "forms" / form / "fill_geometry.json").read_text())


def _fill(form, geom):
    schema = {f["field_id"]: f for f in json.loads(
        (ROOT / "repo" / "forms" / form / "schema.json").read_text())["fields"]}
    doc = fitz.open(str(fetch_source(form))); _strip_widgets(doc)
    align = _load_alignment(form, ROOT)
    for fid, spec in geom.get("fields", {}).items():
        c = schema.get(fid, {})
        if (c.get("category") == "signature"
                or c.get("fill_strategy", {}).get("source") in ("wet_ink", "left_blank")
                or spec.get("geometry_source", "").startswith(("suppressed", "court"))):
            continue
        for i, w in enumerate(spec.get("widgets") or []):
            name = fid if i == 0 else f"{fid}__{i}"
            if spec.get("type") == "enabler":
                _add_checkbox(doc[w["page"]], w["rect"], name); continue
            ml = bool(w.get("multiline"))
            val = _value_for_printed_context(doc[w["page"]], w["rect"], fid, _value(fid, w["rect"], ml))
            _add_text(doc[w["page"]], w["rect"], name, val, _ALIGN_CONST.get(align.get(fid)),
                      force_multiline=ml)
        for i, o in enumerate(spec.get("options") or []):
            _add_checkbox(doc[o["page"]], o["rect"], f"{fid}__{o.get('value') or i}")
    return doc


def render(form, out_dir, ref=None, dpi=210, tile_h=900, overlap=140, tag=""):
    doc = _fill(form, _geom(form, ref))
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for pno, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        H = pix.height
        y = 0; t = 0
        while y < H:
            y1 = min(y + tile_h, H)
            clip = fitz.IRect(0, y, pix.width, y1)
            tile = fitz.Pixmap(pix, pix, clip) if False else None
            # crop via Pixmap on a clipped pixmap of the page
            sub = page.get_pixmap(dpi=dpi, clip=fitz.Rect(0, y * 72 / dpi, page.rect.width, y1 * 72 / dpi))
            sfx = f"{tag}_" if tag else ""
            p = out_dir / f"{form.replace('/', '_')}_p{pno+1}_{sfx}t{t+1}.png"
            sub.save(str(p)); paths.append(str(p))
            if y1 >= H:
                break
            y = y1 - overlap; t += 1
    doc.close()
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--ref", help="git ref for before-comparison geometry")
    ap.add_argument("--out-dir", default="/tmp/chunks")
    ap.add_argument("--dpi", type=int, default=210)
    a = ap.parse_args()
    if a.ref:
        for p in render(a.form, a.out_dir, a.ref, a.dpi, tag="before"):
            print(p)
    for p in render(a.form, a.out_dir, None, a.dpi, tag="after" if a.ref else ""):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
