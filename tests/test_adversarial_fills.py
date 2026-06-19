"""Adversarial fills: plausible-but-wrong values an LLM could emit.

These assert the *safety contract* the runtime relies on, not that bad input is
magically corrected:

  1. an out-of-enum choice value is never stamped onto the form (no wrong
     checkbox), and verify_filled surfaces it as expected-but-not-placed;
  2. an invalid member inside a multi-select is dropped, not coerced, and the
     verify step reports the mismatch;
  3. degenerate text (empty, None, very long, numeric-in-a-name) never raises
     in the fill path and still yields a verify summary.

Caveat documented in docs/geometry-review-2026-06.md: the fill path applies no
semantic validation to free-text/date/currency values. A syntactically fine but
nonsensical value ("last spring" as a date) lands verbatim; verify_filled and
human review are the guards. These tests pin the behaviour so a future change
that starts silently stamping invalid enums would fail.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from fetch import fetch_source  # noqa: E402
import fill_pdf  # noqa: E402
import verify_filled  # noqa: E402

FORM = "DE-101(I)"
CASE = ROOT / "repo" / "forms" / FORM / "examples" / "case.example.json"


def _base():
    return json.loads(CASE.read_text(encoding="utf-8"))


def _fill_and_verify(case: dict) -> dict:
    try:
        source = fetch_source(FORM)
    except Exception as exc:  # offline
        pytest.skip(f"source unavailable: {exc}")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        out = fh.name
    fill_pdf.fill_pdf(FORM, case, source, out)
    return verify_filled.verify_filled(FORM, case, out)


def test_out_of_enum_choice_is_not_stamped():
    case = _base()
    case["applicant_record"]["applicant_legal_interest"] = "executor"  # not an option
    entry = _fill_and_verify(case)["fields"]["applicant_legal_interest"]
    # The contract: no checkbox is checked for an unknown option value...
    assert entry.get("placed") is not True
    # ...and the verify step still tells the agent the fact went nowhere.
    assert entry.get("expected") == "executor"
    assert not entry.get("actual")


def test_invalid_member_in_multiselect_is_dropped_and_flagged():
    case = _base()
    case["applicant_record"]["applicant_legal_interest"] = ["heir", "executor"]
    entry = _fill_and_verify(case)["fields"]["applicant_legal_interest"]
    # Valid member lands, bogus member is dropped (never coerced to a checkbox).
    assert "heir" in str(entry.get("actual"))
    assert "executor" not in str(entry.get("actual"))
    # verify_filled records the discrepancy for the agent / human reviewer.
    assert "executor" in str(entry.get("expected"))


@pytest.mark.parametrize(
    "value",
    ["", None, "X" * 4000, 1234567890, "last spring sometime", "</script>‮"],
)
def test_degenerate_text_never_crashes_the_fill(value):
    case = _base()
    case["decedent_record"]["decedent_domicile"] = value
    result = _fill_and_verify(case)  # must not raise
    assert "summary" in result
    assert isinstance(result["summary"].get("placed"), int)
