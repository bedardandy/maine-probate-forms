#!/usr/bin/env python3
"""Measure, per text field, how its filled value sits relative to its printed rule.

The "distance of text above the underline" is an exact geometric quantity, not a
thing to eyeball thousands of times. For every single-line text field that sits
on a printed blank we fill a fixed descender-bearing token at the field's nominal
font size (the *largest* font it will ever use -- longer real values only shrink,
so this is the worst case for descender clearance), flatten it to real text, and
read the glyphs' true baseline and descender-bottom y against the rule y.

Reported per field:
  fs            nominal font size used (min(10, height-2), maybe width-shrunk)
  rule_y        y of the printed rule the field sits on
  baseline      text baseline y
  desc_bottom   y of the lowest ink (descender bottom)
  gap_base      rule_y - baseline      (baseline above rule)
  desc_clear    rule_y - desc_bottom   (descender clearance; <0 means crossing)

  python3 tools/measure_baseline.py --form DE-201
  python3 tools/measure_baseline.py --all --csv /tmp/baseline.csv
  python3 tools/measure_baseline.py --all --apply --target 0.6   # normalize
"""
from __future__ import annotations
import argparse, json, pathlib, sys
import fitz
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa
from fill_pdf import _ALIGN_CONST, _add_text, _load_alignment, _strip_widgets  # noqa
from snap_to_blank import blanks, SKIP_FIELDS  # noqa

MEAS = "gy"             # shortest descender-bearing token: always renders at the
                        # field's max font size (no width shrink) -> worst case


def _rule_for(rect, page_blanks):
    """The printed rule a single-line field sits on: nearest blank just below the
    field's bottom edge that overlaps its x-span. None if the field isn't on one."""
    r = rect
    best = None
    for x0, x1, y in page_blanks:
        if x1 - x0 < 20:
            continue
        ov = min(r[2], x1) - max(r[0], x0)
        if abs(y - r[3]) < 9 and ov > 0.4 * min(r[2] - r[0], x1 - x0):
            d = abs(y - r[3])
            if best is None or d < best[0]:
                best = (d, y)
    return best[1] if best else None


def measure(form_id, apply=False, target=0.6):
    pkg = ROOT / "repo" / "forms" / form_id
    geom = json.loads((pkg / "fill_geometry.json").read_text())
    schema = {f["field_id"]: f for f in
              json.loads((pkg / "schema.json").read_text())["fields"]}
    src = fetch_source(form_id)
    doc = fitz.open(str(src))
    _strip_widgets(doc)
    align = _load_alignment(form_id, ROOT)
    page_blanks = {i: blanks(doc[i]) for i in range(doc.page_count)}

    # candidate widgets: single-line text on a printed rule (same gate as snap)
    cand = []  # (fid, widget, rule_y)
    for fid, spec in geom["fields"].items():
        if (spec.get("options") or spec.get("type") == "enabler"
                or spec.get("geometry_source", "").startswith(("suppressed", "court"))
                or schema.get(fid, {}).get("data_type") == "signature"
                or (form_id, fid) in SKIP_FIELDS):
            continue
        for w in spec.get("widgets", []) or []:
            r = w["rect"]
            if r[3] - r[1] > 20:
                continue
            ry = _rule_for(r, page_blanks[w["page"]])
            if ry is None:
                continue
            cand.append((fid, w, ry))
            _add_text(doc[w["page"]], r, f"__m_{fid}", MEAS,
                      _ALIGN_CONST.get(align.get(fid)))

    doc.bake(widgets=True)
    # collect baked spans per page
    spans = {i: [] for i in range(doc.page_count)}
    for i in range(doc.page_count):
        for blk in doc[i].get_text("dict")["blocks"]:
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    if MEAS in sp["text"]:
                        spans[i].append(sp)

    rows = []
    for fid, w, ry in cand:
        r = w["rect"]; cy = (r[1] + r[3]) / 2
        sp = None
        for s in spans[w["page"]]:
            bb = s["bbox"]
            if bb[0] >= r[0] - 4 and bb[0] <= r[2] and abs(s["origin"][1] - cy) < 9:
                if sp is None or abs(s["origin"][1] - cy) < abs(sp["origin"][1] - cy):
                    sp = s
        if sp is None:
            continue
        baseline = sp["origin"][1]; desc = sp["bbox"][3]; fs = sp["size"]
        rows.append({
            "form": form_id, "field": fid, "page": w["page"], "fs": round(fs, 2),
            "rule_y": round(ry, 2), "baseline": round(baseline, 2),
            "desc_bottom": round(desc, 2), "gap_base": round(ry - baseline, 2),
            "desc_clear": round(ry - desc, 2),
            "_w": w, "_shift": round((ry - target) - desc, 2),
        })
    doc.close()

    if apply and rows:
        for row in rows:
            dy = row["_shift"]
            if abs(dy) < 0.1:
                continue
            r = row["_w"]["rect"]
            row["_w"]["rect"] = [r[0], round(r[1] + dy, 1), r[2], round(r[3] + dy, 1)]
        (pkg / "fill_geometry.json").write_text(
            json.dumps(geom, indent=1, ensure_ascii=False))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--target", type=float, default=0.6,
                    help="desired descender clearance above the rule (pt)")
    ap.add_argument("--csv")
    a = ap.parse_args()
    forms = (sorted(p.parent.name for p in
                    (ROOT / "repo" / "forms").glob("*/fill_geometry.json"))
             if a.all else [a.form])
    allrows = []
    for f in forms:
        try:
            allrows += measure(f, a.apply, a.target)
        except Exception as exc:
            print(f"SKIP {f}: {exc}")
    if a.csv:
        import csv
        keys = ["form", "field", "page", "fs", "rule_y", "baseline",
                "desc_bottom", "gap_base", "desc_clear"]
        with open(a.csv, "w", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(allrows)
        print(f"wrote {a.csv} ({len(allrows)} fields)")
    # distribution summary
    if allrows:
        dc = sorted(r["desc_clear"] for r in allrows)
        n = len(dc)
        def pct(p): return dc[min(n - 1, int(p * n))]
        print(f"fields={n}  desc_clear  min={dc[0]}  p10={pct(.1)}  "
              f"median={pct(.5)}  p90={pct(.9)}  max={dc[-1]}")
        crossing = [r for r in allrows if r["desc_clear"] < 0]
        print(f"descenders crossing the rule: {len(crossing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
