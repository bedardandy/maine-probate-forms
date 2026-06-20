#!/usr/bin/env python3
"""Snap single-line text widgets onto their printed blank (rule or underscore run).

Replicates the granular hand-alignment pattern: a fill field should sit on the
printed blank it answers -- left edge at the blank start (clear of any label),
right edge at the blank end, baseline just above the line. Blanks are detected
two ways: drawn horizontal rules, and runs of printed underscores in the text
layer (which most of these forms use). Dry-run by default.

    python3 tools/snap_to_blank.py --form AF-103            # dry-run report
    python3 tools/snap_to_blank.py --form AF-103 --apply
"""
from __future__ import annotations
import argparse, json, pathlib, re, sys
import fitz
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools")); from fetch import fetch_source  # noqa
sys.path.insert(0, str(ROOT / "scripts")); from audit_form_geometry import horizontal_rules  # noqa

US_RE = re.compile(r"^[_—….]{3,}$")  # underscores / em-dash / dots run


def blanks(page):
    """List of (x0, x1, y_line) printed blanks on the page."""
    out = []
    for x0, x1, y in horizontal_rules(page):
        out.append((x0, x1, y))
    # underscore-run words; merge adjacent ones on the same baseline
    words = sorted(page.get_text("words"), key=lambda w: (round(w[3], 0), w[0]))
    run = None
    for w in words:
        if US_RE.match(w[4].strip()) or set(w[4].strip()) <= set("_"):
            if run and abs(w[3] - run[3]) < 2 and w[0] - run[2] < 8:
                run = (run[0], w[1], max(run[2], w[2]), w[3])
            else:
                if run:
                    out.append((run[0], run[2], run[3]))
                run = (w[0], w[1], w[2], w[3])
        else:
            if run:
                out.append((run[0], run[2], run[3])); run = None
    if run:
        out.append((run[0], run[2], run[3]))
    return out


def snap(form_id, apply=False):
    pkg = ROOT / "repo" / "forms" / form_id
    geom = json.loads((pkg / "fill_geometry.json").read_text())
    schema = {f["field_id"]: f for f in json.loads((pkg / "schema.json").read_text())["fields"]}
    doc = fitz.open(str(fetch_source(form_id)))
    bl = {i: blanks(doc[i]) for i in range(doc.page_count)}
    # every other field's widget rects, per page -- to avoid snapping a field on
    # top of a neighbour (collisions the geometry audit gates on).
    others = {}
    for ofid, ospec in geom["fields"].items():
        for ow in ospec.get("widgets", []) or []:
            others.setdefault(ow["page"], []).append((ofid, ow["rect"]))

    def _collides(nr, page, self_fid):
        for ofid, orr in others.get(page, []):
            if ofid == self_fid:
                continue
            ix = min(nr[2], orr[2]) - max(nr[0], orr[0])
            iy = min(nr[3], orr[3]) - max(nr[1], orr[1])
            if ix > 2 and iy > 2:
                return True
        return False

    changes = []
    for fid, spec in geom["fields"].items():
        if (spec.get("options") or spec.get("type") == "enabler"
                or spec.get("geometry_source", "").startswith(("suppressed", "court"))):
            continue
        if schema.get(fid, {}).get("data_type") == "signature":
            continue
        # multi-row narrative blocks: rows are already spaced; snapping each
        # independently makes consecutive rows overlap. Leave them.
        if len(spec.get("widgets", []) or []) > 1:
            continue
        for w in spec.get("widgets", []) or []:
            r = w["rect"]; h = r[3] - r[1]
            if h > 20:
                continue
            cy = (r[1] + r[3]) / 2
            # candidate blanks: same row, meaningfully overlapping the widget x-span
            cands = []
            row_overlaps = 0  # distinct blanks underlying this widget on the row
            for x0, x1, y in bl[w["page"]]:
                if x1 - x0 < 20:
                    continue
                ov = min(r[2], x1) - max(r[0], x0)
                if abs(y - r[3]) < 9 and ov > 25:
                    row_overlaps += 1
                if abs(y - r[3]) < 9 and ov > 0.4 * min(r[2] - r[0], x1 - x0):
                    cands.append((abs(y - r[3]), x0, x1, y))
            if not cands:
                continue
            # multi-slot sentence (e.g. "on ___, ___, at ___"): one field spans
            # several inline blanks -- snapping would truncate it. Leave as-is.
            if row_overlaps > 1:
                continue
            _, bx0, bx1, by = min(cands)
            # don't aggressively shrink: a wide field collapsing onto a short
            # blank is usually a multi-slot sentence or narrative answer area
            # (e.g. "produce ... on ___, ___, at ___") -- leave for manual review.
            if (bx1 - bx0) < 0.75 * (r[2] - r[0]):
                continue
            y1 = by - 1.3
            new = [round(bx0, 1), round(y1 - h, 1), round(bx1, 1), round(y1, 1)]
            # right-aligned currency: keep left as-is (value flush right in column)
            if abs(new[0] - r[0]) < 1 and abs(new[2] - r[2]) < 1 and abs(new[3] - r[3]) < 1:
                continue
            # never snap a field onto a neighbour it didn't already overlap
            if _collides(new, w["page"], fid) and not _collides(r, w["page"], fid):
                continue
            changes.append((fid, w["page"], [round(c, 1) for c in r], new))
            if apply:
                w["rect"] = new
    doc.close()
    if apply:
        (pkg / "fill_geometry.json").write_text(json.dumps(geom, indent=1, ensure_ascii=False))
    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    for c in snap(a.form, a.apply):
        print(f"  {c[0]} p{c[1]} {c[2]} -> {c[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
