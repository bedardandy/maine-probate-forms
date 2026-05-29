"""Diagnostic: did Patch E balloon text-widget x-extents to oversized underlines?

For a fused PDF, for each TEXT/SIGNATURE widget:
  * read current rect
  * extract hlines via geometric_snap
  * find the hline Patch E *would* match today (find_underline)
  * report widget width, matched-line width, ratio, and whether the match
    extends notably past the widget on either side

Flags any widget where (matched line is >50pt wider than widget center +/- a
small pad), which is the signature of "long row-rule absorbed by short field".
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.geometric_snap import (  # noqa: E402
    extract_anchors,
    find_underline,
)


def measure(pdf_path: pathlib.Path) -> None:
    d = fitz.open(pdf_path)
    total = 0
    matched = 0
    crept = 0
    very_crept = 0
    rows: list[tuple] = []
    for pno in range(d.page_count):
        page = d[pno]
        anchors = extract_anchors(page)
        hlines = anchors["hlines"]
        for w in (page.widgets() or []):
            if w.field_type not in (6, 7):  # SIG or TEXT
                continue
            total += 1
            r = w.rect
            wname = w.field_name or "?"
            wwid = r.x1 - r.x0
            hl = find_underline(r, hlines)
            if hl is None:
                rows.append((pno, wname, round(wwid, 1), None, None, None,
                             None, "no-anchor"))
                continue
            matched += 1
            lwid = hl.x1 - hl.x0
            left_overhang = max(0.0, r.x0 - hl.x0)
            right_overhang = max(0.0, hl.x1 - r.x1)
            total_overhang = left_overhang + right_overhang
            tag = "ok"
            if total_overhang > 25:
                tag = "creep"
                crept += 1
            if total_overhang > 75:
                tag = "BIG-CREEP"
                very_crept += 1
            rows.append((pno, wname, round(wwid, 1), round(lwid, 1),
                         round(left_overhang, 1), round(right_overhang, 1),
                         round(total_overhang, 1), tag))

    d.close()

    print(f"\n{pdf_path.name}")
    print(f"  text/sig widgets:       {total}")
    print(f"  matched a vector hline: {matched}")
    print(f"  match overhangs >25pt:  {crept}    (= 'horizontal creep')")
    print(f"  match overhangs >75pt:  {very_crept}    (= 'big creep')")
    print()
    print(f"  {'page':>4} {'name':<30} {'w':>6} {'line':>6} "
          f"{'L+':>5} {'R+':>5} {'sum':>5}  flag")
    for row in rows:
        pno, wname, wwid, lwid, lo, ro, sum_, tag = row
        if tag == "no-anchor":
            print(f"  {pno:>4} {wname[:30]:<30} {wwid:>6} "
                  f"{'   --':>6} {'  --':>5} {'  --':>5} {'  --':>5}  {tag}")
        else:
            print(f"  {pno:>4} {wname[:30]:<30} {wwid:>6} {lwid:>6} "
                  f"{lo:>5} {ro:>5} {sum_:>5}  {tag}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    args = ap.parse_args()
    measure(args.pdf)
