"""Run synthetic cases through the full event-chain pipeline.

For each synthetic case in router/synthetic_cases.jsonl:
  1. expand_chain() → N events with state rolled forward
  2. run_case() each event → routed + filled + validated
  3. Aggregate metrics per case_type

This is the end-to-end "simulate a real probate calendar" loop: a case
is generated, has a 2-year lifecycle, every event triggers a routed
form, every form is filled by Qwen and validated.

Outputs router/chain_batch_report.tsv (one row per event) plus a per-
case-type rollup.

Usage:
  python3 -m router.run_chain_batch
  python3 -m router.run_chain_batch --max-cases 3
  python3 -m router.run_chain_batch --dry-run   # route only, skip fills
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import pathlib
import sys
import threading
import time

from router.case_chain import expand_chain, LIFECYCLE_TEMPLATES
from router.generate_case import _normalize_case
from router.router import Router
from router.run_case import run_case, run_case_multi
from router.schemas import from_dict_case, from_dict_event


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_IN = REPO_ROOT / "router" / "synthetic_cases.jsonl"
DEFAULT_OUT = REPO_ROOT / "router" / "chain_batch_report.tsv"


def _row(case_id: str, case_type: str, event_idx: int, total_events: int,
         event_type: str, event_date: str, form_id: str | None,
         confidence: float | None, errors: int | None, status: str,
         elapsed: float) -> dict:
    return {
        "case_id": case_id, "case_type": case_type,
        "event_idx": event_idx, "total_events": total_events,
        "event_type": event_type, "event_date": event_date,
        "form_id": form_id or "", "confidence": confidence,
        "errors": errors if errors is not None else "",
        "status": status, "elapsed_sec": round(elapsed, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=pathlib.Path, default=DEFAULT_IN)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--max-cases", type=int, default=0,
                    help="Cap number of cases (0 = all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Route only; skip Qwen fill + validate")
    ap.add_argument("--parallel", type=int, default=1,
                    help="Concurrent fills against Qwen (default 1). "
                         "Events are fill-independent, so 2-4 typically "
                         "halves wall time. Watch Qwen for OOM at higher N.")
    ap.add_argument("--multi-threshold", type=float, default=0.0,
                    help="If >0, fill every routed candidate whose "
                         "confidence ≥ threshold instead of just top-1. "
                         "Reports one row per (case, event, form). "
                         "Typical: 0.7. Use 0 for top-1 only.")
    ap.add_argument("--url", default="http://localhost:8088")
    ap.add_argument("--model", default="Qwen3.6-27B-FP8")
    args = ap.parse_args()

    cases = []
    for line in args.cases.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(json.loads(line))
    if args.max_cases:
        cases = cases[: args.max_cases]

    if not cases:
        print("no cases in", args.cases, file=sys.stderr)
        return 1

    print(f"Running {len(cases)} synthetic case(s) through chain pipeline"
          + (" [dry-run]" if args.dry_run else "")
          + (f" [parallel={args.parallel}]" if args.parallel > 1 else ""))
    rows: list[dict] = []
    per_type: dict[str, dict] = collections.defaultdict(
        lambda: {"events": 0, "filled": 0, "clean": 0, "fail": 0})

    router_v1 = Router()
    print_lock = threading.Lock()

    # ── 1. Expand all chains into a flat (ci, ei, ...) work list ───────
    work: list[dict] = []
    for ci, c in enumerate(cases, 1):
        ct = c.get("case", {}).get("case_type", "?")
        c = _normalize_case(c, ct)
        cid = c.get("case", {}).get("case_id", f"synth-{ci}")

        if ct not in LIFECYCLE_TEMPLATES:
            print(f"  [{ci}/{len(cases)}] {cid} ({ct}): no template — skipping")
            rows.append(_row(cid, ct, 0, 0, "-", "-", None, None, None,
                             "no_template", 0))
            continue

        try:
            chain = expand_chain(c)
        except Exception as e:
            print(f"  [{ci}/{len(cases)}] {cid} ({ct}): expand failed — {e}")
            rows.append(_row(cid, ct, 0, 0, "-", "-", None, None, None,
                             "expand_failed", 0))
            continue

        for ei, step in enumerate(chain, 1):
            work.append({"ci": ci, "ei": ei, "step": step, "ct": ct,
                         "cid": cid, "chain_len": len(chain)})

    print(f"Total events: {len(work)}")

    # ── 2. Per-event handler (callable in thread pool) ─────────────────
    # Returns a list of rows: 1 row in single-form mode, N rows when
    # multi_threshold fires multiple candidates for one event.
    def _process(w: dict) -> list[dict]:
        ci, ei = w["ci"], w["ei"]
        step = w["step"]
        ct = w["ct"]
        cid = w["cid"]
        ev = step["event"]
        t0 = time.time()

        if args.dry_run:
            case_obj = from_dict_case(step["case"])
            event_obj = from_dict_event(step["event"])
            if args.multi_threshold > 0:
                cands = [c for c in router_v1.route(case_obj, event_obj, top_k=10)
                         if c.confidence >= args.multi_threshold] or \
                        router_v1.route(case_obj, event_obj, top_k=1)
            else:
                cands = router_v1.route(case_obj, event_obj, top_k=1)
            if not cands:
                with print_lock:
                    print(f"  [c{ci}.ev{ei}] {ev['type']:30s} {ev['date']} "
                          f"→ (none)")
                return [_row(cid, ct, ei, w["chain_len"], ev["type"],
                             ev["date"], None, None, None,
                             "no_candidates", time.time() - t0)]
            out_rows = []
            for cand in cands:
                with print_lock:
                    print(f"  [c{ci}.ev{ei}] {ev['type']:30s} {ev['date']} "
                          f"→ {cand.form_id} conf={cand.confidence}")
                out_rows.append(_row(cid, ct, ei, w["chain_len"], ev["type"],
                                     ev["date"], cand.form_id, cand.confidence,
                                     None, "routed", time.time() - t0))
            return out_rows

        tag = f"e{ei}_{ev['type']}"
        try:
            if args.multi_threshold > 0:
                results = run_case_multi(
                    step, threshold=args.multi_threshold,
                    qwen_url=args.url, qwen_model=args.model, tag=tag)
            else:
                results = [run_case(step, qwen_url=args.url,
                                    qwen_model=args.model, tag=tag)]
        except Exception as e:
            with print_lock:
                print(f"  [c{ci}.ev{ei}] EXCEPTION {e}")
            return [_row(cid, ct, ei, w["chain_len"], ev["type"],
                         ev["date"], None, None, None,
                         f"exception:{e}", time.time() - t0)]

        dt = time.time() - t0
        rows_out: list[dict] = []
        for result in results:
            fid = result.get("form_id", "-")
            errs = result.get("errors", -1)
            status = result.get("status", "?")
            with print_lock:
                print(f"  [c{ci}.ev{ei}] {ev['type']:30s} → {fid} "
                      f"errors={errs} ({dt:.0f}s, {status})")
            rows_out.append(_row(cid, ct, ei, w["chain_len"], ev["type"],
                                 ev["date"], fid, result.get("confidence"),
                                 errs, status, dt))
        return rows_out

    # ── 3. Run sequentially or fan out via thread pool ─────────────────
    if args.parallel <= 1 or args.dry_run:
        for w in work:
            rows.extend(_process(w))
    else:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=args.parallel) as ex:
            futures = [ex.submit(_process, w) for w in work]
            for fut in concurrent.futures.as_completed(futures):
                rows.extend(fut.result())

    # ── 4. Per-type rollup ─────────────────────────────────────────────
    for r in rows:
        ct = r.get("case_type", "?")
        st = r.get("status", "")
        if st == "no_template" or st == "expand_failed":
            continue
        per_type[ct]["events"] += 1
        if st == "ok":
            per_type[ct]["filled"] += 1
            if r.get("errors") == 0:
                per_type[ct]["clean"] += 1
        elif st not in ("routed", "dry_run"):
            per_type[ct]["fail"] += 1

    # Sort rows by (case_id, event_idx) so the TSV is deterministic
    # regardless of thread-pool completion order.
    rows.sort(key=lambda r: (r.get("case_id") or "", r.get("event_idx") or 0))

    cols = ["case_id", "case_type", "event_idx", "total_events",
            "event_type", "event_date", "form_id", "confidence",
            "errors", "status", "elapsed_sec"]
    args.out.write_text(
        "\t".join(cols) + "\n"
        + "\n".join("\t".join(str(r.get(k, "")) for k in cols) for r in rows)
        + "\n")
    print(f"\nWrote {args.out}")

    print("\n=== rollup ===")
    print(f"{'case_type':22s} {'events':>7} {'filled':>7} "
          f"{'clean':>6} {'fail':>5}")
    for ct, m in sorted(per_type.items()):
        print(f"{ct:22s} {m['events']:>7} {m['filled']:>7} "
              f"{m['clean']:>6} {m['fail']:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
