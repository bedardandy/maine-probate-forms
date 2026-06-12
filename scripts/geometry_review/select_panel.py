#!/usr/bin/env python3
"""Pick the units that go to the full voting panel.

The triage voter (fast local vLLM) sees everything; the slower panelists
only see units worth their time:
  - triage verdict minor/major (or triage errored), or
  - strong deterministic evidence regardless of triage
    (OCR token_missing/token_offset, analytic sits_below_line /
     off_square / starts_under_label), plus
  - a fixed random sample of clean controls (panel FP calibration).

Writes <out>/panel.keys (one unit key per line).

    python3 scripts/geometry_review/select_panel.py --out ~/geom-review-out
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from scripts.geometry_review.vl_vote import load_units  # noqa: E402

STRONG_ANALYTIC = {"sits_below_line", "off_square", "starts_under_label"}
STRONG_OCR = {"token_missing", "token_offset"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--triage-voter", default="local")
    ap.add_argument("--control-sample", type=int, default=40)
    args = ap.parse_args()
    random.seed(20260612)

    triage: dict[str, dict] = {}
    for line in (args.out / "votes.jsonl").open():
        o = json.loads(line)
        if o["voter"] == args.triage_voter:
            triage[o["key"]] = o

    keys: set[str] = set()
    units = load_units(args.out)
    for u in units:
        t = triage.get(u["key"], {})
        ev = u["evidence"]
        strong = (STRONG_ANALYTIC & set(ev.get("analytic", {}))
                  or ev.get("ocr", {}).get("ocr") in STRONG_OCR)
        if t.get("verdict") in ("minor", "major") or "error" in t or strong:
            keys.add(u["key"])

    ctrl = [f"CTRL|{json.loads(l)['form']}|{json.loads(l)['field']}|"
            f"{json.loads(l).get('widget_idx', json.loads(l).get('option'))}"
            for l in (args.out / "controls.jsonl").open()]
    keys.update(random.sample(ctrl, min(args.control_sample, len(ctrl))))

    (args.out / "panel.keys").write_text("\n".join(sorted(keys)) + "\n")
    print(f"panel: {len(keys)} units "
          f"({sum(1 for k in keys if k.startswith('CTRL'))} controls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
