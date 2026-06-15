#!/usr/bin/env python3
"""Horizontal snap: match a single-line text rect to its underline segment.

The larger poll batch confirmed this trend across ~13 fields (incl. a whole
N-118 cluster): "trim the right so it spans the underline", "shift left so it
stays in the underline", "extend the right a character to the line end". The
unifying rule is: a single-line text field over ONE clean underline segment
should have x0 = segment start, x1 = segment end.

Strong guards (the heterogeneity that made me hold back before):
  - exactly ONE hline segment overlaps the field's y-band by >40% of its width
  - that segment is a real blank: 25pt <= width <= 0.8*page (not a tiny tick,
    not a full-page rule)
  - field is left/right aligned single-line (8-16pt); center captions skipped
  - never extend x0 left past a printed word on the same line (stay past the
    label); keep snapped width >= 25pt
  - only act when the mismatch is meaningful (|dx0|>4 or |dx1|>4)
Vertical is left untouched (snap_underline.py handles it); current y is kept.

    python3 scripts/geometry_review/snap_horizontal.py            # dry-run
    python3 scripts/geometry_review/snap_horizontal.py --apply
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools.fetch import fetch_source                       # noqa: E402
from scripts.geometry_review.sweep import page_features     # noqa: E402

GAP = 1.5
MIN_W = 25.0
MIN_DELTA = 4.0


def lone_segment(rect: fitz.Rect, feats: dict, page_w: float):
    """Return the single clean underline segment under the field, or None.

    Rejects multi-blank lines: a wide box that straddles two separate blanks
    (e.g. "Date: ___ at ___") would otherwise snap onto whichever blank passes
    the width test and drag the value off its real blank. So require exactly
    ONE segment with any real overlap (>8pt), not just one that passes 40%.
    """
    any_seg, strong = [], []
    for x0, x1, y in feats["hlines"]:
        if rect.y0 - 3 <= y <= rect.y1 + 9:
            ov = min(rect.x1, x1) - max(rect.x0, x0)
            if ov > 8:
                any_seg.append((x0, x1, y, ov))
            if ov > 0.40 * rect.width:
                strong.append((x0, x1, y, ov))
    if len(any_seg) != 1 or len(strong) != 1:
        return None
    x0, x1, y, ov = strong[0]
    if not (MIN_W <= (x1 - x0) <= 0.8 * page_w):
        return None
    # the box must not extend far beyond the segment on either side (that
    # means it covers something else too, even if no line was detected there)
    if (x0 - rect.x0) > 30 or (rect.x1 - x1) > 30:
        return None
    return (x0, x1, y)


def left_label_limit(rect: fitz.Rect, feats: dict, seg_x0: float) -> float:
    """Don't extend x0 left past a printed word sitting between seg start and
    the current left edge on this line."""
    lim = seg_x0
    for wr, t in feats["words"]:
        if min(rect.y1, wr.y1) - max(rect.y0, wr.y0) <= 0.4 * wr.height:
            continue
        if seg_x0 - 2 <= wr.x1 <= rect.x0 + 0.5 * rect.width:
            lim = max(lim, wr.x1 + GAP)
    return lim


def right_word_limit(rect: fitz.Rect, feats: dict, seg_x1: float,
                     start_x: float) -> float:
    """Cap the right edge before any printed word the underline abuts or runs
    under (e.g. the "COUNTY" in "____COUNTY PROBATE COURT"). Without this the
    snap extends back over a trailing label and re-creates print overlap."""
    lim = seg_x1
    for wr, t in feats["words"]:
        if min(rect.y1, wr.y1) - max(rect.y0, wr.y0) <= 0.4 * wr.height:
            continue
        # a word starting to the right of the blank's start, at/inside the
        # segment's right end (the line abuts or underruns it)
        if start_x + 6 < wr.x0 <= seg_x1 + 4:
            lim = min(lim, wr.x0 - GAP)
    return lim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    args = ap.parse_args()
    align_all = json.load(open(ROOT / "catalog" / "field_alignment.json")).get("forms", {})

    forms = sorted(d.name for d in (ROOT / "repo" / "forms").iterdir()
                   if (d / "fill_geometry.json").exists())
    if args.forms:
        want = {f.strip() for f in args.forms.split(",")}
        forms = [f for f in forms if f in want]

    planned = []
    log = (args.out / "hsnap2_applied.jsonl").open("a") if args.apply else None
    for form in forms:
        gp = ROOT / "repo" / "forms" / form / "fill_geometry.json"
        g = json.loads(gp.read_text())
        al = align_all.get(form, {})
        try:
            doc = fitz.open(str(fetch_source(form)))
        except Exception:
            continue
        feats = {p: page_features(doc[p]) for p in range(doc.page_count)}
        pw = doc[0].rect.width
        doc.close()
        changed = 0
        for fid, spec in g["fields"].items():
            if al.get(fid) == "center":
                continue
            wlist = spec.get("widgets") or []
            for i, w in enumerate(wlist):
                r = fitz.Rect(w["rect"])
                if not (8 <= r.height <= 16):
                    continue
                # same-line sibling cells (street/city/zip on one underline)
                # would all snap to the one segment and collapse — skip them
                if any(j != i and w.get("page") == w2.get("page")
                       and abs(r.y0 - w2["rect"][1]) < 3
                       for j, w2 in enumerate(wlist)):
                    continue
                seg = lone_segment(r, feats[w["page"]], pw)
                if seg is None:
                    continue
                sx0, sx1, sy = seg
                nx0 = left_label_limit(r, feats[w["page"]], sx0 + GAP)
                nx1 = right_word_limit(r, feats[w["page"]], sx1 - GAP, nx0)
                if nx1 - nx0 < MIN_W:
                    continue
                if abs(nx0 - r.x0) < MIN_DELTA and abs(nx1 - r.x1) < MIN_DELTA:
                    continue
                new = [round(nx0, 1), round(r.y0, 1),
                       round(nx1, 1), round(r.y1, 1)]
                planned.append((form, fid, i, round(r.x0 - nx0, 1),
                                round(nx1 - r.x1, 1)))
                if args.apply:
                    w["rect"] = new
                    changed += 1
                    log.write(json.dumps({"form": form, "field": fid,
                              "widget_idx": i, "old": list(r), "new": new}) + "\n")
        if args.apply and changed:
            gp.write_text(json.dumps(g, indent=1))
            print(f"{form}: snapped {changed}")

    print(f"\nplanned horizontal snaps: {len(planned)} across "
          f"{len(set(p[0] for p in planned))} forms")
    grew = sum(1 for p in planned if p[3] > 0 or p[4] > 0)
    shr = sum(1 for p in planned if p[4] < 0)
    print(f"  extend-left (dx0>0): {sum(1 for p in planned if p[3]>0)} | "
          f"trim/extend-right: shrink {shr} / grow {sum(1 for p in planned if p[4]>0)}")
    if not args.apply:
        for p in planned[:24]:
            print(f"  {p[0]:9} {p[1][:26]:26} w{p[2]} extendL={p[3]} dR={p[4]}")
        if len(planned) > 24:
            print(f"  … +{len(planned)-24} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
