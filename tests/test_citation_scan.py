"""Offline tests for the deterministic citation scanner (no network, no LLM)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import citation_scan as cs                 # noqa: E402


def _cites(text, **kw):
    return {h["cite"] for h in cs.scan(text, **kw)}


def test_scans_18c_section_variants():
    for form in ("18-C §3-401", "18-C M.R.S.A. § 3-401", "18-C section 3-401(2)"):
        hits = cs.scan(f"The petition is governed by {form}.")
        assert hits and hits[0]["cite"] == "18-C §3-401"
        assert hits[0]["resolves"] is True
        assert hits[0]["kind"] == "statute"


def test_bare_section_symbol_assumed_18c():
    hits = cs.scan("see §3-203 for priority")
    assert hits[0]["cite"] == "18-C §3-203" and hits[0]["resolves"] is True


def test_unresolvable_statute_flagged():
    rep = cs.report("relying on 18-C §9-999")
    assert "18-C §9-999" in rep["unresolvable"]


def test_neutral_and_reporter_case_cites():
    assert "2000 ME 17" in _cites("as held in 2000 ME 17")
    assert "457 A.2d 1123" in _cites("Estate of Bonin, 457 A.2d 1123")


def test_cross_reference_statute():
    hits = cs.scan("the estate tax return under 36 M.R.S. §4107 is due")
    assert hits[0]["cite"] == "36 M.R.S. §4107"
    assert hits[0]["kind"] == "crossref" and hits[0]["resolves"] is True


def test_case_name_resolves_with_in_re_variant():
    full = _cites("see In re Estate of Kruzynski for the time limit")
    short = _cites("see Estate of Kruzynski for the time limit")
    assert "2000 ME 17" in full and "2000 ME 17" in short


def test_leaked_vs_in_placeholder():
    # one cite inside the protocol, one written bare in prose
    text = "Under [[REF: 18-C §3-401]] the court acts; see also 18-C §3-203."
    rep = cs.report(text)
    assert rep["uses_protocol"] is True
    by_cite = {h["cite"]: h for h in rep["hits"]}
    assert by_cite["18-C §3-401"]["in_placeholder"] is True
    assert by_cite["18-C §3-203"]["in_placeholder"] is False
    assert rep["leaked"] == ["18-C §3-203"]          # only the bare one leaked


def test_no_leaked_when_no_protocol():
    # bare prose with no placeholders -> "leaked" is not meaningful
    rep = cs.report("see 18-C §3-203")
    assert rep["uses_protocol"] is False
    assert rep["leaked"] == []


def test_out_of_vocab_is_form_scoped():
    # 9-306 is a real section, but not in DE-101's vocabulary
    rep = cs.report("citing 18-C §9-306 here", form_id="DE-101")
    assert "18-C §9-306" in rep["out_of_vocab"]
    assert "18-C §9-306" not in rep["unresolvable"]   # it does resolve globally


def test_does_not_false_positive_on_plain_numbers():
    assert cs.scan("the deadline is 3-401 days after filing, in 2000 or later") == []
