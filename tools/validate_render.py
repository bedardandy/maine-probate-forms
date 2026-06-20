#!/usr/bin/env python3
"""Render a form with a fixed descender-bearing value in every field, for
field-by-field validation of the baseline normalization.

Unlike saturate_render (which packs each box, shrinking the font), this fills a
short fixed token at the field's *nominal* font size -- the worst case for
descender clearance -- so a reviewer (human or agent) sees exactly how the
lowest ink sits on each printed rule. Also dumps the objective clearance table.

    python3 tools/validate_render.py --form DE-201 --out-dir /tmp/validate
"""
from __future__ import annotations
import argparse, json, pathlib, sys
import fitz
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa
from fill_pdf import (_ALIGN_CONST, _add_checkbox, _add_text, _load_alignment,  # noqa
                      _strip_widgets, _value_for_printed_context)
from measure_baseline import measure  # noqa

VAL = "Gjpy 1980"   # short, nominal font size, carries g/j/p/y descenders


def render(form_id, out_dir, dpi=170):
    pkg = ROOT / "repo" / "forms" / form_id
    geom = json.loads((pkg / "fill_geometry.json").read_text())
    schema = {f["field_id"]: f for f in
              json.loads((pkg / "schema.json").read_text())["fields"]}
    doc = fitz.open(str(fetch_source(form_id)))
    _strip_widgets(doc)
    align = _load_alignment(form_id, ROOT)
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
            val = _value_for_printed_context(doc[w["page"]], w["rect"], fid, VAL)
            _add_text(doc[w["page"]], w["rect"], name, val,
                      _ALIGN_CONST.get(align.get(fid)), force_multiline=ml)
        for i, o in enumerate(spec.get("options") or []):
            _add_checkbox(doc[o["page"]], o["rect"], f"{fid}__{o.get('value') or i}")
    out = pathlib.Path(out_dir) / form_id.replace("/", "_")
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, page in enumerate(doc):
        p = out / f"p{i+1}.png"
        page.get_pixmap(dpi=dpi).save(str(p)); paths.append(str(p))
    doc.close()
    # objective clearance table (does not modify geometry)
    rows = measure(form_id, apply=False, target=0.6)
    tbl = out / "clearance.txt"
    with open(tbl, "w") as fh:
        fh.write("field\tpage\tfs\tdesc_clear\n")
        for r in sorted(rows, key=lambda r: r["desc_clear"]):
            fh.write(f"{r['field']}\t{r['page']}\t{r['fs']}\t{r['desc_clear']}\n")
    return paths, str(tbl)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--out-dir", default="/tmp/validate")
    ap.add_argument("--dpi", type=int, default=170)
    a = ap.parse_args()
    paths, tbl = render(a.form, a.out_dir, a.dpi)
    print("\n".join(paths)); print(tbl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
