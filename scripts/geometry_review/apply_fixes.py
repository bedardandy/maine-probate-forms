#!/usr/bin/env python3
"""Tier 3 of the geometry review: turn confirmed findings into rect fixes.

Only units the voting panel CONFIRMED (≥2 majors) get fixes, and only when a
deterministic correction can be computed from the source PDF itself:

  label_intrusion   the rect starts under printed label text → new x0 just
                    past the intruding words' right edge (the dominant class:
                    "Estate of", "Dated:", section headings)
  square_snap       checkbox rect recentered onto its printed square
  manual            everything else → left for the maintainer, with evidence

Writes <out>/fixes_proposed.tsv for review. With --apply, edits each form's
fill_geometry.json in place and appends an entry to <out>/fixes_applied.jsonl.
Always re-verify (scripts/verify_fill_geometry.py) and re-run the sweep on
the touched forms afterwards — the loop is done when the flags stop firing.

    python3 scripts/geometry_review/apply_fixes.py --out ~/geom-review-out
    python3 scripts/geometry_review/apply_fixes.py --out ~/geom-review-out --apply
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))     # fetch imports its sibling `verify`
from tools.fetch import fetch_source                       # noqa: E402
from scripts.geometry_review.sweep import page_features    # noqa: E402

MIN_REMAINING_W = 25.0
GAP = 3.0


def label_fix(rect: fitz.Rect, feats: dict) -> float | None:
    """New x0 just past printed words intruding at the rect's left edge."""
    worst = None
    for wr, t in feats["words"]:
        vert = min(rect.y1, wr.y1) - max(rect.y0, wr.y0)
        if vert <= 0.5 * wr.height:
            continue
        if wr.x0 < rect.x0 + 0.5 * rect.width and wr.x1 > rect.x0:
            worst = max(worst or 0, wr.x1)
    if worst is None:
        return None
    new_x0 = worst + GAP
    if rect.x1 - new_x0 < MIN_REMAINING_W:
        return None
    return round(new_x0, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    confirmed = [json.loads(l) for l in (args.out / "consensus.jsonl").open()
                 if json.loads(l)["status"] == "confirmed"]
    # adjudicated disputes that codex called major join the queue
    adj_p = args.out / "adjudications.jsonl"
    if adj_p.exists():
        adj = {json.loads(l)["key"]: json.loads(l) for l in adj_p.open()}
        for l in (args.out / "consensus.jsonl").open():
            o = json.loads(l)
            if o["status"] == "disputed" and \
                    adj.get(o["key"], {}).get("verdict") == "major":
                confirmed.append(o)

    feats_cache: dict[tuple, dict] = {}
    rows = []
    for c in confirmed:
        form, field = c["form"], c["field"]
        parts = c["key"].split("|")
        widx = parts[-1]
        rect = fitz.Rect(c["rect"])
        fk = (form, c["page"])
        if fk not in feats_cache:
            doc = fitz.open(str(fetch_source(form)))
            feats_cache[fk] = page_features(doc[c["page"]])
        feats = feats_cache[fk]
        ev = c["evidence"]
        analytic = ev.get("analytic", {}) or {}
        fix, new_rect = "manual", None
        if c.get("kind") == "checkbox" and "off_square" in analytic:
            dx, dy = analytic["off_square"]
            r2 = fitz.Rect(rect) + (dx, dy, dx, dy)
            fix, new_rect = "square_snap", [round(v, 1) for v in r2]
        elif "starts_under_label" in analytic or "print_overlap" in analytic:
            nx0 = label_fix(rect, feats)
            if nx0 is not None and nx0 > rect.x0 + 1:
                fix = "label_intrusion"
                new_rect = [nx0, round(rect.y0, 1),
                            round(rect.x1, 1), round(rect.y1, 1)]
        rows.append({"form": form, "field": field, "widget_idx": widx,
                     "kind": c.get("kind"), "page": c["page"],
                     "fix": fix, "old_rect": c["rect"],
                     "new_rect": new_rect,
                     "evidence": json.dumps(ev)[:160]})

    tsv = args.out / "fixes_proposed.tsv"
    with tsv.open("w") as fh:
        fh.write("form\tfield\twidget_idx\tfix\told_rect\tnew_rect\tevidence\n")
        for r in rows:
            fh.write("\t".join(str(r[k]) for k in
                               ("form", "field", "widget_idx", "fix",
                                "old_rect", "new_rect", "evidence")) + "\n")
    fixable = [r for r in rows if r["new_rect"]]
    print(f"{len(rows)} confirmed: {len(fixable)} auto-fixable, "
          f"{len(rows) - len(fixable)} manual — {tsv}")

    if not args.apply:
        return 0

    applied_f = (args.out / "fixes_applied.jsonl").open("a")
    by_form: dict[str, list] = {}
    for r in fixable:
        by_form.setdefault(r["form"], []).append(r)
    for form, frs in by_form.items():
        gp = ROOT / "repo" / "forms" / form / "fill_geometry.json"
        g = json.loads(gp.read_text())
        n = 0
        for r in frs:
            spec = g["fields"].get(r["field"])
            if not spec:
                continue
            tgt = None
            if r["kind"] == "checkbox":
                for o in spec.get("options") or []:
                    if str(o.get("value")) == str(r["widget_idx"]):
                        tgt = o
            else:
                ws = spec.get("widgets") or []
                i = int(r["widget_idx"])
                if i < len(ws):
                    tgt = ws[i]
            if tgt is None or [round(v, 1) for v in tgt["rect"]] != \
                    [round(v, 1) for v in fitz.Rect(r["old_rect"])]:
                continue
            tgt["rect"] = r["new_rect"]
            n += 1
            applied_f.write(json.dumps(r) + "\n")
        gp.write_text(json.dumps(g, indent=1))
        print(f"{form}: {n} rect(s) fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
