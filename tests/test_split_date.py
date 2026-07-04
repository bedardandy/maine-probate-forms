"""Split-date rendering for printed "on ___, 20__" slot pairs.

MISC-102 (and other subpoena/jurat layouts) print a date across two slots: a
blank for the month and day and a separate "20__" stub for the two-digit year.
`fill_pdf._split_date` splits a parseable date so the form reads "April 2, 2025"
instead of dumping the whole ISO date on the first blank and orphaning the year
stub. Unparseable values must fall through untouched (the stub stays blank).
"""
from __future__ import annotations

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


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2025-04-02", ("April 2", "25")),
        ("04/02/2025", ("April 2", "25")),
        ("4/2/25", ("April 2", "25")),
        ("2025-12-31", ("December 31", "25")),
        ("2001-01-01", ("January 1", "01")),
        ("not a date", None),
        ("", None),
        ("2025-13-40", None),        # out-of-range month/day
        ("April 2, 2025", None),     # already word form; left whole
    ],
)
def test_split_date(value, expected):
    assert fill_pdf._split_date(value) == expected


def _baked_words(pdf: str, page: int) -> list[tuple]:
    doc = fitz.open(pdf)
    try:
        doc.bake(widgets=True)
    except Exception:
        pass
    words = doc[page].get_text("words")
    doc.close()
    return words


def test_misc102_split_date_renders_month_day_and_year():
    """A real appearance date fills the month/day blank and the 20__ stub."""
    try:
        src = fetch_source("MISC-102")
    except Exception as exc:
        pytest.skip(f"source unavailable: {exc}")
    case = {
        "narrative_facts": {
            "appear_probate_court_date": "2025-04-02",
        }
    }
    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
    fill_pdf.fill_pdf("MISC-102", case, src, out)
    words = _baked_words(out, 0)
    # month/day on the "on ___" blank (right side of line 1)
    line1 = [w[4] for w in words if 246 <= w[1] <= 262 and w[0] > 440]
    assert "April" in " ".join(line1), line1
    # two-digit year on the "20__" stub (left of line 2, just after "20")
    stub = [w[4] for w in words if 258 <= w[1] <= 274 and 82 <= w[0] <= 100]
    assert "25" in " ".join(stub), stub


def test_misc102_non_date_leaves_stub_blank():
    """An unparseable value writes the whole value on the main blank and
    leaves the year stub empty (no crash, no stray text)."""
    try:
        src = fetch_source("MISC-102")
    except Exception as exc:
        pytest.skip(f"source unavailable: {exc}")
    case = {"narrative_facts": {"appear_probate_court_date": "to be set"}}
    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
    res = fill_pdf.fill_pdf("MISC-102", case, src, out)
    assert res["ok"]
    stub = [w[4] for w in _baked_words(out, 0)
            if 258 <= w[1] <= 274 and 82 <= w[0] <= 100]
    assert not any(ch.isdigit() for ch in " ".join(stub)), stub
