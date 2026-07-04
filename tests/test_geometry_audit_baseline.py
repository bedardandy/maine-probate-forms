"""Regression lock on the systematic geometry audit, across all pages of all forms.

`scripts/audit_form_geometry.py` flags the high-value mapper failure modes a
visual reviewer looks for — widgets overlapping each other or the printed text,
rects overrunning a printed rule, choice/text widgets of the wrong size, and
generic/orphan field names. This test runs that audit over the whole corpus and
requires each form's per-code count to stay at or below the reviewed baseline in
``tests/geometry_audit_baseline.json``.

Effect: a geometry edit that introduces a new collision/overrun/orphan on any
page of any form fails the gate; cleaning a form up (fewer findings) always
passes. As forms are fixed, lower the baseline numbers to lock in the gain.

Self widget/widget collisions (a field overlapping its own continuation widgets)
are excluded — those are benign multiline / repeating-group layouts.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_form_geometry import audit_form  # noqa: E402

HIGH = {
    "widget_widget_collision",
    "widget_native_text_collision",
    "widget_overruns_rule",
    "widget_overruns_blank",
    "checkbox_off_printed_box",
    "county_label_collision",
    "split_date_stub_unhandled",
    "generic_or_orphan_name",
    "choice_not_checkbox_sized",
    "text_is_checkbox_sized",
}

BASELINE = json.loads(
    (ROOT / "tests" / "geometry_audit_baseline.json").read_text(encoding="utf-8")
)["counts"]

FORMS = sorted(p.name for p in (ROOT / "repo" / "forms").iterdir() if p.is_dir())


def _high_value_counts(form_id: str) -> dict[str, int]:
    counts = collections.Counter()
    for f in audit_form(form_id):
        code = f["code"]
        if code not in HIGH:
            continue
        if code == "widget_widget_collision" and f.get("field_id") == f.get(
            "other_field_id"
        ):
            continue
        counts[code] += 1
    return dict(counts)


@pytest.mark.parametrize("form_id", FORMS)
def test_no_new_high_value_findings(form_id: str):
    current = _high_value_counts(form_id)
    allowed = BASELINE.get(form_id, {})
    regressions = {
        code: (allowed.get(code, 0), n)
        for code, n in current.items()
        if n > allowed.get(code, 0)
    }
    assert not regressions, (
        f"{form_id} regressed (code: baseline -> now): " + ", ".join(
            f"{c}: {b} -> {n}" for c, (b, n) in sorted(regressions.items())
        )
    )
