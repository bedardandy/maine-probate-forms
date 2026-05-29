#!/usr/bin/env python3
"""Report current error counts across all pipeline stages.

Stages tracked, in order of application:
  v2          : baseline (filled_1.json)
  v3          : v3 prompt (filled_1.v3.json) — adds CRITICAL enum rule
  v3+canon    : canonicalize_enums applied (filled_1.v3.canon.json)
  v3+canon+gate: + infer_gates (filled_1.v3.canon.gated.json)
  v4          : v4 prompt (filled_1.v4.json) — adds chunk-aware recap; canonicalizer is baked into fill_form.py so v4 already includes canon
  v4+gate     : + infer_gates (filled_1.v4.gated.json)

Skips stages where no file exists yet.

Usage:
  python3 scripts/report_pipeline_state.py
"""
import json
import os
import glob
import subprocess
import sys
from pathlib import Path


STAGES = [
    ("v2",            "filled_1.json"),
    ("v3",            "filled_1.v3.json"),
    ("v3+canon",      "filled_1.v3.canon.json"),
    ("v3+canon+gate", "filled_1.v3.canon.gated.json"),
    ("v4",            "filled_1.v4.json"),
    ("v4+gate",       "filled_1.v4.gated.json"),
    ("v4+fixed",      "filled_1.v4.fixed.json"),
]


def errs(schema_path, filled_path):
    if not os.path.exists(filled_path):
        return None
    out = subprocess.run(
        ["python3", "scripts/validate_filled.py",
         "--schema", schema_path, "--filled", filled_path],
        capture_output=True, text=True).stdout
    return sum(1 for l in out.splitlines() if "[error]" in l)


def main():
    totals = {s[0]: 0 for s in STAGES}
    counts = {s[0]: 0 for s in STAGES}
    bottlenecks = {}
    for sch_path in sorted(glob.glob("repo/forms/*/schema.json")):
        fid = sch_path.split("/")[-2]
        base = f"intermediate/fact_eval/{fid}"
        if not os.path.exists(f"{base}/filled_1.json"):
            continue
        for label, fname in STAGES:
            n = errs(sch_path, f"{base}/{fname}")
            if n is None:
                continue
            totals[label] += n
            counts[label] += 1
            if label == STAGES[-1][0] and n > 0:
                bottlenecks[fid] = n

    print(f"{'stage':22s} {'forms':>5} {'errors':>7}")
    print("-" * 42)
    for label, _ in STAGES:
        if counts[label] == 0:
            continue
        print(f"{label:22s} {counts[label]:>5} {totals[label]:>7}")

    if bottlenecks:
        print(f"\nForms with errors at deepest stage:")
        for f, n in sorted(bottlenecks.items(), key=lambda x: -x[1]):
            print(f"  {f}: {n}")


if __name__ == "__main__":
    sys.exit(main())
