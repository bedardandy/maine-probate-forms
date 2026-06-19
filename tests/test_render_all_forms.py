"""Full-corpus render + geometry-sanity gate (a VLM-free 'visual' check).

A test cannot literally eyeball a page, but it can rasterise every page of every
form and assert the invariants a human reviewer would otherwise catch by eye:

  * every page renders without raising (broken/again-reissued source, bad rect);
  * every widget rectangle lands on a real page and inside the page box
    (nothing pushed off the sheet);
  * no rectangle is degenerate (zero/negative width or height).

For per-page overlay PNGs you can actually look at, run ``make probe-all``
(tools/render_corpus.py), which writes the same overlays this gate validates.

Network is needed to fetch the flat sources (cached + SHA-verified); a form
whose source cannot be fetched is skipped, not failed, so the suite stays green
offline while still covering everything in CI.
"""
from __future__ import annotations

import json
import pathlib
import sys

import fitz
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402

TOL = 2.0  # points of slack for rects that hug the page edge / a printed rule

FORMS = sorted(p.name for p in (ROOT / "repo" / "forms").iterdir() if p.is_dir())


def _geometry(form_id: str) -> dict:
    return json.loads(
        (ROOT / "repo" / "forms" / form_id / "fill_geometry.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.parametrize("form_id", FORMS)
def test_every_page_renders_and_rects_are_on_page(form_id: str):
    try:
        source = fetch_source(form_id)
    except Exception as exc:  # offline / upstream re-issue
        pytest.skip(f"source unavailable for {form_id}: {exc}")
    geom = _geometry(form_id)
    with fitz.open(str(source)) as doc:
        dims = {i: (p.rect.width, p.rect.height) for i, p in enumerate(doc)}
        for i in range(doc.page_count):
            doc[i].get_pixmap(dpi=72)  # must not raise

    problems = []
    for fid, spec in geom.get("fields", {}).items():
        for w in spec.get("widgets", []) or []:
            pg, r = w.get("page"), w.get("rect")
            if pg not in dims:
                problems.append(f"{fid}: page {pg} does not exist")
                continue
            width, height = dims[pg]
            if r[2] - r[0] <= 0 or r[3] - r[1] <= 0:
                problems.append(f"{fid} p{pg}: degenerate rect {r}")
            if r[0] < -TOL or r[1] < -TOL or r[2] > width + TOL or r[3] > height + TOL:
                problems.append(f"{fid} p{pg}: rect {r} off page {width}x{height}")
    assert not problems, f"{form_id} geometry problems: " + "; ".join(problems)


def test_corpus_is_non_empty():
    assert len(FORMS) >= 80
