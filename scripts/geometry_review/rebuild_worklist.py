#!/usr/bin/env python3
"""Rebuild the human-review worklist from the post-fix verification state.

A unit lands on the worklist when it was a real defect (vision-confirmed, or
codex adjudicated 'major') AND it still flags after every automated fix has
been applied — i.e. a re-sweep (--verify-dir) of the touched forms still
shows an analytic flag or a hard OCR signal (token_missing / token_offset)
on it. Benign residuals (no_line_support alone on a total, a lone ';'
overlap) are filtered so the list stays actionable.

    python3 scripts/geometry_review/rebuild_worklist.py \
        --out ~/geom-review-out --verify-dir ~/geom-review-r3 \
        --write catalog/geometry_review_worklist.tsv
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


def real_defect_keys(out: pathlib.Path) -> dict:
    """key -> reason for every unit that was a real defect."""
    keys = {}
    for l in (out / "consensus.jsonl").open():
        o = json.loads(l)
        if o["status"] == "confirmed":
            keys[o["key"]] = {"form": o["form"], "field": o["field"],
                              "page": o["page"], "source": "vision_confirmed"}
    adj = out / "adjudications.jsonl"
    if adj.exists():
        cons = {json.loads(l)["key"]: json.loads(l)
                for l in (out / "consensus.jsonl").open()}
        for l in adj.open():
            o = json.loads(l)
            if o.get("verdict") == "major" and o["key"] in cons:
                c = cons[o["key"]]
                keys[o["key"]] = {"form": c["form"], "field": c["field"],
                                  "page": c["page"], "source": "codex_major"}
    return keys


def still_flagged(verify: pathlib.Path) -> dict:
    """key -> residual flag detail in the post-fix re-sweep."""
    res = {}
    for l in (verify / "candidates.jsonl").open():
        o = json.loads(l)
        fl = o["flags"]
        # benign-alone residuals: a total with no underline, a lone punctuation
        if set(fl) == {"no_line_support"}:
            continue
        if set(fl) == {"print_overlap"} and all(
                len(t.strip(".,;:")) <= 1 for t in fl["print_overlap"]):
            continue
        k = (o["form"], o["field"], str(o.get("widget_idx", o.get("option"))))
        res[k] = ("analytic", json.dumps(fl))
    ocr = verify / "ocr_results.jsonl"
    if ocr.exists():
        for l in ocr.open():
            o = json.loads(l)
            if o["ocr"] not in ("token_missing", "token_offset"):
                continue
            k = (o["form"], o["field"], str(o.get("widget_idx")))
            res.setdefault(k, ("ocr", o["ocr"]))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--verify-dir", type=pathlib.Path, required=True)
    ap.add_argument("--write", type=pathlib.Path)
    args = ap.parse_args()

    real = real_defect_keys(args.out)
    flagged = still_flagged(args.verify_dir)

    rows = []
    for key, meta in real.items():
        k3 = (meta["form"], meta["field"],
              key.split("|")[-1] if not key.startswith("CTRL") else "0")
        if k3 in flagged:
            kind, detail = flagged[k3]
            rows.append({"form": meta["form"], "field": meta["field"],
                         "widget_idx": k3[2],
                         "class": "needs_review", "via": meta["source"],
                         "signal": kind, "detail": detail})
    rows.sort(key=lambda r: (r["form"], r["field"]))
    print(f"worklist: {len(rows)} units still flagged after fixes "
          f"(of {len(real)} confirmed-real defects)")
    if args.write:
        with args.write.open("w") as fh:
            fh.write("form\tfield\twidget_idx\tclass\tvia\tsignal\tdetail\n")
            for r in rows:
                fh.write("\t".join(str(r[k]) for k in
                         ("form", "field", "widget_idx", "class", "via",
                          "signal", "detail")) + "\n")
        print(f"wrote {args.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
