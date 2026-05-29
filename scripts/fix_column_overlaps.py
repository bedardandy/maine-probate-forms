#!/usr/bin/env python3
"""Shrink overlapping text widgets so a wide left-column field doesn't
extend through a narrow right-column field on the same row.

Pattern this fixes (e.g. AF-105):
    "Stocks/Bonds, CDs, etc. - (specify) __________ $ ________"
The upstream heuristic detector identified TWO logical fields here — a
"specify" text and a "$ amount" — but placed the "specify" widget rect
from the left margin all the way to the right margin (eating through
the "$ amount" widget). The snap can't fix this from underscores alone
because both underscores end at the same right margin.

After all positioning passes (snap, pin), we run this fix:
  For each pair of TEXT widgets on the same row with DIFFERENT names
  whose rects overlap by > min_overlap_pt2, identify the wider one and
  shrink its x1 to (narrower.x0 - gap_pt).

Heuristic constraints:
  * Same name → multi-widget consolidation, skip (overlap is intentional).
  * Strong y-overlap required (> 60% of either widget's height) — we
    only want to shrink for SAME-ROW overlaps, not adjacent-row touches.
  * The wider widget is the one we shrink, the narrower one stays as
    the right-aligned amount/value field.
"""
from __future__ import annotations
import argparse
import pathlib
import sys
import fitz


def y_overlap_ratio(a: fitz.Rect, b: fitz.Rect) -> float:
    iy0 = max(a.y0, b.y0)
    iy1 = min(a.y1, b.y1)
    if iy1 <= iy0:
        return 0.0
    overlap = iy1 - iy0
    return overlap / min(a.height, b.height)


def x_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    ix0 = max(a.x0, b.x0)
    ix1 = min(a.x1, b.x1)
    return max(0.0, ix1 - ix0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--min-overlap-pt2", type=float, default=50.0)
    ap.add_argument("--min-y-overlap", type=float, default=0.6,
                    help="require y_overlap/min_height >= this; same-row only")
    ap.add_argument("--gap-pt", type=float, default=2.0,
                    help="leave this much padding between shrunk widgets")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr); return 2

    doc = fitz.open(args.pdf)
    fixed = 0
    for page in doc:
        text_widgets = [w for w in page.widgets() if w.field_type == 7]
        # Pre-collect xrefs since we'll mutate widgets in-place
        rects: list[tuple[int, str, fitz.Rect]] = [
            (w.xref, w.field_name, fitz.Rect(w.rect)) for w in text_widgets
        ]
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                xa, na, ra = rects[i]
                xb, nb, rb = rects[j]
                if na == nb:
                    continue  # multi-widget consolidation
                if y_overlap_ratio(ra, rb) < args.min_y_overlap:
                    continue  # different rows
                ox = x_overlap(ra, rb)
                if ox * min(ra.height, rb.height) < args.min_overlap_pt2:
                    continue
                # Pick wider widget — shrink its x1 to narrower.x0 - gap
                if ra.width >= rb.width:
                    wide_xref, wide_name, wide_rect = xa, na, ra
                    narrow_rect = rb
                else:
                    wide_xref, wide_name, wide_rect = xb, nb, rb
                    narrow_rect = ra
                # Decide whether wide widget should be shrunk on the right
                # (narrow on the right) or on the left (narrow on the left).
                wide_center = (wide_rect.x0 + wide_rect.x1) / 2
                narrow_center = (narrow_rect.x0 + narrow_rect.x1) / 2
                if narrow_center > wide_center:
                    new_x1 = narrow_rect.x0 - args.gap_pt
                    new_x0 = wide_rect.x0
                else:
                    new_x0 = narrow_rect.x1 + args.gap_pt
                    new_x1 = wide_rect.x1
                if new_x1 <= new_x0:
                    continue  # would invert
                new_rect = fitz.Rect(new_x0, wide_rect.y0,
                                     new_x1, wide_rect.y1)
                if args.verbose:
                    print(f"  shrink p{page.number} {wide_name!r}: "
                          f"({wide_rect.x0:.1f},{wide_rect.x1:.1f}) → "
                          f"({new_rect.x0:.1f},{new_rect.x1:.1f})  "
                          f"to clear {(na if wide_name == nb else nb)!r}")
                if not args.dry_run:
                    ph = page.rect.height
                    y_ll = ph - new_rect.y1
                    y_ur = ph - new_rect.y0
                    doc.xref_set_key(wide_xref, "Rect",
                                     f"[{new_rect.x0} {y_ll} {new_rect.x1} {y_ur}]")
                # update the in-memory list so subsequent comparisons
                # see the shrunk rect
                if rects[i][0] == wide_xref:
                    rects[i] = (xa, na, new_rect)
                else:
                    rects[j] = (xb, nb, new_rect)
                fixed += 1

    print(f"shrunk {fixed} widget(s)")
    if args.dry_run:
        return 0
    out = args.out or args.pdf
    doc.save(out, incremental=(out == args.pdf),
             encryption=fitz.PDF_ENCRYPT_KEEP)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
