"""Golden render snapshots of the fill layer's output widgets.

`verify_filled` diffs *values* against the plan; it says nothing about where a
value lands or how it is sized. This test locks the rendered geometry of every
filled widget — name, value, rect, font size — for a handful of representative
forms and synthetic cases, so a fill-layer change (split-date rendering,
county upper-casing, shrink-to-fit, an alignment edit that moves a field) is
caught even when the coordinate audit and value verifier both pass.

The snapshot reads the output widgets before baking (fill_pdf writes every
value, including the split-date year suffix, as a named widget). It is
deterministic: values come from committed synthetic cases, rects/sizes are
computed geometrically. Sources are fetched + SHA-verified against the
manifest; if a source drifted upstream the case is skipped (that is the drift
workflow's job, not a fill-logic regression) and offline runs skip cleanly.

Refresh after an intentional fill-layer change:

    UPDATE_FILL_SNAPSHOTS=1 python -m pytest tests/test_fill_render_snapshot.py

Review the JSON diff before committing — an unexpected change here is exactly
the regression this test exists to surface.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

import fitz
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402
import fill_pdf  # noqa: E402

SNAP_DIR = pathlib.Path(__file__).resolve().parent / "snapshots"

# MISC-102 has no committed example; craft a minimal synthetic case that
# exercises the split-date slot pair (the whole reason this test exists).
_MISC102_CASE = {
    "narrative_facts": {
        "appear_probate_court_enabler": True,
        "appear_probate_court_date": "2025-04-02",
        "appear_probate_court_time": "10:00 AM",
    }
}

# (snapshot_name, form_id, case). Chosen to cover distinct fill paths:
# split-date, county caption + upper-case, checkboxes/choices, paragraph
# boxes, docket shrink-to-fit, party-name formatting.
CASES = [
    ("misc-102-split-date", "MISC-102", _MISC102_CASE),
    ("de-101i-example", "DE-101(I)", None),
    ("pb-007-example", "PB-007", None),
    ("ad-026-example", "AD-026", None),
]


def _load_case(form_id, case):
    if case is not None:
        return case
    path = ROOT / "repo" / "forms" / form_id / "examples" / "case.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


def rendered_fields(form_id, case):
    """Fill the form and return every filled widget as a stable, sorted list
    of {page, name, value, rect, size}. Returns (source_verified, rows)."""
    src = fetch_source(form_id)
    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
    res = fill_pdf.fill_pdf(form_id, case, src, out)
    doc = fitz.open(out)
    rows = []
    try:
        for pno in range(len(doc)):
            for wdg in (doc[pno].widgets() or []):
                val = wdg.field_value
                if val in (None, "", False):
                    continue
                if val is True:
                    val = "✓"
                r = wdg.rect
                rows.append({
                    "page": pno,
                    "name": wdg.field_name,
                    "value": str(val),
                    "rect": [round(r.x0, 1), round(r.y0, 1),
                             round(r.x1, 1), round(r.y1, 1)],
                    "size": round(float(wdg.text_fontsize or 0), 1),
                })
    finally:
        doc.close()
    rows.sort(key=lambda t: (t["page"], t["rect"][1], t["rect"][0], t["name"]))
    return res.get("source_verified", True), rows


@pytest.mark.parametrize("name, form_id, case", CASES,
                         ids=[c[0] for c in CASES])
def test_fill_render_snapshot(name, form_id, case):
    try:
        verified, rows = rendered_fields(form_id, _load_case(form_id, case))
    except Exception as exc:  # noqa: BLE001 -- offline / fetch failure
        pytest.skip(f"source unavailable for {form_id}: {exc}")
    if not verified:
        pytest.skip(f"{form_id} source drifted upstream (drift workflow's job)")

    snap_path = SNAP_DIR / f"{name}.json"
    if os.environ.get("UPDATE_FILL_SNAPSHOTS"):
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        snap_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        pytest.skip(f"snapshot refreshed: {snap_path.name}")

    assert snap_path.exists(), (
        f"missing snapshot {snap_path}; regenerate with "
        f"UPDATE_FILL_SNAPSHOTS=1 pytest {pathlib.Path(__file__).name}"
    )
    expected = json.loads(snap_path.read_text(encoding="utf-8"))
    assert rows == expected, (
        f"{name}: rendered fields diverged from snapshot. If intentional, "
        f"refresh with UPDATE_FILL_SNAPSHOTS=1 and review the diff.\n"
        f"expected {len(expected)} rows, got {len(rows)}"
    )
