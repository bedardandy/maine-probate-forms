"""Run all cases in router/synthetic_cases.jsonl through the router +
fill + validate pipeline. Aggregate validation rate per case_type.

Usage:
  python3 -m router.run_synthetic_batch
  python3 -m router.run_synthetic_batch --max 5     # cap for fast iter

For each synthetic case we record:
  - chosen form_id and confidence
  - validation error count on the filled form
  - runner-up form_id (so disagreement rate can be measured later)

Outputs router/synthetic_batch_report.tsv plus a per-case-type rollup.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time

from router.generate_case import _normalize_case
from router.run_case import run_case


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_IN = REPO_ROOT / "router" / "synthetic_cases.jsonl"
DEFAULT_OUT = REPO_ROOT / "router" / "synthetic_batch_report.tsv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=pathlib.Path, default=DEFAULT_IN)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--max", type=int, default=0,
                    help="Cap number of cases (0 = all)")
    ap.add_argument("--url", default="http://localhost:8088")
    ap.add_argument("--model", default="Qwen3.6-27B-FP8")
    args = ap.parse_args()

    cases = []
    for line in args.cases.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(json.loads(line))
    if args.max:
        cases = cases[:args.max]

    print(f"Running {len(cases)} synthetic case(s)")
    rows: list[dict] = []
    per_type_errors = collections.defaultdict(list)
    per_type_chose = collections.defaultdict(list)

    for i, c in enumerate(cases, 1):
        # Defensive: re-normalize at read time so cases written before
        # later normalizer updates still load cleanly.
        ct = c.get("case", {}).get("case_type", "?")
        c = _normalize_case(c, ct)
        cid = c.get("case", {}).get("case_id", f"synth-{i}")
        t0 = time.time()
        try:
            result = run_case(c, qwen_url=args.url, qwen_model=args.model)
        except Exception as e:
            print(f"  [{i}/{len(cases)}] {cid} ({ct}): EXCEPTION {e}")
            rows.append({"case_id": cid, "case_type": ct,
                         "status": "exception", "error": str(e)})
            continue
        dt = time.time() - t0
        status = result.get("status", "?")
        fid = result.get("form_id", "-")
        errs = result.get("errors", -1)
        print(f"  [{i}/{len(cases)}] {cid} ({ct}) → {fid} "
              f"errors={errs} status={status} {dt:.0f}s")
        rows.append({
            "case_id": cid, "case_type": ct, "status": status,
            "form_id": fid, "confidence": result.get("confidence"),
            "errors": errs, "elapsed_sec": round(dt, 1),
        })
        if status == "ok":
            per_type_errors[ct].append(errs)
            per_type_chose[ct].append(fid)

    args.out.write_text(
        "case_id\tcase_type\tstatus\tform_id\tconfidence\terrors\telapsed_sec\n"
        + "\n".join(
            "\t".join(str(r.get(k, "")) for k in
                      ("case_id", "case_type", "status", "form_id",
                       "confidence", "errors", "elapsed_sec"))
            for r in rows)
        + "\n")
    print(f"\nWrote {args.out}")

    # Rollup
    print("\n=== rollup ===")
    print(f"{'case_type':22s} {'n':>3} {'avg_err':>8} {'clean_rate':>11}  "
          f"forms_chosen")
    for ct, errs in sorted(per_type_errors.items()):
        n = len(errs)
        avg = sum(errs) / n if n else 0
        clean = sum(1 for e in errs if e == 0) / n if n else 0
        forms = collections.Counter(per_type_chose[ct]).most_common()
        forms_str = ",".join(f"{f}×{c}" for f, c in forms)
        print(f"{ct:22s} {n:>3} {avg:>8.2f} {clean:>10.0%}  {forms_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
