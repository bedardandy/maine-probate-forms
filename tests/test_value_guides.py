"""Value-guide layer: freshness + calculation integrity.

  * every repo/forms/<ID>/value_guide.json matches what build_value_guide.py
    would emit from the current schema (no drift);
  * every calculated field's formula references fields that exist, so a derived
    total can be recomputed and validated rather than trusted.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_value_guide import build_form  # noqa: E402

FORMS = sorted(p.parent.name for p in (ROOT / "repo" / "forms").glob("*/schema.json"))


@pytest.mark.parametrize("form_id", FORMS)
def test_value_guide_in_sync_with_schema(form_id: str):
    path = ROOT / "repo" / "forms" / form_id / "value_guide.json"
    assert path.exists(), f"missing value_guide.json for {form_id} (run build_value_guide.py)"
    expected = json.dumps(build_form(form_id), indent=2, ensure_ascii=False) + "\n"
    assert path.read_text() == expected, (
        f"{form_id} value_guide.json is stale; run scripts/build_value_guide.py"
    )


def _refs(node, out):
    if isinstance(node, dict):
        if node.get("op") == "field" and node.get("id"):
            out.add(node["id"])
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)


@pytest.mark.parametrize("form_id", FORMS)
def test_calculations_reference_existing_fields(form_id: str):
    schema = json.loads(
        (ROOT / "repo" / "forms" / form_id / "schema.json").read_text())
    ids = {f["field_id"] for f in schema.get("fields", [])}
    bad = {}
    for f in schema.get("fields", []):
        if f.get("formula"):
            refs = set()
            _refs(f["formula"], refs)
            missing = refs - ids
            if missing:
                bad[f["field_id"]] = sorted(missing)
    assert not bad, f"{form_id} formulas reference missing fields: {bad}"
