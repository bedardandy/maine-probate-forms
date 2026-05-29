#!/usr/bin/env python3
"""Side-by-side reporter: Qwen v2 vs Opus across all forms.

Consumes:
  intermediate/fact_eval/<form>/eval_{1..5}.yaml         (Qwen v2)
  intermediate/fact_eval/<form>/eval_{1..5}.opus.yaml    (Opus)

Emits:
  intermediate/fact_eval/COMPARE.md   markdown report
  intermediate/fact_eval/COMPARE.tsv  per-form TSV

Idempotent + safe on partial data — forms missing one side are reported
as such; aggregates exclude them from head-to-head numbers.
"""
from __future__ import annotations
import argparse
import pathlib
import sys
from collections import defaultdict

import yaml


PATTERN_COMPLEXITY = {1: "complete", 2: "partial", 3: "partial",
                      4: "edge_case", 5: "sparse"}


def _load_eval(path: pathlib.Path) -> dict | None:
    if not path.exists():
        return None
    try:
        d = yaml.safe_load(path.read_text())
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return None


def _summary_counts(d: dict | None) -> dict[str, int]:
    if not isinstance(d, dict):
        return {}
    s = d.get("summary") or {}
    out = {}
    for k in ("total_fields", "matches", "partial", "wrong",
             "not_applicable", "well_calibrated",
             "overconfident", "underconfident", "comprehension_issues"):
        v = s.get(k, 0) or 0
        try:
            out[k] = int(v)
        except (TypeError, ValueError):
            out[k] = 0
    return out


def _agg_form(form_dir: pathlib.Path, suffix: str) -> dict:
    """Sum counts across all eval files for one form."""
    agg = defaultdict(int)
    seen = 0
    for pid in range(1, 6):
        ev = _load_eval(form_dir / f"eval_{pid}{suffix}.yaml")
        if not ev:
            continue
        seen += 1
        for k, v in _summary_counts(ev).items():
            agg[k] += v
    agg["_patterns"] = seen
    return dict(agg)


def _pct(numer: int, denom: int) -> str:
    if denom <= 0:
        return "  —"
    return f"{100*numer/denom:5.1f}%"


