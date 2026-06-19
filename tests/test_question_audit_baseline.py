"""Regression lock on the actionable question-audit findings.

scripts/audit_form_questions.py flags field-model problems a question-by-question
review would catch: a numbered prompt with a blank but no widget
(uncovered_question), a text widget on a signature line (text_on_signature_line),
one widget spanning a printed $ or comma that should be separate fields
(symbol_splits_underline), and two widgets that would share an AcroForm name and
overwrite each other (duplicate_widget_name).

This requires each form's per-code count to stay at or below the reviewed
baseline in tests/question_audit_baseline.json, so a geometry/schema change that
introduces one of these fails CI. Alignment and underline-buffer findings are
advisory and intentionally excluded.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

import fitz
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
from audit_form_questions import audit_form  # noqa: E402

ACTIONABLE = {"symbol_splits_underline", "text_on_signature_line",
              "uncovered_question", "duplicate_widget_name"}
BASELINE = json.loads(
    (ROOT / "tests" / "question_audit_baseline.json").read_text())["counts"]
FORMS = sorted(p.parent.name for p in (ROOT / "repo" / "forms").glob("*/fill_geometry.json"))


@pytest.mark.parametrize("form_id", FORMS)
def test_no_new_actionable_question_findings(form_id: str):
    geom = json.loads(
        (ROOT / "repo" / "forms" / form_id / "fill_geometry.json").read_text())
    try:
        source = fetch_source(form_id)
    except Exception as exc:
        pytest.skip(f"source unavailable for {form_id}: {exc}")
    with fitz.open(str(source)) as doc:
        findings = audit_form(form_id, geom, doc)
    counts = collections.Counter(
        x["code"] for x in findings if x["code"] in ACTIONABLE)
    allowed = BASELINE.get(form_id, {})
    regressions = {c: (allowed.get(c, 0), n) for c, n in counts.items()
                   if n > allowed.get(c, 0)}
    assert not regressions, (
        f"{form_id} new question-audit findings (code: baseline -> now): "
        + ", ".join(f"{c}: {b} -> {n}" for c, (b, n) in sorted(regressions.items())))
