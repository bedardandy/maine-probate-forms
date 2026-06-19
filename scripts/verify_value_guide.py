#!/usr/bin/env python3
"""Validate the value-guide layer; be adversarial about under-specification.

Checks (CI gate + advisory):
  * guides are in sync with schema.json (regenerate with build_value_guide.py);
  * every calculated field's formula references fields that exist (so derived
    totals can actually be recomputed and validated);
  * ADVISORY: text fields whose label implies a concrete type (date, money,
    ZIP, phone, email, year, docket) but are typed generic `text` — the guide
    cannot be specific about a field the schema leaves vague.

Exit non-zero only on the first two (hard) classes; advisories are printed and
written to catalog/value_guide_advisories.json but do not fail the build.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_value_guide import build_form  # noqa: E402

IMPLIED = [
    (re.compile(r"date of|_date$|dated|date of birth|date of death", re.I), "date"),
    (re.compile(r"amount|\bfee\b|\$|value of|sum|balance|salary|wages", re.I), "currency"),
    (re.compile(r"\bzip\b|postal code", re.I), "zip"),
    (re.compile(r"telephone|phone number", re.I), "phone"),
    (re.compile(r"e-?mail", re.I), "email"),
    (re.compile(r"\byear\b", re.I), "year"),
    (re.compile(r"docket", re.I), "docket_number"),
]


def _formula_field_ids(node, out):
    if isinstance(node, dict):
        if node.get("op") == "field" and node.get("id"):
            out.add(node["id"])
        for v in node.values():
            _formula_field_ids(v, out)
    elif isinstance(node, list):
        for v in node:
            _formula_field_ids(v, out)


def main() -> int:
    forms = sorted(p.parent.name for p in
                   (ROOT / "repo" / "forms").glob("*/schema.json"))
    stale, broken_formula, advisories = [], [], []
    for form_id in forms:
        pkg = ROOT / "repo" / "forms" / form_id
        schema = json.loads((pkg / "schema.json").read_text())
        field_ids = {f["field_id"] for f in schema.get("fields", [])}
        guide_path = pkg / "value_guide.json"
        expected = json.dumps(build_form(form_id), indent=2, ensure_ascii=False) + "\n"
        if not guide_path.exists() or guide_path.read_text() != expected:
            stale.append(form_id)

        for f in schema.get("fields", []):
            # calculation validation: inputs must resolve
            if f.get("formula"):
                refs = set()
                _formula_field_ids(f["formula"], refs)
                # slot formulas reference prefixes, not literal ids; only check
                # explicit {op:field,id:...} references.
                missing = {r for r in refs if r not in field_ids}
                if missing:
                    broken_formula.append((form_id, f["field_id"], sorted(missing)))
            # under-typed text advisory
            if f.get("data_type") == "text":
                label = f.get("label", "") or f["field_id"].replace("_", " ")
                for rx, implied in IMPLIED:
                    if rx.search(label):
                        advisories.append({"form_id": form_id,
                                           "field_id": f["field_id"],
                                           "label": label, "implies": implied})
                        break

    (ROOT / "catalog" / "value_guide_advisories.json").write_text(
        json.dumps({"count": len(advisories), "advisories": advisories},
                   indent=2) + "\n")

    print(f"value guides: {len(forms)} forms, {len(advisories)} under-typed "
          f"text advisories")
    ok = True
    if stale:
        print("  FAIL stale guides (run scripts/build_value_guide.py): "
              + ", ".join(stale))
        ok = False
    if broken_formula:
        ok = False
        for form_id, fid, missing in broken_formula:
            print(f"  FAIL {form_id}:{fid} formula references missing fields {missing}")
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
