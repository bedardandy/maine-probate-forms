#!/usr/bin/env python3
"""Vertical snap: seat single-line text rects a small clearance ABOVE the line.

Discovered from the human-review poll, then corrected by a second poll batch:
poppler centers text in the widget rect, so where the rect bottom lands decides
where the descenders (s, g, p, y) land relative to the supporting underline.

  Round 1 saw rects 2.5-5pt BELOW the line ("too low / move up") and lifted the
  bottom to underline + 0.5. Round 2 showed that was still a hair too low: 14
  fields came back "move up ~1/4 character height so it doesn't merge with the
  underline", and measuring them found the bottom sitting at underline + 0.5 --
  exactly the old TARGET. With the bottom on/just-below the line the descenders
  still clip it.

Corrected rule: seat the rect bottom CLEAR points ABOVE the line (clearance ~=
descender depth, "1/4 character height") so the glyph body sits on the line and
descenders clear it. One rule now covers both the gross "lift onto the line"
case and the fine "move up a hair" case: any single-line text rect (8<=h<=16)
with a clear supporting underline whose bottom is not already >= CLEAR above the
line is shifted UP so the bottom lands at underline - CLEAR. Move up only, cap
the shift, and skip if lifting would push the rect into printed text above.

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

CLEAR = 1.5         # seat the bottom this far ABOVE the line (descender gap)
MIN_SHIFT = 0.8     # ignore sub-pixel nudges
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
                delta = r.y1 - uy           # >0 = bottom below the line
                # want bottom to sit CLEAR above the line: target = uy - CLEAR
                shift = min(delta + CLEAR, MAXSHIFT)   # how far to move UP
                if shift <= MIN_SHIFT:
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
