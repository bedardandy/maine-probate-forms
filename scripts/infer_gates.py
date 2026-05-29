#!/usr/bin/env python3
"""Infer writable_when gate fields from populated dependents.

Symptom (N-118 in the v3 canon corpus): the LLM fills in concrete values
for `change_in_dwelling_new_address`, `change_in_dwelling_notified_person`,
etc., but leaves the parent gate field `change_in_dwelling` empty,
explaining in its own reasoning that the gate is "a section enabler
field; no substantive value is required." The validator then flags every
dependent as `not_writable` because writable_when is unsatisfied.

The post-processor logic: for each field with a
`writable_when: {all_of: [{field: <gate>, equals: <V>}]}` clause, if the
dependent's value is non-empty and the gate's value is empty (or wrong),
set the gate to <V>. This is a safe inference — if the LLM committed to
concrete values for the children, the parent toggle is implied.

Limited to the simple `{all_of: [{field, equals}]}` shape today; more
exotic writable_when trees (nested any_of, comparison operators) are
skipped with a warning so we don't guess.

Usage:
    python3 scripts/infer_gates.py \\
        --schema repo/forms/N-118/schema.json \\
        --filled intermediate/fact_eval/N-118/filled_1.v3.canon.json \\
        --out    intermediate/fact_eval/N-118/filled_1.v3.canon.gated.json \\
        --report intermediate/fact_eval/N-118/gate_changes.tsv

    --dry-run prints inferences and exits.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


def simple_gate(writable_when) -> tuple[str, object] | None:
    """Return (gate_field, equals_value) for {all_of: [{field, equals}]}
    or None for other shapes."""
    if not isinstance(writable_when, dict):
        return None
    items = writable_when.get("all_of") or writable_when.get("any_of") or []
    if len(items) != 1 or not isinstance(items[0], dict):
        return None
    item = items[0]
    if set(item.keys()) > {"field", "equals"}:
        return None
    gate = item.get("field")
    equals = item.get("equals")
    if not gate or equals is None:
        return None
    return (gate, equals)


def value_of(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
    if isinstance(a, dict):
        return (a.get("value") or "")
    return str(a)


def coerce_for_gate(equals_value) -> str:
    """How the gate should be written. Schemas use `equals: true` for
    boolean checkboxes, sometimes `equals: "yes"`. Map both to "yes" so
    the gate is human-readable and value_in-compatible."""
    if equals_value is True:
        return "yes"
    if equals_value is False:
        return "no"
    return str(equals_value)


def gate_satisfies(current: str, equals_value) -> bool:
    """Does `current` already satisfy `equals_value`?"""
    if not current:
        return False
    want = coerce_for_gate(equals_value)
    return current.strip().lower() == want.lower()


def process(schema: dict, filled: dict) -> tuple[dict, list[tuple], list[str]]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes = []
    skipped_shapes = []

    # Group by (gate_field, equals_value) — a single gate can drive
    # multiple mutually-exclusive subgroups (PB-007 has appointment_level
    # equals "limited" for limited_* fields AND equals "expanded" for
    # expanded_* fields). Conflating these would let one subgroup's
    # populated deps overwrite the gate with the other subgroup's value.
    gate_groups: dict[tuple[str, str], list[str]] = {}
    for f in schema.get("fields", []):
        w = f.get("writable_when")
        if not w:
            continue
        info = simple_gate(w)
        if info is None:
            skipped_shapes.append(f["field_id"])
            continue
        gate, equals = info
        key = (gate, coerce_for_gate(equals))
        gate_groups.setdefault(key, []).append(f["field_id"])

    # Safety rule: NEVER overwrite a gate that already has a non-empty
    # value. If the LLM committed to a scope ("expanded" vs "limited"),
    # respect that even if some of the *other* scope's dependents also
    # have values — that's an LLM consistency bug we'd surface as a
    # validator error, not silently overwrite.
    handled_gates: set[str] = set()
    for (gate, target_val), deps in gate_groups.items():
        current_gate = value_of(answers, gate)
        if current_gate and current_gate.strip():
            # Gate already set — leave it alone, regardless of which
            # subgroup we're looking at.
            handled_gates.add(gate)
            continue
        if gate in handled_gates:
            continue
        # Find populated deps in THIS subgroup only
        populated_deps = [d for d in deps
                          if value_of(answers, d).strip()]
        if not populated_deps:
            continue
        # Conflict check: if another subgroup of the same gate also has
        # populated deps with a DIFFERENT target_val, skip — the LLM
        # gave us contradictory data and we shouldn't pick a winner.
        conflicting = False
        for (other_gate, other_val), other_deps in gate_groups.items():
            if other_gate != gate or other_val == target_val:
                continue
            if any(value_of(answers, d).strip() for d in other_deps):
                conflicting = True
                break
        if conflicting:
            changes.append((gate, "<empty>", "<skipped-conflict>",
                            f"conflict",
                            ",".join(populated_deps[:3])))
            handled_gates.add(gate)
            continue
        # Safe to infer
        existing = answers.get(gate)
        if isinstance(existing, dict):
            existing["value"] = target_val
            existing.setdefault("gate_inferred_from", populated_deps[:5])
            existing["reasoning"] = (
                f"[gate-inferred] dependents populated "
                f"({', '.join(populated_deps[:3])}"
                f"{'...' if len(populated_deps)>3 else ''}) "
                f"so this gate must be {target_val!r}. "
                f"Prior LLM reasoning: {existing.get('reasoning','')[:120]}"
            )
            existing["confidence"] = 0.95
        else:
            answers[gate] = target_val
        changes.append((gate, "<empty>", target_val,
                        f"{len(populated_deps)}-deps",
                        ",".join(populated_deps[:5])))
        handled_gates.add(gate)

    return new_filled, changes, skipped_shapes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True)
    ap.add_argument("--filled", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    schema = json.loads(Path(args.schema).read_text())
    filled = json.loads(Path(args.filled).read_text())

    new_filled, changes, skipped = process(schema, filled)

    if args.dry_run:
        for r in changes:
            print("\t".join(map(str, r)))
        print(f"-- {len(changes)} gate(s) would be inferred",
              file=sys.stderr)
        if skipped:
            print(f"-- {len(skipped)} complex writable_when shape(s) skipped: "
                  f"{', '.join(skipped[:5])}", file=sys.stderr)
        return

    out_path = args.out or args.filled.replace(".json", ".gated.json")
    Path(out_path).write_text(json.dumps(new_filled, indent=2))

    if args.report:
        with open(args.report, "w") as fh:
            fh.write("gate_field\tfrom\tto\treason\tpopulated_deps\n")
            for r in changes:
                fh.write("\t".join(map(str, r)) + "\n")

    print(f"wrote {out_path}  ({len(changes)} gate(s) inferred)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
