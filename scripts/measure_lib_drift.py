"""Quantify pdfplumber vs PyMuPDF horizontal-line disagreement.

For each TEXT/SIGNATURE widget in a fused PDF:
  * find the pdfplumber line nearest its bottom edge (proxy for the
    detector's anchor — detector reads h_lines via pdfplumber)
  * find the PyMuPDF / extract_anchors line nearest its bottom edge
    (Patch E's anchor)
  * report dx0, dx1 between the two libraries' takes on the same line.

A consistent multi-pt drift between the libraries means widget rects (set
by the detector) and snap targets (set by Patch E) come from different
geometric truth — and that's the dominant source of visible jaggedness.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import fitz
import pdfplumber

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.geometric_snap import extract_anchors, find_underline  # noqa: E402

# Pair-up tolerances.
PAIR_Y_TOL = 2.5     # call two lines "the same" if their y differs by <= this
PAIR_X_TOL = 30.0    # ... and one endpoint is within this many points


def pdfplumber_hlines(plumber_page):
    """Extract horizontal lines + thin rectangles from pdfplumber.
    Returns list of (x0, y, x1) tuples in PDF coordinates (origin top-left)."""
    h = plumber_page.height
    out = []
    for ln in plumber_page.lines:
        if abs(ln["y0"] - ln["y1"]) > 0.6:
            continue
        x0, x1 = sorted([ln["x0"], ln["x1"]])
        if x1 - x0 < 5:
            continue
        # pdfplumber y is from bottom; flip to fitz top-down.
        y_top = h - ln["y0"]
        out.append((x0, y_top, x1))
    for r in plumber_page.rects:
        if (r["height"] <= 1.6 and r["width"] >= 5):
            x0, x1 = r["x0"], r["x1"]
            y_top = h - r["y0"]   # rect bottom in fitz space
            out.append((x0, y_top, x1))
    return out


def find_nearest(widget_rect, lines, kind: str):
    """Pick the line nearest widget bottom with at least some x-overlap.
    `lines` is list of (x0, y, x1) for plumber, or list of HLine for fitz.
    """
    best = None
    best_score = float("inf")
    for ln in lines:
        if kind == "plumber":
            x0, y, x1 = ln
        else:
            x0, y, x1 = ln.x0, ln.y, ln.x1
        if x1 < widget_rect.x0 - 12 or x0 > widget_rect.x1 + 12:
            continue
        dy = abs(y - widget_rect.y1)
        if dy > 15:
            continue
        # Prefer line just below the widget bottom.
        score = dy + (0.0 if y >= widget_rect.y1 - 1 else 0.5)
        if score < best_score:
            best_score = score
            best = (x0, y, x1)
    return best


def measure(pdf_path: pathlib.Path) -> None:
    doc = fitz.open(pdf_path)
    plumber = pdfplumber.open(pdf_path)

    rows: list[tuple] = []
    paired = 0
    diff_x0_total = 0.0
    diff_x1_total = 0.0
    big_drift = 0  # count of widgets where dx0 or dx1 > 5pt
    for pno in range(doc.page_count):
        page = doc[pno]
        pp_page = plumber.pages[pno]
        anchors = extract_anchors(page)
        fitz_lines = anchors["hlines"]
        plumb_lines = pdfplumber_hlines(pp_page)
        for w in (page.widgets() or []):
            if w.field_type not in (6, 7):
                continue
            r = w.rect
            pl = find_nearest(r, plumb_lines, "plumber")
            fl = find_nearest(r, fitz_lines, "fitz")
            if pl is None or fl is None:
                rows.append((pno, w.field_name or "?",
                             None if pl is None else (round(pl[0], 1), round(pl[2], 1)),
                             None if fl is None else (round(fl[0], 1), round(fl[2], 1)),
                             None, None, "missing"))
                continue
            dx0 = fl[0] - pl[0]
            dx1 = fl[2] - pl[2]
            paired += 1
            diff_x0_total += abs(dx0)
            diff_x1_total += abs(dx1)
            if abs(dx0) > 5 or abs(dx1) > 5:
                big_drift += 1
            rows.append((pno, w.field_name or "?",
                         (round(pl[0], 1), round(pl[2], 1)),
                         (round(fl[0], 1), round(fl[2], 1)),
                         round(dx0, 1), round(dx1, 1), "ok"))

    plumber.close()
    doc.close()

    print(f"\n{pdf_path.name}")
    print(f"  text/sig widgets paired across libraries: {paired}")
    if paired:
        print(f"  mean |dx0|: {diff_x0_total/paired:.2f}pt")
        print(f"  mean |dx1|: {diff_x1_total/paired:.2f}pt")
        print(f"  widgets with >5pt drift on x0 or x1: {big_drift}  "
              f"({100*big_drift/paired:.0f}%)")
    print()
    print(f"  {'page':>4} {'name':<32} "
          f"{'plumb x0..x1':>17} {'fitz x0..x1':>17} "
          f"{'dx0':>6} {'dx1':>6}")
    for pno, name, pl, fl, dx0, dx1, tag in rows:
        if tag == "missing":
            pl_s = f"{pl[0]}..{pl[1]}" if pl else "    --"
            fl_s = f"{fl[0]}..{fl[1]}" if fl else "    --"
            print(f"  {pno:>4} {name[:32]:<32} {pl_s:>17} {fl_s:>17} "
                  f"{'':>6} {'':>6}  missing")
        else:
            pl_s = f"{pl[0]}..{pl[1]}"
            fl_s = f"{fl[0]}..{fl[1]}"
            mark = "  *" if (abs(dx0) > 5 or abs(dx1) > 5) else ""
            print(f"  {pno:>4} {name[:32]:<32} {pl_s:>17} {fl_s:>17} "
                  f"{dx0:>6} {dx1:>6}{mark}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    args = ap.parse_args()
    measure(args.pdf)
