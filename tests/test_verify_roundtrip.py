"""Fill -> verify round-trip regressions (PR #2 review fixes).

Guards the three fill/verify mismatches the automated review caught:
  * county values are upper-cased by the fill path but must still verify;
  * a truthy enabler is written as a checkbox and must verify as checked,
    not string-compared as text;
  * continuation widgets keep unique names so multi-widget / cross-page values
    do not overwrite each other in the output.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys
import tempfile

import fitz
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402
import fill_pdf  # noqa: E402
import verify_filled  # noqa: E402


def _fill(form_id: str, case: dict) -> str:
    try:
        src = fetch_source(form_id)
    except Exception as exc:
        pytest.skip(f"source unavailable for {form_id}: {exc}")
    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
    fill_pdf.fill_pdf(form_id, case, src, out)
    return out


def test_county_uppercase_still_verifies():
    case = json.loads(
        (ROOT / "repo/forms/CN-1/examples/case.example.json").read_text())
    out = _fill("CN-1", case)
    result = verify_filled.verify_filled("CN-1", case, out)
    for fid in ("county", "notary_county"):
        if fid in result["fields"]:
            assert result["fields"][fid]["placed"], f"{fid} should verify"


def test_truthy_enabler_verifies_as_checkbox():
    case = {"narrative_facts": {"treated_by_physician": "yes"}}
    out = _fill("GS-014", case)
    entry = verify_filled.verify_filled("GS-014", case, out)["fields"].get(
        "treated_by_physician")
    assert entry and entry["kind"] == "enabler" and entry["placed"]


CASES = sorted((ROOT / "repo" / "forms").glob("*/examples/case*.json"))

# Known pre-existing shared-widget collisions (documented PB-007 option rect that
# `make verify` already warns about), allowed so the test guards against NEW ones.
KNOWN_DUP_NAMES = {"appointment_level__expanded"}


@pytest.mark.parametrize("case_path", CASES, ids=lambda p: f"{p.parents[1].name}:{p.stem}")
def test_filled_widget_names_are_unique(case_path: pathlib.Path):
    form_id = case_path.parents[1].name
    case = json.loads(case_path.read_text())
    out = _fill(form_id, case)
    names = []
    with fitz.open(out) as doc:
        for page in doc:
            names.extend(w.field_name for w in (page.widgets() or []))
    dupes = [n for n, c in collections.Counter(names).items()
             if c > 1 and n not in KNOWN_DUP_NAMES]
    assert not dupes, f"{form_id} has colliding widget names (overwrite): {dupes}"
