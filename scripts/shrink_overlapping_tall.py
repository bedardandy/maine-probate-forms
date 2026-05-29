#!/usr/bin/env python3
"""Shrink tall-stripe widgets that engulf other widgets on rows below or
above. Pattern (DE-301): upstream detector made a "Other ___" widget 70pt
tall, eating the next two rows. After snap, the widget still spans rows
that have their own widgets, producing same-page different-row overlaps.

Strategy:
  For each pair of text widgets that overlap by > min_overlap_pt with
  different names AND a height difference > min_height_diff_pt, the taller
  one is the suspect. Look near its top/bottom edge for a single short
  underscore (width < width_ratio_max × widget_width). If found, shrink
  the taller widget to canonical_height anchored to that underscore.

This is conservative on purpose — it ONLY fires when there's already an
overlap (audit-visible bug), and only when a single short underscore is
nearby. Multi-line essay areas (AF-104 family_friends_contacted etc.) are
not touched because they don't overlap anything.
"""
from __future__ import annotations
import argparse
import pathlib
import sys
import fitz


def short_underscores_near(page, rect, *, x_overlap_min, y_margin_above,
                            y_margin_below, width_ratio_max, min_width):
    y_top = rect.y0 - y_margin_above
    y_bot = rect.y1 + y_margin_below
    found = []
    for d in page.get_drawings():
        for item in d.get("items", []):
            if item[0] == "re":
                r = item[1]
                if (r.height < 3 and r.width >= min_width
                        and y_top <= r.y0 <= y_bot):
                    ix = max(0.0, min(r.x1, rect.x1) - max(r.x0, rect.x0))
                    if (ix >= rect.width * x_overlap_min
                            or ix >= r.width * 0.5):
                        if r.width <= rect.width * width_ratio_max:
                            found.append((r.x0, r.y0, r.x1, r.y1))
    for blk in page.get_text("rawdict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                chars = sp.get("chars", [])
                us = [ch for ch in chars if ch["c"] == "_"]
                if not us:
                    continue
                groups = [[us[0]]]
                for ch in us[1:]:
                    if ch["bbox"][0] - groups[-1][-1]["bbox"][2] < 2:
                        groups[-1].append(ch)
                    else:
                        groups.append([ch])
                for g in groups:
                    x0 = min(c["bbox"][0] for c in g)
                    x1 = max(c["bbox"][2] for c in g)
                    y0 = min(c["bbox"][1] for c in g)
                    y1 = max(c["bbox"][3] for c in g)
                    w = x1 - x0
                    if w < min_width:
                        continue
                    if y_top <= y1 <= y_bot:
                        ix = max(0.0, min(x1, rect.x1) - max(x0, rect.x0))
                        if (ix >= rect.width * x_overlap_min
                                or ix >= w * 0.5):
                            if w <= rect.width * width_ratio_max:
                                found.append((x0, y0, x1, y1))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--canonical-height", type=float, default=12.0)
    ap.add_argument("--min-overlap-pt2", type=float, default=100.0,
                    help="only consider pairs whose overlap area exceeds this")
    ap.add_argument("--min-y-overlap-pt", type=float, default=2.0,
                    help="require this much y-overlap to call it a row crash")
    ap.add_argument("--min-height-diff-pt", type=float, default=12.0,
                    help="taller widget must be this many pt taller than "
                         "the shorter one")
    ap.add_argument("--width-ratio-max", type=float, default=0.5,
                    help="found underscore must be <= this × widget width "
                         "(rejects full-row decorative section underscores)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr)
        return 2

    doc = fitz.open(args.pdf)
    shrunk = 0
    for page in doc:
        text_widgets = [w for w in page.widgets() if w.field_type == 7]
        # (xref, name, rect) tuples we mutate in place
        slots = [(w.xref, w.field_name, fitz.Rect(w.rect))
                 for w in text_widgets]
        for i in range(len(slots)):
            for j in range(i + 1, len(slots)):
                xa, na, ra = slots[i]
                xb, nb, rb = slots[j]
                if na == nb:
                    continue
                y_ov = min(ra.y1, rb.y1) - max(ra.y0, rb.y0)
                if y_ov < args.min_y_overlap_pt:
                    continue
                x_ov = min(ra.x1, rb.x1) - max(ra.x0, rb.x0)
                if x_ov <= 0:
                    continue
                area = y_ov * x_ov
                if area < args.min_overlap_pt2:
                    continue
                # Determine the taller widget — that's our shrink target
                if abs(ra.height - rb.height) < args.min_height_diff_pt:
                    continue
                if ra.height > rb.height:
                    tall_idx, tall_xref, tall_rect = i, xa, ra
                else:
                    tall_idx, tall_xref, tall_rect = j, xb, rb

                us = short_underscores_near(
                    page, tall_rect,
                    x_overlap_min=0.05,
                    y_margin_above=20.0,
                    y_margin_below=5.0,
                    width_ratio_max=args.width_ratio_max,
                    min_width=20.0,
                )
                # Reject any candidate underscore whose y is within 2pt of
                # another widget's bottom (y1) — that underscore belongs
                # to the neighbor widget, not ours. MISC-101 q4c eats the
                # date_signed underscore; without this check we'd anchor
                # the tall widget to date_signed's line, producing an
                # identical-rect overlap.
                other_y1s = [r.y1 for k, (_, _, r) in enumerate(slots)
                             if k != tall_idx]
                us = [u for u in us
                      if not any(abs(u[3] - y1) < 2.0
                                 for y1 in other_y1s)]
                if len(us) != 1:
                    if args.verbose:
                        print(f"  skip p{page.number} "
                              f"{slots[tall_idx][1]!r}: "
                              f"{len(us)} orphan short underscores found")
                    continue
                ux0, uy0, ux1, uy1 = us[0]
                new_y1 = uy1  # bottom of widget = bottom of underscore glyph
                new_y0 = new_y1 - args.canonical_height
                new_x0 = ux0
                new_x1 = ux1
                new_rect = fitz.Rect(new_x0, new_y0, new_x1, new_y1)
                if args.verbose:
                    print(f"  shrink p{page.number} "
                          f"{slots[tall_idx][1]!r}: "
                          f"{tuple(round(c,1) for c in tall_rect)} → "
                          f"{tuple(round(c,1) for c in new_rect)}")
                if not args.dry_run:
                    ph = page.rect.height
                    y_ll = ph - new_rect.y1
                    y_ur = ph - new_rect.y0
                    doc.xref_set_key(tall_xref, "Rect",
                                     f"[{new_rect.x0} {y_ll} "
                                     f"{new_rect.x1} {y_ur}]")
                slots[tall_idx] = (tall_xref, slots[tall_idx][1], new_rect)
                shrunk += 1

    print(f"shrunk {shrunk} tall-overlapping widget(s)")
    if args.dry_run:
        return 0
    doc.save(args.pdf, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
