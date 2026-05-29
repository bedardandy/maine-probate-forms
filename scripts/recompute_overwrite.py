#!/usr/bin/env python3
"""Overwrite recompute-target fields with their formula-computed value.

Schema fields with a `formula` are derived from other fields. The LLM
sometimes fills them by hand with an incorrect total (see AF-105
total_cash_assets, medical_support_total). Rather than re-prompt, we
recompute deterministically from the dependency fields.

Usage:
  python3 scripts/recompute_overwrite.py \
      --schema repo/forms/AF-105/schema.json \
      --filled intermediate/fact_eval/AF-105/filled_1.v4.gated.json \
      --out    intermediate/fact_eval/AF-105/filled_1.v4.recomp.json

Behaviour:
  - For each field with `formula`, evaluate the formula against current
    values. If the result is a clean number, overwrite the answer.
  - `formula_mode == "at_least"` fields are skipped: the LLM-provided
    total is permitted to exceed the in-form-slot sum (addendum overflow).
  - Fields where the formula returns None (missing dependency) are left
    untouched.

Reuses eval_formula / _to_num from validate_filled.py.
"""
import argparse
import importlib.util
import json
import pathlib
import sys


HERE = pathlib.Path(__file__).resolve().parent


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_filled", HERE / "validate_filled.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _flatten(filled: dict) -> dict:
    """Return a {field_id: value} view of the filled JSON."""
    answers = filled.get("answers") or {}
    out = {}
    for fid, a in answers.items():
        if isinstance(a, dict):
            out[fid] = a.get("value")
        else:
            out[fid] = a
    return out


def _fmt_currency(n: float) -> str:
    # Use comma thousands separator to match the format the LLM emits
    # for hand-filled currency fields (e.g. "14,500.00"). Without this,
    # a formula-recomputed total renders as "251000.00" next to
    # component lines that show "124,500.00" — visually inconsistent.
    return f"{n:,.2f}"


def overwrite(schema: dict, filled: dict, validator) -> tuple[dict, list[tuple]]:
    """Return (new_filled, changes). changes is [(field_id, from, to)]."""
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    values = _flatten(new_filled)
    changes: list[tuple] = []

    for f in schema.get("fields") or []:
        expr = f.get("formula")
        if not expr:
            continue
        if (f.get("formula_mode") or "exact") == "at_least":
            continue
        fid = f["field_id"]
        expected = validator.eval_formula(expr, values)
        if expected is None:
            continue
        new_str = _fmt_currency(expected)
        cur = answers.get(fid)
        cur_val = cur.get("value") if isinstance(cur, dict) else cur
        # No-op if already numerically equal within $0.01 — but
        # validator._to_num('') returns 0.0, so a blank field would
        # match a computed 0.00 and we'd leave it blank. Skip the
        # no-op fast-path when the existing value is blank, so we
        # always write "0.00" rather than leave a hole. (Vision audit
        # caught this on DE-406 current_total_balance.)
        if cur_val not in (None, "", " "):
            cur_num = validator._to_num(cur_val)
            if cur_num is not None and abs(cur_num - expected) < 0.01:
                continue
        if isinstance(cur, dict):
            cur["value"] = new_str
            cur.setdefault("canon_provenance", []).append(
                {"from": cur_val, "to": new_str, "method": "recompute-overwrite"})
        else:
            answers[fid] = new_str
        # Reflect in the values view so downstream formulas see the new
        # number (e.g. when a "total" feeds a "grand total" elsewhere).
        values[fid] = new_str
        changes.append((fid, "" if cur_val is None else str(cur_val), new_str))

    return new_filled, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", type=pathlib.Path, required=True)
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()

    validator = _load_validator()
    schema = json.loads(args.schema.read_text())
    filled = json.loads(args.filled.read_text())

    new_filled, changes = overwrite(schema, filled, validator)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"recompute_overwrite {schema.get('form_id', '?')}: "
          f"{len(changes)} field(s) overwritten")
    for fid, old, new in changes:
        print(f"  {fid}: {old!r} → {new}")


if __name__ == "__main__":
    sys.exit(main())
