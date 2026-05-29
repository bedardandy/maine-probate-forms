#!/usr/bin/env python3
"""Audit text widget rects across the tree-built PDFs to find bad upstream
detections that need rect_overrides.

Flags two patterns:
  1. Overlapping rects — two text widgets whose bounding boxes intersect
     by more than a small slop. Almost always a sign that the upstream
     detector merged or duplicated a line.
  2. Extreme widths — text widget wider than 1.5× the median width on the
     form. Often a sign that the detector spanned multiple underscores.

For each flagged widget, print enough info that a human can write a
rect_override entry: form, page, name, current rect, source widget ID
(if a digest is available).
"""
from __future__ import annotations
import argparse
import pathlib
import re
import sys
import statistics
import fitz


def widget_id_from_digest(digest_text: str) -> dict[tuple[int, str], str]:
    """Map (page_idx, field_name) → Wxxx by scanning the digest. The
    apply_tree.py renames widgets to their tree-node id, so we match on
    name; if multiple widgets share a name, we can't disambiguate from
    the digest alone."""
    # Digest entries look like:
    #   [W001 TXT @p1 x=72 y=121]
    out: dict[tuple[int, str], str] = {}
    for m in re.finditer(r"\[W(\d{3})\s+\w+\s+@p(\d+)\s+x=(\d+)\s+y=(\d+)",
                         digest_text):
        wid = f"W{m.group(1)}"
        page = int(m.group(2)) - 1
        x = int(m.group(3))
        y = int(m.group(4))
        out[(page, x, y)] = wid
    return out


def overlap_area(a: fitz.Rect, b: fitz.Rect) -> float:
    ix0 = max(a.x0, b.x0)
    iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1)
    iy1 = min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


def audit_pdf(pdf_path: pathlib.Path, digest_path: pathlib.Path | None,
              wide_factor: float = 1.5,
              min_overlap_pt2: float = 4.0,
              min_y_overlap_pt: float = 1.5,
              check_wide: bool = False) -> list[dict]:
    """Return a list of issue dicts for this PDF."""
    doc = fitz.open(pdf_path)
    digest_widget_ids: dict[tuple[int, int, int], str] = {}
    if digest_path and digest_path.exists():
        digest_widget_ids = widget_id_from_digest(digest_path.read_text())

    # Collect all text widgets across all pages
    widgets: list[tuple[int, fitz.Widget]] = []
    for page in doc:
        for w in page.widgets():
            if w.field_type == 7:
                widgets.append((page.number, w))

    issues: list[dict] = []

    # 1. overlapping rects (within a page). Skip pairs that share a field
    # name — those are multi-widget consolidation siblings and the overlap
    # is by-design (typing one syncs to all). Skip pairs whose overlap is
    # under min_overlap_pt2, which filters cosmetic 1pt-y boundary touches
    # between adjacent stacked rows.
    by_page: dict[int, list[fitz.Widget]] = {}
    for p, w in widgets:
        by_page.setdefault(p, []).append(w)
    for p, ws in by_page.items():
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                a, b = ws[i].rect, ws[j].rect
                if ws[i].field_name == ws[j].field_name:
                    continue  # multi-widget consolidation — intentional
                # Compute y/x overlap separately so we can filter cosmetic
                # adjacent-row touches (sub-pt y overlap × wide x extent).
                y_ov = min(a.y1, b.y1) - max(a.y0, b.y0)
                if y_ov < min_y_overlap_pt:
                    continue
                area = overlap_area(a, b)
                if area >= min_overlap_pt2:
                    issues.append({
                        "type": "overlap",
                        "page": p,
                        "name_a": ws[i].field_name,
                        "rect_a": tuple(round(c, 1) for c in a),
                        "name_b": ws[j].field_name,
                        "rect_b": tuple(round(c, 1) for c in b),
                        "overlap_pt2": round(area, 1),
                    })

    # 2. extreme widths (opt-in — noisy; many legit full-width fields)
    widths = [w.rect.width for _, w in widgets]
    if check_wide and widths:
        median = statistics.median(widths)
        threshold = median * wide_factor
        for p, w in widgets:
            if w.rect.width > threshold:
                issues.append({
                    "type": "wide",
                    "page": p,
                    "name": w.field_name,
                    "rect": tuple(round(c, 1) for c in w.rect),
                    "width": round(w.rect.width, 1),
                    "median": round(median, 1),
                })
    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path(__file__).parent.parent)
    ap.add_argument("--check-wide", action="store_true",
                    help="also flag widgets wider than wide-factor × median "
                         "width. Off by default — produces many false "
                         "positives on forms with legit full-width fields.")
    ap.add_argument("--wide-factor", type=float, default=1.5)
    ap.add_argument("--min-overlap-pt2", type=float, default=100.0,
                    help="ignore overlaps below this area in pt². default "
                         "100pt² filters cosmetic 1pt boundary touches "
                         "between adjacent rows; real same-row overlaps "
                         "have area >> 100pt² (full widget height × x-extent).")
    ap.add_argument("--min-y-overlap-pt", type=float, default=1.5,
                    help="ignore overlaps with y-overlap below this in pt. "
                         "Canonical-height widgets touch adjacent rows by "
                         "0.2-1pt cosmetically; only sub-row spillover above "
                         "this threshold is a real same-row collision.")
    args = ap.parse_args()

    pdf_root = args.root / "output_tree"
    digest_root = args.root / "intermediate" / "digest"
    pdfs = sorted(pdf_root.glob("*/*.pdf"))
    print(f"auditing {len(pdfs)} PDFs\n")

    total_issues = 0
    for pdf in pdfs:
        # form_id is everything before the first space in the filename
        form_id = pdf.stem.split()[0].rstrip("_tree")
        # the form_id is actually first part before space (e.g. "AD-008")
        m = re.match(r"^([A-Z]+-?\d+)", pdf.stem)
        form_id = m.group(1) if m else pdf.stem
        digest_path = digest_root / f"{form_id}.txt"
        issues = audit_pdf(pdf, digest_path,
                           wide_factor=args.wide_factor,
                           min_overlap_pt2=args.min_overlap_pt2,
                           min_y_overlap_pt=args.min_y_overlap_pt,
                           check_wide=args.check_wide)
        if not issues:
            print(f"✓ {form_id}: clean")
            continue
        total_issues += len(issues)
        print(f"⚠ {form_id}: {len(issues)} issue(s)")
        for it in issues:
            if it["type"] == "overlap":
                print(f"  overlap p{it['page'] + 1}: "
                      f"{it['name_a']!r} {it['rect_a']} ↔ "
                      f"{it['name_b']!r} {it['rect_b']} "
                      f"(area={it['overlap_pt2']}pt²)")
            else:
                print(f"  wide p{it['page'] + 1}: "
                      f"{it['name']!r} {it['rect']} "
                      f"width={it['width']}pt (median={it['median']}pt)")
    print(f"\ntotal issues: {total_issues}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
