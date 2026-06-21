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


def test_plural_chained_sections_all_scanned():
    # "§§ 5-301 and 9-999" — the bare-§ fallback only catches the first; every
    # section in the list must be scanned so a fabricated later cite can't dodge
    # the unresolvable bucket. (PR #5 review P2.)
    rep = cs.report("The court applies 18-C M.R.S. §§ 5-301 and 9-999.")
    cites = {h["cite"]: h["resolves"] for h in rep["hits"]}
    assert "18-C §5-301" in cites and "18-C §9-999" in cites
    assert "18-C §9-999" in rep["unresolvable"]          # fabricated second cite caught


def test_plural_comma_separated_sections():
    cites = _cites("see §§ 3-401, 3-203, 9-999")
    assert {"18-C §3-401", "18-C §3-203", "18-C §9-999"} <= cites


def test_scan_urls_classifies_known_fabricated_placeholder():
    base = "https://legislature.maine.gov/statutes/18-C/"
    text = (f"real {base}title18-Csec3-401.html "
            f"made-up {base}title18-Csec99-999.html "
            "fake https://example.com/ruling")
    rep = cs.report(text)
    classes = {h["url"].rsplit("/", 1)[-1]: h["class"] for h in rep["urls"]}
    assert classes["title18-Csec3-401.html"] == "known"
    assert classes["title18-Csec99-999.html"] == "fabricated"
    assert "https://example.com/ruling" in rep["fabricated_urls"]
    assert any(u.endswith("title18-Csec99-999.html") for u in rep["fabricated_urls"])


def test_known_cross_reference_url_not_fabricated():
    url = "https://legislature.maine.gov/statutes/36/title36sec4107.html"
    rep = cs.report(f"estate tax due under {url}")
    assert rep["fabricated_urls"] == []
