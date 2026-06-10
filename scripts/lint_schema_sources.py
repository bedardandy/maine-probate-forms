#!/usr/bin/env python3
"""Lint every shipped schema's `fill_strategy.source` values.

Flags sources the resolver (`tools/fill_plan.py:_lookup`) needs special handling
for, so new schema authoring can't silently reintroduce them:

  * two-dot sources (`<record>.<role>.<attr>`, e.g. `case_dict.conservator.phone`)
    — no record carries a literal "role.attr" key; the resolver rewrites them to
    the flat `<role>_<attr>` convention. New shapes beyond `<record>.<x>.<y>`
    (three dots or more) are reported as errors.
  * record-style sources whose record namespace is neither `case_dict` nor a
    `*_record` — these never resolve.

Exit 0 = clean (two-dot sources are listed informationally), 1 = errors.

    python3 scripts/lint_schema_sources.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
_NON_RECORD = {"llm_over_narrative", "recompute_from_dependencies", "wet_ink",
               "human_decision", "left_blank", "triage", ""}


def main() -> int:
    two_dot, errors = [], []
    for sp in sorted(ROOT.glob("repo/forms/*/schema.json")):
        form_id = sp.parent.name
        schema = json.loads(sp.read_text())
        for f in schema.get("fields", []):
            src = (f.get("fill_strategy") or {}).get("source") or ""
            if src in _NON_RECORD or "." not in src:
                continue
            ns = src.split(".", 1)[0]
            if ns != "case_dict" and not ns.endswith("_record"):
                errors.append(f"{form_id}: {f['field_id']} <- {src} "
                              "(record namespace is neither case_dict nor *_record)")
                continue
            dots = src.count(".")
            if dots == 2:
                two_dot.append(f"{form_id}: {f['field_id']} <- {src}")
            elif dots > 2:
                errors.append(f"{form_id}: {f['field_id']} <- {src} "
                              f"({dots} dots — resolver only handles <record>.<role>.<attr>)")
    if two_dot:
        print(f"{len(two_dot)} two-dot source(s) (handled by the resolver's "
              "role.attr rewrite):")
        for line in two_dot:
            print("  " + line)
    if errors:
        print(f"\n{len(errors)} unresolvable source(s):", file=sys.stderr)
        for line in errors:
            print("  " + line, file=sys.stderr)
        return 1
    if not two_dot:
        print("all schema sources use the plain <record>.<key> shape")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
