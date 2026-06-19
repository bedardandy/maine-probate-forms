"""Build + smoke gate: every shipped example case must fill and verify clean.

For each ``repo/forms/<ID>/examples/case*.json`` we fetch the flat source,
write a filled PDF, then re-open it and diff widget values against the plan.
A resolved fact that the plan claims but that does not land on the page (a
mismatch or a missing widget) fails the gate — that is the class of geometry /
mapping regression this suite is meant to catch.

Network is required to fetch sources; sources are cached + SHA-verified. If a
source cannot be fetched the individual case is skipped, not failed, so the
suite stays green offline while still exercising everything in CI.
"""
from __future__ import annotations

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


def _example_cases() -> list[tuple[str, pathlib.Path]]:
    cases = []
    for case_path in sorted((ROOT / "repo" / "forms").glob("*/examples/case*.json")):
        form_id = case_path.parents[1].name
        cases.append((form_id, case_path))
    return cases


CASES = _example_cases()

_KNOWN = json.loads(
    (ROOT / "tests" / "known_fill_gaps.json").read_text(encoding="utf-8")
)["gaps"]


def _norm(value) -> str:
    return " ".join(str(value).split())


def _gaps(result: dict) -> set[str]:
    """Resolved facts that did not land correctly: missing widget or wrong value."""
    out = set()
    for fid, e in result["fields"].items():
        if not e.get("placed") and e.get("expected") is not None:
            out.add(fid)
        elif e.get("placed") and _norm(e.get("expected")) != _norm(e.get("actual")):
            out.add(fid)
    return out


@pytest.mark.parametrize(
    "form_id,case_path", CASES, ids=[f"{f}:{p.stem}" for f, p in CASES]
)
def test_example_fills_and_verifies_clean(form_id: str, case_path: pathlib.Path):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    try:
        source = fetch_source(form_id)
    except Exception as exc:  # offline / upstream re-issue
        pytest.skip(f"source unavailable for {form_id}: {exc}")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        out = fh.name
    fill_pdf.fill_pdf(form_id, case, source, out)
    result = verify_filled.verify_filled(form_id, case, out)

    key = f"{form_id}:{case_path.stem}"
    allowed = set(_KNOWN.get(key, []))
    regressions = _gaps(result) - allowed
    assert not regressions, (
        f"{key} NEW fill gaps (not in tests/known_fill_gaps.json): "
        f"{sorted(regressions)}"
    )


def test_there_are_example_cases():
    assert CASES, "no example cases discovered under repo/forms/*/examples/"
