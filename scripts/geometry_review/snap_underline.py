#!/usr/bin/env python3
"""Vertical snap: lift single-line text rects whose bottom sits below the line.

Discovered from the human-review poll: ~10% of single-line text widgets have
their rect bottom 2.5-5pt BELOW the supporting underline. Poppler centers text
in the widget rect, so the baseline lands on/under the line and descenders
merge with it ("too low / move up ~1/4-1/3 char height"). Clean fields sit at
rect.y1 - underline_y ~= 0 (median +0.2 across the corpus). The analytic sweep
missed this class: it only flagged the few that ALSO had horizontal overlap.

Fix: for single-line text widgets (8<=h<=16) with a clear supporting
underline whose bottom is >= THRESH below the line, shift the whole rect UP so
the bottom lands at underline_y + TARGET. Move up only, cap the shift, and
skip if lifting would push the rect into printed text above.

    python3 scripts/geometry_review/snap_underline.py            # dry-run
    python3 scripts/geometry_review/snap_underline.py --apply
    python3 scripts/geometry_review/snap_underline.py --forms DE-403 --apply
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

THRESH = 2.5        # only fix rects this many pt (or more) below the line
TARGET = 0.5        # leave the bottom this far below the line after the lift
MAXSHIFT = 6.0      # never lift more than this
H_MIN, H_MAX = 8.0, 16.0


def underline_for(rect: fitz.Rect, feats: dict) -> float | None:
    cands = [y for x0, x1, y in feats["hlines"]
             if rect.y0 - 4 <= y <= rect.y1 + 8
             and min(rect.x1, x1) - max(rect.x0, x0) > 0.30 * rect.width]
    return min(cands, key=lambda y: abs(y - rect.y1)) if cands else None


def collides_above(rect: fitz.Rect, feats: dict, new_y0: float) -> bool:
    """A printed word would sit inside the lifted rect's top region."""
    band_top, band_bot = new_y0 - 1, new_y0 + 0.45 * rect.height
    for wr, t in feats["words"]:
        if min(band_bot, wr.y1) - max(band_top, wr.y0) > 0.4 * wr.height \
                and min(rect.x1, wr.x1) - max(rect.x0, wr.x0) > 2:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", help="comma-separated subset")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    args = ap.parse_args()

    forms = sorted(d.name for d in (ROOT / "repo" / "forms").iterdir()
                   if (d / "fill_geometry.json").exists())
    if args.forms:
        want = {f.strip() for f in args.forms.split(",")}
        forms = [f for f in forms if f in want]

    planned, skipped_collide = [], 0
    applied_log = (args.out / "vsnap_applied.jsonl").open("a") if args.apply else None
    for form in forms:
        gp = ROOT / "repo" / "forms" / form / "fill_geometry.json"
        g = json.loads(gp.read_text())
        try:
            doc = fitz.open(str(fetch_source(form)))
        except Exception:
            continue
        feats = {p: page_features(doc[p]) for p in range(doc.page_count)}
        doc.close()
        changed = 0
        for fid, spec in g["fields"].items():
            for i, w in enumerate(spec.get("widgets") or []):
                r = fitz.Rect(w["rect"])
                if not (H_MIN <= r.height <= H_MAX):
                    continue
                uy = underline_for(r, feats[w["page"]])
                if uy is None:
                    continue
                delta = r.y1 - uy
                if delta < THRESH:
                    continue
                shift = min(delta - TARGET, MAXSHIFT)
                if shift <= 0.5:
                    continue
                new_y0 = r.y0 - shift
                if collides_above(r, feats[w["page"]], new_y0):
                    skipped_collide += 1
                    continue
                new = [round(r.x0, 1), round(new_y0, 1),
                       round(r.x1, 1), round(r.y1 - shift, 1)]
                planned.append((form, fid, i, round(delta, 1), round(shift, 1)))
                if args.apply:
                    w["rect"] = new
                    changed += 1
                    applied_log.write(json.dumps(
                        {"form": form, "field": fid, "widget_idx": i,
                         "delta": round(delta, 1), "shift": round(shift, 1),
                         "new_rect": new}) + "\n")
        if args.apply and changed:
            gp.write_text(json.dumps(g, indent=1))
            print(f"{form}: lifted {changed} rect(s)")

    print(f"\nplanned lifts: {len(planned)} across "
          f"{len(set(p[0] for p in planned))} forms "
          f"(skipped {skipped_collide} that would collide above)")
    if not args.apply:
        for p in planned[:25]:
            print(f"  {p[0]:9} {p[1][:26]:26} w{p[2]} delta={p[3]} lift={p[4]}")
        if len(planned) > 25:
            print(f"  … +{len(planned)-25} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
