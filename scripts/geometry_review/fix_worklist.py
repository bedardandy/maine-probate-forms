#!/usr/bin/env python3
"""Targeted deterministic fixes for the geometry-review manual worklist.

apply_fixes.py handles the dominant left-label-intrusion class (shift x0 past
the printed label). The manual worklist holds the units it could not safely
auto-fix. Two well-understood sub-classes here get a deterministic correction;
everything else stays for human review.

  county_trim   the Maine probate caption "____COUNTY PROBATE COURT": the
                county-name rect overruns the printed word "COUNTY" on its
                RIGHT. Trim x1 to just before that word (value is left-aligned,
                so shrink-to-fit then keeps it on the underscores). Applied
                only when >= MIN_TRIM_REMAINING pt of blank remains.
  tight_label   a left-label intrusion apply_fixes skipped because the
                remaining width fell under its 25pt floor, but the field is
                right-aligned (a short dollar amount) or a short-value blank
                (a notary day) where a narrow box is fine.

Each change is checked against the live geometry rect before writing and
logged to <out>/worklist_fixes_applied.jsonl. Re-sweep the touched forms
afterwards to confirm the flags clear.

    python3 scripts/geometry_review/fix_worklist.py --out ~/geom-review-out
    python3 scripts/geometry_review/fix_worklist.py --out ~/geom-review-out --apply
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools.fetch import fetch_source                       # noqa: E402
from tools.fill_pdf import _load_alignment                 # noqa: E402
from scripts.geometry_review.sweep import page_features     # noqa: E402

GAP = 3.0
MIN_TRIM_REMAINING = 40.0      # county_trim: keep at least this much blank
MIN_RIGHT_REMAINING = 15.0     # tight_label, right-aligned currency
MIN_DAY_REMAINING = 18.0       # tight_label, short-value left blank
SHORT_VALUE_FIELDS = ("day", "year", "_no", "number")  # narrow blanks ok


def county_word(rect: fitz.Rect, feats: dict) -> fitz.Rect | None:
    """A printed COUNTY/County token overrunning the rect on its right half."""
    best = None
    for wr, t in feats["words"]:
        if t.strip().upper().rstrip(":.,") != "COUNTY":
            continue
        vert = min(rect.y1, wr.y1) - max(rect.y0, wr.y0)
        if vert <= 0.5 * wr.height:
            continue
        if wr.x0 < rect.x0 + 0.3 * rect.width:      # must be on the right
            continue
        if (rect & wr).get_area() <= 0:             # must actually overlap
            continue
        if best is None or wr.x0 < best.x0:
            best = wr
    return best


def left_label_x1(rect: fitz.Rect, feats: dict) -> float | None:
    worst = None
    for wr, t in feats["words"]:
        vert = min(rect.y1, wr.y1) - max(rect.y0, wr.y0)
        if vert <= 0.5 * wr.height:
            continue
        if wr.x0 < rect.x0 + 0.5 * rect.width and wr.x1 > rect.x0:
            worst = max(worst or 0, wr.x1)
    return worst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rows = [r for r in csv.DictReader(
        (args.out / "manual_worklist.tsv").open()
        if (args.out / "manual_worklist.tsv").exists()
        else (ROOT / "catalog" / "geometry_review_worklist.tsv").open(),
        delimiter="\t") if r["class"] == "manual_review"]

    by_form: dict[str, list] = {}
    for r in rows:
        by_form.setdefault(r["form"], []).append(r)

    applied_f = (args.out / "worklist_fixes_applied.jsonl").open("a") \
        if args.apply else None
    planned = []
    for form, frs in sorted(by_form.items()):
        gp = ROOT / "repo" / "forms" / form / "fill_geometry.json"
        g = json.loads(gp.read_text())
        doc = fitz.open(str(fetch_source(form)))
        align = _load_alignment(form, ROOT)
        feats = {p: page_features(doc[p]) for p in range(doc.page_count)}
        changed = 0
        for r in frs:
            field, widx = r["field"], r["widget_idx"]
            spec = g["fields"].get(field)
            if not spec or not spec.get("widgets"):
                continue
            i = int(widx) if str(widx).isdigit() else 0
            ws = spec["widgets"]
            if i >= len(ws):
                continue
            rect = fitz.Rect(ws[i]["rect"])
            al = align.get(field, "left")
            ft = feats[ws[i]["page"]]
            fix = new_rect = None
            cw = county_word(rect, ft)
            if cw is not None:
                nx1 = cw.x0 - GAP
                if nx1 - rect.x0 >= MIN_TRIM_REMAINING and nx1 < rect.x1 - 1:
                    fix = "county_trim"
                    new_rect = [round(rect.x0, 1), round(rect.y0, 1),
                                round(nx1, 1), round(rect.y1, 1)]
            if fix is None:
                lx1 = left_label_x1(rect, ft)
                if lx1 is not None and lx1 > rect.x0 + 1:
                    nx0 = lx1 + GAP
                    remain = rect.x1 - nx0
                    short = any(s in field for s in SHORT_VALUE_FIELDS)
                    ok = (al == "right" and remain >= MIN_RIGHT_REMAINING) or \
                         (short and remain >= MIN_DAY_REMAINING)
                    if ok:
                        fix = "tight_label"
                        new_rect = [round(nx0, 1), round(rect.y0, 1),
                                    round(rect.x1, 1), round(rect.y1, 1)]
            planned.append({"form": form, "field": field, "widget_idx": widx,
                            "fix": fix or "skip", "old_rect": list(rect),
                            "new_rect": new_rect, "align": al})
            if fix and args.apply:
                ws[i]["rect"] = new_rect
                changed += 1
                applied_f.write(json.dumps(planned[-1]) + "\n")
        if args.apply and changed:
            gp.write_text(json.dumps(g, indent=1))
            print(f"{form}: {changed} rect(s) fixed")
        doc.close()

    n = {"county_trim": 0, "tight_label": 0, "skip": 0}
    for p in planned:
        n[p["fix"]] = n.get(p["fix"], 0) + 1
    print(f"planned: {n}")
    if not args.apply:
        for p in planned:
            if p["fix"] != "skip":
                print(f"  {p['fix']:12} {p['form']:10} {p['field'][:28]:28} "
                      f"{p['old_rect']} -> {p['new_rect']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
