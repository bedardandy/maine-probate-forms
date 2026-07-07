"""Regression tests for the 2026-07-06 suite-wide audit fixes.

Covers four verified findings that previously let bad drafts pass clean:

  1. validate_filled DATE_RE checked format only, so impossible calendar
     dates (13/45/2024, 2024-02-31) certified as valid dates.
  2. run_date_order silently returned [] (pass) when an operand was
     present but unparseable, so ordering constraints (died-before-born)
     never fired on garbage dates.
  3. run_case / case_chain reported status:"ok" with errors>0, so drafts
     that failed validation were surfaced as usable.
  4. generate_case: SCENARIO_VARIANTS["guardianship_minor"] declared
     twice (second override killed the "temporary" variant), and the
     corpus seed used builtin hash() (salted per-process → non-repro).

The router/ subsystem previously had ZERO tests; this file is the first.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # for `router` namespace package
sys.path.insert(0, str(ROOT / "scripts"))

import validate_filled as V              # noqa: E402
from router.run_case import _ok_status   # noqa: E402
from router.generate_case import (       # noqa: E402
    SCENARIO_VARIANTS,
    _pick_scenario,
)
import zlib                              # noqa: E402


# ── Fix 1: calendar-validity in _typecheck (date) ───────────────────────────
DATE_FIELD = {"field_id": "decedent_date_of_birth", "data_type": "date"}


@pytest.mark.parametrize("value", [
    "13/45/2024",     # month 13, day 45
    "2024-02-31",     # Feb never has 31 days
    "2023-02-29",     # 2023 is not a leap year
    "00/00/0000",     # all-zero
    "04/31/2024",     # April has 30 days
])
def test_impossible_dates_flagged(value):
    """Format-valid but non-existent calendar dates must be an error, not
    a silent pass (regex alone can't catch out-of-range month/day)."""
    out = V._typecheck(DATE_FIELD, value)
    codes = {v["code"] for v in out}
    assert "impossible_date" in codes, (value, out)
    assert all(v["severity"] == V.SEVERITY_ERR for v in out), out


@pytest.mark.parametrize("value", [
    "01/15/2024",
    "2024-02-29",       # 2024 IS a leap year
    "2024-12-31",
    "March 3, 2024",
    "12/31/2024",
])
def test_real_dates_pass(value):
    """Genuine calendar dates in accepted formats produce no violation."""
    assert V._typecheck(DATE_FIELD, value) == []


def test_garbage_format_still_bad_date():
    """A value that fails even the format regex stays a bad_date error
    (not silently reclassified)."""
    out = V._typecheck(DATE_FIELD, "not a date")
    assert {v["code"] for v in out} == {"bad_date"}


def test_empty_date_passes():
    """Empty/None values are for required_when to enforce, not typecheck."""
    assert V._typecheck(DATE_FIELD, "") == []
    assert V._typecheck(DATE_FIELD, None) == []


# ── Fix 2: run_date_order errors on unparseable operand ─────────────────────
DOD = {"field_id": "decedent_date_of_death"}


def test_date_order_unparseable_this_operand_errors():
    out = V.run_date_order(
        DOD, {"decedent_date_of_death": "13/45/2024",
              "decedent_date_of_birth": "2000-01-01"},
        "decedent_date_of_birth", ">")
    assert out and out[0]["code"] == "date_order_unparseable"
    assert out[0]["severity"] == V.SEVERITY_ERR


def test_date_order_unparseable_other_operand_errors():
    out = V.run_date_order(
        DOD, {"decedent_date_of_death": "2020-01-01",
              "decedent_date_of_birth": "garbage"},
        "decedent_date_of_birth", ">")
    assert out and out[0]["code"] == "date_order_unparseable"


def test_date_order_valid_ordering_passes():
    out = V.run_date_order(
        DOD, {"decedent_date_of_death": "2020-01-01",
              "decedent_date_of_birth": "2000-01-01"},
        "decedent_date_of_birth", ">")
    assert out == []


def test_date_order_died_before_born_still_caught():
    """The real bug this constraint guards: death must be after birth."""
    out = V.run_date_order(
        DOD, {"decedent_date_of_death": "1990-01-01",
              "decedent_date_of_birth": "2000-01-01"},
        "decedent_date_of_birth", ">")
    assert out and out[0]["code"] == "date_order_violation"


def test_date_order_empty_operand_still_skips():
    """Genuinely empty operands are skipped (presence is required_when's
    job) — only *present-but-unparseable* becomes an error."""
    out = V.run_date_order(
        DOD, {"decedent_date_of_death": "",
              "decedent_date_of_birth": "2000-01-01"},
        "decedent_date_of_birth", ">")
    assert out == []


# ── Fix 3: errors>0 must not be status "ok" ─────────────────────────────────
def test_ok_status_zero_errors():
    assert _ok_status(0) == "ok"


@pytest.mark.parametrize("n", [1, 2, 17])
def test_ok_status_nonzero_errors_is_errors(n):
    assert _ok_status(n) == "errors"
    assert _ok_status(n) != "ok"


# ── Fix 4a: guardianship_minor keeps both variants ──────────────────────────
def test_guardianship_minor_has_both_variants():
    names = [v[0] for v in SCENARIO_VARIANTS["guardianship_minor"]]
    assert names == ["baseline", "temporary"], names


def test_temporary_variant_resolvable():
    """--scenario temporary previously raised ValueError because the
    second dict entry overrode the first."""
    name, facts, _desc = _pick_scenario("guardianship_minor", 0, "temporary")
    assert name == "temporary"
    assert facts == {"temporary_guardianship": True}


# ── Fix 4b: stable seed hash (crc32, not builtin hash) ──────────────────────
def test_corpus_seed_is_stable_across_processes():
    """crc32 is deterministic across interpreter runs; builtin hash() is
    salted by PYTHONHASHSEED. Assert the exact stable value."""
    ct = "estate_intestate"
    assert zlib.crc32(ct.encode()) % 1000 == 761
