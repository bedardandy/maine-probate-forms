#!/usr/bin/env python3
"""Compare LLM-emitted trees against gold (hand-fixed) trees across N retries.

For each form, run build_form_tree.py at retry=0, 1, 2, ... and record:
  - validator pass/fail per attempt
  - widget coverage (% of digest widgets bound)
  - widget-binding edit distance vs gold (mismatched + missed Wxxx→node_id pairs)

Usage:
    python3 scripts/eval_trees.py \
        --forms PB-007 GS-008 AF-101 AD-008 AF-103 APP-1 DE-104 DE-405 NC-001 PP-203 \
        --retries 0 3
"""
from __future__ import annotations
import argparse
import pathlib
import sys
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_form_tree import (  # noqa: E402
    emit_with_retry, widget_ids_in_digest, validate_tree,
)


def widget_node_map(tree: dict) -> dict[str, str]:
    """Return {Wxxx: node_id_or_option_value}. For select_* options the value
    is `{node_id}__{option_value}` so options are distinguishable from text."""
    out: dict[str, str] = {}
    for node in tree.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        nid = node.get("id", "<?>")
        if node.get("widget"):
            out[node["widget"]] = nid
        for w in node.get("widgets") or []:
            out[w] = nid
        for opt in node.get("options") or []:
            if not isinstance(opt, dict):
                continue
            tag = f"{nid}__{opt.get('value', '?')}"
            if opt.get("widget"):
                out[opt["widget"]] = tag
            for w in opt.get("widgets") or []:
                out[w] = tag
    return out


def compare_to_gold(llm_tree: dict, gold_tree: dict,
                    digest_widgets: set[str]) -> dict:
    """Return diff stats. Uses widget→node mapping, not node names (LLM names
    are arbitrary; what matters is whether each widget ends up in the right
    *kind* of role)."""
    llm_map = widget_node_map(llm_tree)
    gold_map = widget_node_map(gold_tree)
    matches = mismatches = missing_in_llm = extra_in_llm = 0
    for w in digest_widgets:
        in_llm = w in llm_map
        in_gold = w in gold_map
        if in_llm and in_gold:
            if llm_map[w] == gold_map[w]:
                matches += 1
            else:
                mismatches += 1
        elif in_gold and not in_llm:
            missing_in_llm += 1
        elif in_llm and not in_gold:
            extra_in_llm += 1
    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing_in_llm": missing_in_llm,
        "extra_in_llm": extra_in_llm,
        "llm_coverage": len(set(llm_map) & digest_widgets),
        "gold_coverage": len(set(gold_map) & digest_widgets),
        "digest_widgets": len(digest_widgets),
    }


def run_one(form_id: str, retries: int, *, endpoint: str, model: str,
            max_tokens: int, digest_dir: pathlib.Path,
            gold_dir: pathlib.Path) -> dict:
    digest_path = digest_dir / f"{form_id}.txt"
    gold_path = gold_dir / f"{form_id}.yaml"
    if not digest_path.exists():
        return {"form": form_id, "error": f"missing digest {digest_path}"}
    if not gold_path.exists():
        return {"form": form_id, "error": f"missing gold {gold_path}"}
    digest = digest_path.read_text()
    gold_tree = yaml.safe_load(gold_path.read_text())
    digest_widgets = widget_ids_in_digest(digest)
    tree, errs, attempts = emit_with_retry(
        digest, form_id,
        endpoint=endpoint, model=model,
        max_tokens=max_tokens, retries=retries,
    )
    if tree is None:
        return {"form": form_id, "retries": retries, "attempts": attempts,
                "passed": False, "errors": len(errs),
                "matches": 0, "mismatches": 0,
                "missing_in_llm": len(digest_widgets), "extra_in_llm": 0,
                "llm_coverage": 0, "digest_widgets": len(digest_widgets)}
    diff = compare_to_gold(tree, gold_tree, digest_widgets)
    return {
        "form": form_id,
        "retries": retries,
        "attempts": attempts,
        "passed": not errs,
        "errors": len(errs),
        **diff,
    }


def print_table(rows: list[dict]) -> None:
    cols = ["form", "retries", "attempts", "passed", "errors",
            "llm_coverage", "digest_widgets",
            "matches", "mismatches", "missing_in_llm", "extra_in_llm"]
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0))
              for c in cols}
    sep = " │ "
    print(sep.join(c.rjust(widths[c]) for c in cols))
    print("─┼─".join("─" * widths[c] for c in cols))
    for r in rows:
        print(sep.join(str(r.get(c, "")).rjust(widths[c]) for c in cols))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", nargs="+", required=True)
    ap.add_argument("--retries", type=int, nargs="+", default=[0, 3],
                    help="List of retry counts to evaluate (e.g. 0 3)")
    ap.add_argument("--endpoint", default="http://localhost:8088/v1")
    ap.add_argument("--model", default="Qwen3.6-27B-FP8")
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--digest-dir", type=pathlib.Path,
                    default=ROOT / "intermediate" / "digest")
    ap.add_argument("--gold-dir", type=pathlib.Path,
                    default=ROOT / "trees" / "gold")
    ap.add_argument("--out", type=pathlib.Path,
                    default=ROOT / "intermediate" / "eval_trees.yaml")
    args = ap.parse_args()

    rows: list[dict] = []
    for retries in args.retries:
        for form in args.forms:
            print(f"\n=== {form} (retries={retries}) ===", file=sys.stderr)
            row = run_one(form, retries,
                          endpoint=args.endpoint, model=args.model,
                          max_tokens=args.max_tokens,
                          digest_dir=args.digest_dir,
                          gold_dir=args.gold_dir)
            rows.append(row)
            print(row, file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump(rows, sort_keys=False))
    print(f"\nwrote {args.out}\n")
    print_table(rows)

    # Summary
    print("\n── summary ──")
    for retries in args.retries:
        subset = [r for r in rows if r.get("retries") == retries]
        passed = sum(1 for r in subset if r.get("passed"))
        avg_match = (sum(r.get("matches", 0) for r in subset)
                     / max(sum(r.get("digest_widgets", 0) for r in subset), 1))
        print(f"  retries={retries}: {passed}/{len(subset)} passed validation, "
              f"widget-match rate {avg_match:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
