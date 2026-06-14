#!/usr/bin/env python3
"""Build the human-review poll: candidate rect fixes + rendered crops per unit.

For every unit on catalog/geometry_review_worklist.tsv, generate a few
plausible corrected rects from the source PDF's own layout, render each
(current + candidates) by filling that one widget with a realistic sample
value through the real fill appearance pipeline, and crop them all to a
shared window so they compare cleanly. Writes poll_data.json + crops the
poll server serves.

Candidate strategies (deduped, max 3):
  shift_label   x0 past printed text intruding from the left
  trim_right    x1 before printed text intruding from the right
  line_below    drop the rect onto the next underline beneath it

    python3 scripts/geometry_review/build_poll.py --out ~/geom-review-out
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools.fetch import fetch_source                       # noqa: E402
from tools.fill_pdf import _add_text, _ALIGN_CONST, _load_alignment  # noqa: E402
from scripts.geometry_review.sweep import page_features     # noqa: E402

DPI = 150
SCALE = DPI / 72.0
GAP = 3.0

SAMPLE_BY_TYPE = {
    "person_name": "Margaret L. Walsh",
    "date": "01/15/2026",
    "currency": "$12,345.00",
    "address": "82 Falmouth Foreside Way, Falmouth ME 04105",
    "docket_number": "2024-CV-00451",
    "phone": "(207) 555-0142",
    "email": "sample@example.com",
    "bar_number": "4271",
}


def sample_value(field: str, dtype: str) -> str:
    if dtype in SAMPLE_BY_TYPE:
        return SAMPLE_BY_TYPE[dtype]
    f = field.lower()
    if "county" in f:
        return "Androscoggin"
    if any(s in f for s in ("description", "detail", "specify", "explain",
                            "reason", "circumstanc", "address")):
        return "1998 Ford F-150 pickup, VIN 1FTZX1762WKA12345, value $8,500"
    if "name" in f:
        return "Margaret L. Walsh"
    if "date" in f or "day" in f:
        return "01/15/2026"
    return "Sample value text"


def schema_dtype(form: str, field: str) -> str:
    try:
        s = json.loads((ROOT / "repo" / "forms" / form / "schema.json").read_text())
        for fl in s["fields"]:
            if fl["field_id"] == field:
                return fl.get("data_type", "")
    except Exception:
        pass
    return ""


def candidates(rect: fitz.Rect, feats: dict) -> list[tuple[str, list]]:
    out = []
    R = fitz.Rect(rect)
    lw = [wr for wr, t in feats["words"]
          if (min(R.y1, wr.y1) - max(R.y0, wr.y0)) > 0.5 * wr.height
          and wr.x0 < R.x0 + 0.6 * R.width and wr.x1 > R.x0 - 1]
    if lw:
        nx0 = max(w.x1 for w in lw) + GAP
        if R.x1 - nx0 >= 20:
            out.append(("shift right, past the label",
                        [round(nx0, 1), round(R.y0, 1),
                         round(R.x1, 1), round(R.y1, 1)]))
    rw = [wr for wr, t in feats["words"]
          if (min(R.y1, wr.y1) - max(R.y0, wr.y0)) > 0.5 * wr.height
          and wr.x0 > R.x0 + 0.3 * R.width and wr.x0 < R.x1]
    if rw:
        nx1 = min(w.x0 for w in rw) - GAP
        if nx1 - R.x0 >= 20:
            out.append(("trim right edge, before the printed text",
                        [round(R.x0, 1), round(R.y0, 1),
                         round(nx1, 1), round(R.y1, 1)]))
    below = [y for x0, x1, y in feats["hlines"]
             if R.y1 < y <= R.y1 + 34
             and min(R.x1, x1) - max(R.x0, x0) > 0.3 * R.width]
    if below:
        shift = min(below) - 2 - R.y1
        out.append(("move down onto the next line",
                    [round(R.x0, 1), round(R.y0 + shift, 1),
                     round(R.x1, 1), round(R.y1 + shift, 1)]))
    seen, ded = set(), []
    for label, rc in out:
        if tuple(rc) in seen:
            continue
        seen.add(tuple(rc))
        ded.append((label, rc))
    return ded[:3]


def render_option(src: pathlib.Path, page: int, rect: list, value: str,
                  align, window: fitz.Rect, out_png: pathlib.Path,
                  color: tuple) -> None:
    """Fill one widget with `value` at `rect`, render the page, crop `window`,
    draw the rect outline."""
    doc = fitz.open(str(src))
    _add_text(doc[page], rect, "poll_field", value, align=align)
    doc.need_appearances(True)
    tmp = out_png.parent / (out_png.stem + "_tmp.pdf")
    doc.save(str(tmp))
    doc.close()
    pre = out_png.parent / (out_png.stem + "_tmp")     # distinct from out_png
    subprocess.run(["pdftoppm", "-png", "-r", str(DPI), "-f", str(page + 1),
                    "-l", str(page + 1), "-singlefile", str(tmp), str(pre)],
                   check=True)
    rendered = pre.with_suffix(".png")
    from PIL import Image, ImageDraw
    img = Image.open(rendered).convert("RGB")
    w = window * SCALE
    box = (max(0, int(w.x0)), max(0, int(w.y0)),
           min(img.width, int(w.x1)), min(img.height, int(w.y1)))
    crop = img.crop(box)
    d = ImageDraw.Draw(crop)
    r = fitz.Rect(rect) * SCALE
    d.rectangle([r.x0 - box[0], r.y0 - box[1], r.x1 - box[0], r.y1 - box[1]],
                outline=color, width=2)
    crop.save(out_png)
    tmp.unlink(missing_ok=True)
    rendered.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--worklist", type=pathlib.Path,
                    default=ROOT / "catalog" / "geometry_review_worklist.tsv")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    import csv
    rows = list(csv.DictReader(args.worklist.open(), delimiter="\t"))
    if args.limit:
        rows = rows[: args.limit]
    crops_dir = args.out / "poll_crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    by_form: dict[str, list] = {}
    for r in rows:
        by_form.setdefault(r["form"], []).append(r)

    units = []
    for form, frs in sorted(by_form.items()):
        src = fetch_source(form)
        align_map = _load_alignment(form, ROOT)
        doc = fitz.open(str(src))
        feats = {p: page_features(doc[p]) for p in range(doc.page_count)}
        G = json.loads((ROOT / "repo" / "forms" / form /
                        "fill_geometry.json").read_text())["fields"]
        doc.close()
        for r in frs:
            field, widx = r["field"], r["widget_idx"]
            spec = G.get(field)
            if not spec or not spec.get("widgets"):
                continue
            i = int(widx) if str(widx).isdigit() else 0
            if i >= len(spec["widgets"]):
                continue
            w = spec["widgets"][i]
            rect = fitz.Rect(w["rect"])
            pg = w["page"]
            al = _ALIGN_CONST.get(align_map.get(field))
            dtype = schema_dtype(form, field)
            val = sample_value(field, dtype)
            cands = candidates(rect, feats[pg])
            # shared crop window = union of all rects, padded
            allr = [rect] + [fitz.Rect(rc) for _, rc in cands]
            win = fitz.Rect(min(r.x0 for r in allr) - 70,
                            min(r.y0 for r in allr) - 45,
                            max(r.x1 for r in allr) + 70,
                            max(r.y1 for r in allr) + 45)
            uid = hashlib.md5(f"{form}|{field}|{widx}".encode()).hexdigest()[:10]
            opts = []
            # option A = current
            cur_png = crops_dir / f"{uid}_A.png"
            render_option(src, pg, list(rect), val, al, win, cur_png,
                          (220, 0, 0))
            opts.append({"key": "A", "label": "current (leave as-is)",
                         "rect": [round(v, 1) for v in rect],
                         "crop": f"poll_crops/{cur_png.name}"})
            for n, (label, rc) in enumerate(cands):
                k = chr(ord("B") + n)
                png = crops_dir / f"{uid}_{k}.png"
                render_option(src, pg, rc, val, al, win, png, (0, 90, 220))
                opts.append({"key": k, "label": label, "rect": rc,
                             "crop": f"poll_crops/{png.name}"})
            units.append({"id": uid, "form": form, "field": field,
                          "widget_idx": widx, "page": pg,
                          "value_shown": val, "via": r.get("via", ""),
                          "signal": r.get("signal", ""),
                          "detail": r.get("detail", ""), "options": opts})
            print(f"{form} {field} w{widx}: {len(opts)} options")
    (args.out / "poll_data.json").write_text(json.dumps(units, indent=1))
    print(f"\nwrote {args.out / 'poll_data.json'} — {len(units)} units")
    return 0


if __name__ == "__main__":
    sys.exit(main())