def _verdict(qw: dict, op: dict) -> str:
    qw_p = qw.get("_patterns", 0)
    op_p = op.get("_patterns", 0)
    if not qw_p and not op_p:
        return "pending"
    if not qw_p or not op_p:
        return "partial"
    qw_w = qw.get("wrong", 0)
    op_w = op.get("wrong", 0)
    qw_oc = qw.get("overconfident", 0)
    op_oc = op.get("overconfident", 0)
    score_qw = qw_w + 0.5 * qw_oc
    score_op = op_w + 0.5 * op_oc
    if abs(score_qw - score_op) < 0.6:
        return "tie"
    return "Opus" if score_op < score_qw else "Qwen"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="intermediate/fact_eval",
                    type=pathlib.Path)
    ap.add_argument("--md-out", type=pathlib.Path,
                    default=None)
    ap.add_argument("--tsv-out", type=pathlib.Path,
                    default=None)
    args = ap.parse_args()
    root = args.root
    md_out = args.md_out or (root / "COMPARE.md")
    tsv_out = args.tsv_out or (root / "COMPARE.tsv")
    if not root.exists():
        print(f"missing root: {root}", file=sys.stderr)
        return 2

    rows: list[tuple[str, dict, dict]] = []
    for form_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        form_id = form_dir.name
        qw = _agg_form(form_dir, suffix="")
        op = _agg_form(form_dir, suffix=".opus")
        if not qw and not op:
            continue
        rows.append((form_id, qw, op))

    if not rows:
        print("no eval data found", file=sys.stderr)
        return 1

    # Per-form table
    md_lines: list[str] = ["# Qwen v2 vs Opus — fact-eval comparison", ""]
    md_lines.append(f"_root: `{root}` · {len(rows)} forms_")
    md_lines.append("")
    md_lines.append("| form | Qwen p | Opus p | total | "
                    "Qwen m/w/oc | Opus m/w/oc | winner |")
    md_lines.append("|------|--------|--------|-------|-------------|"
                    "-------------|--------|")
    tsv_lines = ["form_id\tqw_patterns\top_patterns\ttotal\t"
                 "qw_match\tqw_wrong\tqw_oc\top_match\top_wrong\top_oc\t"
                 "verdict"]

    agg_qw: dict[str, int] = defaultdict(int)
    agg_op: dict[str, int] = defaultdict(int)
    cx_qw: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cx_op: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for form_id, qw, op in rows:
        total = max(qw.get("total_fields", 0), op.get("total_fields", 0))
        qw_p = qw.get("_patterns", 0)
        op_p = op.get("_patterns", 0)
        verdict = _verdict(qw, op)
        qw_cell = (f"{qw.get('matches',0)}/{qw.get('wrong',0)}/"
                   f"{qw.get('overconfident',0)}") if qw_p else "—"
        op_cell = (f"{op.get('matches',0)}/{op.get('wrong',0)}/"
                   f"{op.get('overconfident',0)}") if op_p else "—"
        md_lines.append(f"| {form_id} | {qw_p} | {op_p} | {total} | "
                        f"{qw_cell} | {op_cell} | {verdict} |")
        tsv_lines.append(
            f"{form_id}\t{qw_p}\t{op_p}\t{total}\t"
            f"{qw.get('matches',0)}\t{qw.get('wrong',0)}\t"
            f"{qw.get('overconfident',0)}\t"
            f"{op.get('matches',0)}\t{op.get('wrong',0)}\t"
            f"{op.get('overconfident',0)}\t{verdict}"
        )
        if qw_p and op_p:
            for k, v in qw.items():
                if isinstance(v, int) and not k.startswith("_"):
                    agg_qw[k] += v
            for k, v in op.items():
                if isinstance(v, int) and not k.startswith("_"):
                    agg_op[k] += v
            # Per-pattern complexity bucket
            for pid in range(1, 6):
                qw_ev = _load_eval(root / form_id / f"eval_{pid}.yaml")
                op_ev = _load_eval(root / form_id / f"eval_{pid}.opus.yaml")
                if not qw_ev or not op_ev:
                    continue
                cx = PATTERN_COMPLEXITY[pid]
                for k, v in _summary_counts(qw_ev).items():
                    cx_qw[cx][k] += v
                for k, v in _summary_counts(op_ev).items():
                    cx_op[cx][k] += v

    # Aggregate (only over forms where BOTH models scored)
    def _agg_block(title: str, qw: dict, op: dict) -> list[str]:
        out = [f"### {title}", ""]
        qw_scor = qw.get("total_fields", 0) - qw.get("not_applicable", 0)
        op_scor = op.get("total_fields", 0) - op.get("not_applicable", 0)
        out.append("| metric | Qwen v2 | Opus | delta |")
        out.append("|--------|---------|------|-------|")
        for k in ("total_fields", "not_applicable",
                  "matches", "partial", "wrong",
                  "overconfident", "underconfident",
                  "comprehension_issues"):
            qv = qw.get(k, 0)
            ov = op.get(k, 0)
            delta = ov - qv
            sign = "+" if delta > 0 else ""
            out.append(f"| {k} | {qv} | {ov} | {sign}{delta} |")
        out.append(f"| match%_scorable | {_pct(qw.get('matches',0), qw_scor)}"
                   f" | {_pct(op.get('matches',0), op_scor)} | |")
        out.append(f"| wrong%_scorable | {_pct(qw.get('wrong',0), qw_scor)}"
                   f" | {_pct(op.get('wrong',0), op_scor)} | |")
        out.append(f"| oc%_scorable | "
                   f"{_pct(qw.get('overconfident',0), qw_scor)} | "
                   f"{_pct(op.get('overconfident',0), op_scor)} | |")
        out.append("")
        return out

    md_lines.append("")
    md_lines += _agg_block("Aggregate (forms where both models scored)",
                           agg_qw, agg_op)
    for cx in ("complete", "partial", "edge_case", "sparse"):
        if cx_qw[cx]:
            md_lines += _agg_block(f"By complexity: {cx}",
                                   cx_qw[cx], cx_op[cx])

    md_out.write_text("\n".join(md_lines) + "\n")
    tsv_out.write_text("\n".join(tsv_lines) + "\n")
    print(f"wrote {md_out}")
    print(f"wrote {tsv_out}")

    # Quick stdout summary
    n_both = sum(1 for _, q, o in rows if q.get("_patterns") and o.get("_patterns"))
    print(f"\nforms with both models scored: {n_both}/{len(rows)}")
    if agg_qw and agg_op:
        qs = agg_qw["total_fields"] - agg_qw["not_applicable"]
        os_ = agg_op["total_fields"] - agg_op["not_applicable"]
        print(f"Qwen v2 match%: {100*agg_qw['matches']/max(qs,1):.1f} "
              f"wrong%: {100*agg_qw['wrong']/max(qs,1):.1f}")
        print(f"Opus    match%: {100*agg_op['matches']/max(os_,1):.1f} "
              f"wrong%: {100*agg_op['wrong']/max(os_,1):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
