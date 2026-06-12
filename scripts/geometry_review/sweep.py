#!/usr/bin/env python3
"""Tier 0 of the geometry review: analytic audit + sentinel render + crops.

For every form with fill_geometry.json:
  1. Fetch the blank source (tools.fetch cache, SHA-verified).
  2. ANALYTIC pass against the source's own text layer + vector drawings:
       - text rect intersects printed glyphs (excluding underscore runs)
       - no supporting line/underline/cell border under the rect (vertical)
       - rect starts under a printed label on the same line (horizontal)
       - checkbox rect far from the nearest printed checkbox square
  3. SENTINEL fill: every text widget gets a unique token (ZQnnn), every
     checkbox option gets an X — through the same _add_text/_add_checkbox
     appearance machinery the real fill uses (NeedAppearances + /Q).
  4. Render every page with poppler (pdftoppm), the conforming renderer.
  5. Crop every flagged widget (plus N clean controls per form) with a red
     box marking the expected rect — inputs for the vision voters.

Outputs under --out (default ~/geom-review-out):
  <form>/sentinel.pdf, <form>/page-N.png, <form>/crops/*.png
  candidates.jsonl   one row per (form, field, widget, flags…)
  controls.jsonl     clean-control crops for voter calibration

    python3 scripts/geometry_review/sweep.py --forms DE-101 --out /tmp/gr
    python3 scripts/geometry_review/sweep.py               # all forms
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import subprocess
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.fetch import fetch_source                      # noqa: E402
from tools.fill_pdf import (_add_text, _add_checkbox, _fontsize_for,  # noqa: E402
                            _load_alignment, _ALIGN_CONST)

DPI = 150
SCALE = DPI / 72.0
PAD = 2.0


# ── source-page features ────────────────────────────────────────────────────
def page_features(page: fitz.Page) -> dict:
    """Printed words (sans underscore runs), line segments, small squares.

    Mixed tokens ("$_____", "___COUNTY") are trimmed: the underscore run
    becomes a line, the glyph remainder keeps a proportional sub-box.
    Private-use glyphs (Wingdings checkboxes, U+F0xx) count as squares.
    """
    words = []
    extra_lines = []
    extra_squares = []
    for w in page.get_text("words"):
        r, t = fitz.Rect(w[:4]), w[4]
        if not t.strip() or set(t) <= set("_-–—."):
            if len(t) >= 3 and set(t) <= set("_"):
                pass                                   # handled as hline below
            continue
        if len(t) == 1 and 0xE000 <= ord(t) <= 0xF8FF:
            extra_squares.append(r)                    # printed checkbox glyph
            continue
        lead = len(t) - len(t.lstrip("_"))
        trail = len(t) - len(t.rstrip("_"))
        if lead + trail >= 3 and len(t) > lead + trail:
            cw = r.width / len(t)
            sub = fitz.Rect(r.x0 + lead * cw, r.y0, r.x1 - trail * cw, r.y1)
            extra_lines.append((r.x0, r.x1, r.y1 - 1)) # the run is a line
            words.append((sub, t.strip("_")))
            continue
        if lead + trail >= 3:
            extra_lines.append((r.x0, r.x1, r.y1 - 1))
            continue
        words.append((r, t))
    hlines, squares = [], []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":                      # line segment
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) <= 1.5 and abs(p1.x - p2.x) > 8:
                    hlines.append((min(p1.x, p2.x), max(p1.x, p2.x),
                                   (p1.y + p2.y) / 2))
            elif it[0] == "re":                   # rectangle
                r = fitz.Rect(it[1])
                if 4 <= r.width <= 18 and 4 <= r.height <= 18 \
                        and abs(r.width - r.height) <= 5:
                    squares.append(r)
                elif r.height <= 1.5 and r.width > 8:
                    hlines.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
                elif r.width > 30 and r.height > 8:
                    # table cell: bottom border supports text
                    hlines.append((r.x0, r.x1, r.y1))
    # underscore runs in the raw text are lines too
    for w in page.get_text("words"):
        t = w[4]
        if len(t) >= 3 and set(t) <= set("_"):
            r = fitz.Rect(w[:4])
            hlines.append((r.x0, r.x1, r.y1 - 1))
    hlines.extend(extra_lines)
    squares.extend(extra_squares)
    return {"words": words, "hlines": hlines, "squares": squares}


def analytics_text(rect: fitz.Rect, feats: dict, align: str = "left") -> dict:
    flags = {}
    # A small square-ish rect sitting on a printed checkbox glyph is a
    # checkbox-style mark in text clothing — judge it as a checkbox.
    if rect.width <= 16 and rect.height <= 16:
        return analytics_checkbox(rect, feats)
    # (a) printed-glyph intrusion into the rect
    overl = []
    for wr, t in feats["words"]:
        ix = rect & wr
        if not ix.is_empty and ix.get_area() > 0.30 * wr.get_area():
            overl.append(t)
    if align != "left":
        # centered/right text doesn't start at x0 — only mid-rect intrusions count
        mid = fitz.Rect(rect.x0 + 0.2 * rect.width, rect.y0,
                        rect.x1 - 0.2 * rect.width, rect.y1)
        overl = [t for wr, t in feats["words"]
                 if not (mid & wr).is_empty
                 and (mid & wr).get_area() > 0.30 * wr.get_area()]
    if overl:
        flags["print_overlap"] = overl[:6]
    # (b) line support — single-line rects only; tall rects are paragraph
    # areas / table cells where "on the line" has no meaning.
    if rect.height <= 24:
        cands = []
        for x0, x1, y in feats["hlines"]:
            if y < rect.y0 - 2 or y > rect.y1 + 10:
                continue
            span = min(rect.x1, x1) - max(rect.x0, x0)
            if span > 0.30 * rect.width:
                cands.append(y)
        if not cands:
            flags["no_line_support"] = True
        else:
            delta = round(min(cands, key=lambda y: abs(y - rect.y1)) - rect.y1, 1)
            if delta < -0.6 * rect.height:          # rect hangs below its line
                flags["sits_below_line"] = delta
            elif delta > 6:                          # rect floats above the line
                flags["floats_above_line"] = delta
    # (c) label intrusion: word ends inside rect's left edge on the same band
    # (left-aligned fields only — centered/right text never starts at x0)
    if align != "left":
        return flags
    for wr, t in feats["words"]:
        vert = min(rect.y1, wr.y1) - max(rect.y0, wr.y0)
        if vert > 0.5 * wr.height and rect.x0 + 3 < wr.x1 <= rect.x0 + 0.4 * rect.width:
            flags.setdefault("starts_under_label", []).append(t)
    return flags


def analytics_checkbox(rect: fitz.Rect, feats: dict) -> dict:
    flags = {}
    if not feats["squares"]:
        return flags
    c = ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    best = min(feats["squares"],
               key=lambda s: (((s.x0 + s.x1) / 2 - c[0]) ** 2 +
                              ((s.y0 + s.y1) / 2 - c[1]) ** 2))
    dx = (best.x0 + best.x1) / 2 - c[0]
    dy = (best.y0 + best.y1) / 2 - c[1]
    if abs(dx) > 5 or abs(dy) > 5:
        flags["off_square"] = [round(dx, 1), round(dy, 1)]
    return flags


# ── sentinel fill + render ──────────────────────────────────────────────────
def sentinel_fill(form_id: str, geom: dict, src: pathlib.Path,
                  out_pdf: pathlib.Path) -> dict:
    """Token per (field, widget); X in every checkbox option. Returns
    {token: {field, widget_idx, page, rect}}."""
    doc = fitz.open(str(src))
    align_map = _load_alignment(form_id, ROOT)
    tokens, n = {}, 0
    for fid, spec in geom.items():
        for i, w in enumerate(spec.get("widgets") or []):
            n += 1
            tok = f"ZQ{n:03d}"
            _add_text(doc[w["page"]], w["rect"], f"{fid}__s{i}", tok,
                      align=_ALIGN_CONST.get(align_map.get(fid)))
            tokens[tok] = {"field": fid, "widget_idx": i,
                           "page": w["page"], "rect": w["rect"]}
        for j, o in enumerate(spec.get("options") or []):
            _add_checkbox(doc[o["page"]], o["rect"], f"{fid}__o{j}")
    doc.need_appearances(True)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_pdf))
    doc.close()
    return tokens


def render(pdf: pathlib.Path, outdir: pathlib.Path) -> list[pathlib.Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftoppm", "-png", "-r", str(DPI), str(pdf),
                    str(outdir / "page")], check=True)
    return sorted(outdir.glob("page-*.png"))


def crop(page_png: pathlib.Path, rect, out_png: pathlib.Path,
         ctx: float = 90.0) -> None:
    """Crop rect + context from the rendered page; draw a red box on the rect."""
    from PIL import Image, ImageDraw
    img = Image.open(page_png)
    r = fitz.Rect(rect) * SCALE
    cx0 = max(0, int(r.x0 - ctx)); cy0 = max(0, int(r.y0 - ctx * 0.7))
    cx1 = min(img.width, int(r.x1 + ctx)); cy1 = min(img.height, int(r.y1 + ctx * 0.7))
    clip = img.crop((cx0, cy0, cx1, cy1)).convert("RGB")
    d = ImageDraw.Draw(clip)
    d.rectangle([r.x0 - cx0, r.y0 - cy0, r.x1 - cx0, r.y1 - cy0],
                outline=(255, 0, 0), width=2)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    clip.save(out_png)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", help="comma-separated subset")
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--controls", type=int, default=3,
                    help="clean-control crops per form")
    args = ap.parse_args()
    random.seed(20260612)

    forms_dir = ROOT / "repo" / "forms"
    forms = sorted(d.name for d in forms_dir.iterdir()
                   if (d / "fill_geometry.json").exists())
    if args.forms:
        want = {f.strip() for f in args.forms.split(",")}
        forms = [f for f in forms if f in want]

    args.out.mkdir(parents=True, exist_ok=True)
    cand_f = (args.out / "candidates.jsonl").open("a")
    ctrl_f = (args.out / "controls.jsonl").open("a")
    done_marker = args.out / "sweep_done.txt"
    already = set(done_marker.read_text().split()) if done_marker.exists() else set()

    for form in forms:
        if form in already:
            continue
        try:
            src = fetch_source(form)
        except Exception as e:
            print(f"{form}: fetch failed: {e}", file=sys.stderr)
            continue
        geom = json.loads((forms_dir / form / "fill_geometry.json").read_text())["fields"]
        fdir = args.out / form
        tokens = sentinel_fill(form, geom, src, fdir / "sentinel.pdf")
        pages = render(fdir / "sentinel.pdf", fdir)
        sdoc = fitz.open(str(src))
        feats = {p: page_features(sdoc[p]) for p in range(sdoc.page_count)}
        align_map = _load_alignment(form, ROOT)

        n_flag = 0
        clean: list[dict] = []
        for fid, spec in geom.items():
            for i, w in enumerate(spec.get("widgets") or []):
                fl = analytics_text(fitz.Rect(w["rect"]), feats[w["page"]],
                                    align=align_map.get(fid, "left"))
                row = {"form": form, "field": fid, "kind": "text",
                       "widget_idx": i, "page": w["page"], "rect": w["rect"],
                       "token": next((t for t, m in tokens.items()
                                      if m["field"] == fid and m["widget_idx"] == i), None)}
                if fl:
                    row["flags"] = fl
                    cp = fdir / "crops" / f"{fid}__w{i}.png"
                    if w["page"] < len(pages):
                        crop(pages[w["page"]], w["rect"], cp)
                        row["crop"] = str(cp)
                    cand_f.write(json.dumps(row) + "\n")
                    n_flag += 1
                else:
                    clean.append(row)
            for j, o in enumerate(spec.get("options") or []):
                fl = analytics_checkbox(fitz.Rect(o["rect"]), feats[o["page"]])
                row = {"form": form, "field": fid, "kind": "checkbox",
                       "option": o.get("value"), "page": o["page"],
                       "rect": o["rect"]}
                if fl:
                    row["flags"] = fl
                    cp = fdir / "crops" / f"{fid}__o{j}.png"
                    if o["page"] < len(pages):
                        crop(pages[o["page"]], o["rect"], cp)
                        row["crop"] = str(cp)
                    cand_f.write(json.dumps(row) + "\n")
                    n_flag += 1
                else:
                    clean.append(row)
        for row in random.sample(clean, min(args.controls, len(clean))):
            key = f"{row['field']}__w{row.get('widget_idx', row.get('option'))}"
            cp = fdir / "crops" / f"CTRL_{key}.png"
            if row["page"] < len(pages):
                crop(pages[row["page"]], row["rect"], cp)
                row["crop"] = str(cp)
                ctrl_f.write(json.dumps(row) + "\n")
        (args.out / "tokens" ).mkdir(exist_ok=True)
        (args.out / "tokens" / f"{form}.json").write_text(json.dumps(tokens))
        with done_marker.open("a") as fh:
            fh.write(form + "\n")
        sdoc.close()
        print(f"{form}: {n_flag} analytic flags, {len(pages)} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
