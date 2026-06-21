"""Offline tests for the legal-citation hallucination inspector.

No network and no LLM: the inspector's pure stages (placeholder substitution with
its two hard-fail gates, the Maine closed-vocabulary builder, statute-text
extraction over a frozen fixture, and verdict validation/quote-grounding) are all
exercised with a stub client and a monkeypatched fetch. Mirrors the repo's test
conventions (import tools as siblings; skip-on-failure for any live path).
"""
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

import legal_inspector as li               # noqa: E402
import maine_citation_db as mdb            # noqa: E402
import fetch_statute_text as fst           # noqa: E402


# --------------------------------------------------------------------------- #
# Stub OpenAI-compatible client (same shape route_form/legal_inspector call)  #
# --------------------------------------------------------------------------- #
def make_stub(payload: str):
    def create(**_kw):
        msg = types.SimpleNamespace(content=payload, reasoning_content="")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def _resolver(_key):
    return {"cite": "18-C §3-401", "title": "Formal testacy proceedings",
            "url": "u", "text": "After notice and hearing the court enters an "
            "order determining intestacy and heirs."}


# --------------------------------------------------------------------------- #
# Generic engine: placeholders + the two gates                                #
# --------------------------------------------------------------------------- #
def test_extract_refs_ordered_unique_and_spacing():
    refs = li.extract_refs("a [[REF:K1]] b [[REF:  K2 ]] c [[REF: K1]]")
    assert refs == ["K1", "K2"]


def test_substitute_uses_resub_over_spans():
    text = "First [[REF: K]] and again [[REF: K]]."
    out, cites = li.substitute(text, {"K"},
                               lambda k: {"cite": "K", "title": "T", "text": "BODY"})
    assert out.count("BODY") == 2                 # both placeholders replaced
    resolved = [c for c in cites if c["status"] == "resolved"]
    assert len(resolved) == 1                     # deduped to one record


def test_substitute_gate_a_invented_key():
    out, cites = li.substitute("see [[REF: Made_Up]]", {"K"}, _resolver)
    assert "[[INVENTED: Made_Up]]" in out
    assert cites[0]["status"] == "invented"


def test_substitute_gate_b_unresolved_when_no_text():
    out, cites = li.substitute("see [[REF: K]]", {"K"}, lambda k: None)
    assert "[[UNRESOLVED: K]]" in out
    assert cites[0]["status"] == "unresolved"


# --------------------------------------------------------------------------- #
# Generic engine: inspect() merge + quote grounding                           #
# --------------------------------------------------------------------------- #
def test_inspect_merges_structured_verdicts():
    payload = json.dumps({"verdicts": [{
        "cite": "18-C §3-401", "supports_conclusion": "fail",
        "quote": "the court enters an order determining intestacy",
        "rationale": "draft overstates the authority"}]})
    res = li.inspect("The court must do X under [[REF: 18-C §3-401]].",
                     {"18-C §3-401"}, _resolver,
                     client=make_stub(payload), model="m")
    assert res["ok"]
    v = res["verdicts"][0]
    assert v["supports_conclusion"] == "fail"
    assert v["quote_grounded"] is True
    assert res["summary"]["fail"] == 1


def test_inspect_downgrades_fabricated_quote():
    payload = json.dumps({"verdicts": [{
        "cite": "18-C §3-401", "supports_conclusion": "pass",
        "quote": "the owner has absolute unregulatable power",
        "rationale": "fabricated"}]})
    res = li.inspect("X under [[REF: 18-C §3-401]].", {"18-C §3-401"}, _resolver,
                     client=make_stub(payload), model="m")
    v = res["verdicts"][0]
    assert v["quote_grounded"] is False
    assert v["supports_conclusion"] == "unclear"     # downgraded from pass


def test_inspect_short_circuits_with_no_resolved_citations():
    # invented-only draft: no LLM call needed, recorded deterministically.
    res = li.inspect("see [[REF: Bogus]].", {"18-C §3-401"}, _resolver,
                     client=make_stub("{}"), model="m")
    assert res["ok"]
    assert res["invented"] == ["Bogus"]
    assert res["summary"]["invented"] == 1
    assert res["verdicts"] == []


# --------------------------------------------------------------------------- #
# Maine adapter: closed vocabulary from the real repo files                    #
# --------------------------------------------------------------------------- #
def test_build_vocab_de101_is_scoped_and_typed():
    vocab = mdb.build_vocab("DE-101")
    assert "18-C §3-401" in vocab
    assert vocab["18-C §3-401"]["kind"] == "statute"
    # cross-ref present, case present (DE-101 cites Kruzynski)
    assert any(m["kind"] == "crossref" for m in vocab.values())
    assert any(m["kind"] == "case" for m in vocab.values())
    # an unrelated statute is NOT in this form's vocabulary
    assert "18-C §9-306" not in vocab


def test_resolves_matches_verify_statutes():
    sec, xref, _ = mdb._index()
    assert mdb.resolves("18-C §3-401", sec, xref)
    assert mdb.resolves("36 M.R.S. §4107", sec, xref)
    assert not mdb.resolves("18-C §9-999", sec, xref)


def test_make_resolver_offline_uses_title_and_note():
    vocab = mdb.build_vocab("DE-101")
    resolve = mdb.make_resolver(vocab, fetch_text=False)
    auth = resolve("18-C §3-401")
    assert auth and "Formal testacy" in auth["text"]


def test_make_resolver_fetch_failure_is_unresolved():
    vocab = mdb.build_vocab("DE-101")
    resolve = mdb.make_resolver(
        vocab, fetch_text=True,
        fetch=lambda c: {"cite": c, "text": None, "error": "boom"})
    assert resolve("18-C §3-401") is None           # Gate B -> unresolved


# --------------------------------------------------------------------------- #
# Statute-text fetch + extraction (frozen fixture, no network)                #
# --------------------------------------------------------------------------- #
def test_extract_statute_text_strips_chrome():
    html = (FIXTURES / "sec3-108.html").read_text(encoding="utf-8")
    text = fst._extract_statute_text(html, "18-C §3-108")
    assert "ultimate time limit" in text
    assert "3 years after the decedent's death" in text
    assert "Bills & Laws" not in text and "Search" not in text   # nav stripped
    assert "Revisor" not in text                                 # footer stripped
    assert "analytics" not in text                               # script stripped


def test_fetch_statute_text_pins_and_verifies(tmp_path, monkeypatch):
    html = (FIXTURES / "sec3-108.html").read_text(encoding="utf-8")
    monkeypatch.setattr(fst, "_download", lambda url, timeout=60: html)
    monkeypatch.setattr(fst, "statute_url", lambda cite: "https://example/sec3-108")

    res = fst.fetch_statute_text("18-C §3-108", cache_dir=tmp_path)
    assert res["text"] and res["text_verified"] is None          # not pinned yet
    sha = res["sha256"]

    manifest = {"statutes": {"18-C §3-108": {"sha256": sha}}}
    ok = fst.fetch_statute_text("18-C §3-108", cache_dir=tmp_path, manifest=manifest)
    assert ok["text_verified"] is True

    bad = {"statutes": {"18-C §3-108": {"sha256": "deadbeef"}}}
    drift = fst.fetch_statute_text("18-C §3-108", cache_dir=tmp_path, manifest=bad)
    assert drift["text_verified"] is False


def test_fetch_statute_text_unknown_cite_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fst, "statute_url", lambda cite: None)
    res = fst.fetch_statute_text("18-C §9-999", cache_dir=tmp_path)
    assert res["text"] is None and "no url" in res["error"]


# --------------------------------------------------------------------------- #
# End-to-end adapter glue with a stub client (no network, no real LLM)        #
# --------------------------------------------------------------------------- #
def test_inspect_fails_soft_when_client_unavailable(monkeypatch):
    # No client passed and _client() raises (e.g. openai missing / endpoint down):
    # the deterministic buckets survive and ok is False, never an exception.
    def boom():
        raise RuntimeError("no endpoint")
    monkeypatch.setattr(li, "_client", boom)
    res = li.inspect("under [[REF: 18-C §3-401]]", {"18-C §3-401"}, _resolver)
    assert res["ok"] is False
    assert "unavailable" in res["error"]
    assert res["summary"]["invented"] == 0


def test_inspect_field_attaches_scan_safety_net(monkeypatch):
    monkeypatch.setattr(li, "_client", lambda: make_stub('{"verdicts":[]}'))
    # bare cite in prose (no placeholder) + an unresolvable one
    res = mdb.inspect_field("DE-101", "see 18-C §3-203 and 18-C §9-999",
                            fetch_text=False)
    assert "18-C §9-999" in res["scan"]["unresolvable"]


def test_resolver_dead_link_is_bucketed(monkeypatch):
    import urllib.error
    vocab = mdb.build_vocab("DE-101")
    dead_fetch = lambda c: {"cite": c, "text": None, "link_status": "dead", "url": "u"}
    resolve = mdb.make_resolver(vocab, fetch_text=True, fetch=dead_fetch)
    auth = resolve("18-C §3-401")
    assert auth and auth.get("dead_link") is True
    res = li.inspect("under [[REF: 18-C §3-401]]", set(vocab), resolve,
                     client=make_stub('{"verdicts":[]}'), model="m")
    assert res["dead_links"] == ["18-C §3-401"]
    assert res["summary"]["dead_links"] == 1
    assert "[[DEAD LINK: 18-C §3-401]]" in res["substituted"]


def test_resolver_blocked_link_stays_unresolved(monkeypatch):
    vocab = mdb.build_vocab("DE-101")
    blocked = lambda c: {"cite": c, "text": None, "link_status": "blocked", "url": "u"}
    resolve = mdb.make_resolver(vocab, fetch_text=True, fetch=blocked)
    assert resolve("18-C §3-401") is None      # blocked != dead -> unresolved


def test_fetch_link_status_classification():
    import socket
    import urllib.error
    assert fst._link_status(urllib.error.HTTPError("u", 404, "x", {}, None)) == "dead"
    assert fst._link_status(urllib.error.HTTPError("u", 403, "x", {}, None)) == "blocked"
    assert fst._link_status(urllib.error.URLError(socket.gaierror("nx"))) == "dead"
    assert fst._link_status(urllib.error.URLError("timed out")) == "inconclusive"


def test_inspect_requires_a_verdict_for_every_resolved_authority():
    # The inspector returns a verdict for A1 only, while the draft also cites A2.
    # A2 must not pass silently: it is reconciled as unreviewed/unclear so a
    # partially-checked draft still counts toward needs_review. (PR #5 review P1.)
    text = {"A1": "On notice the body shall enter an order.",
            "A2": "The fiduciary may act only after authorization."}
    payload = json.dumps({"verdicts": [{
        "cite": "A1", "supports_conclusion": "pass",
        "quote": "shall enter an order", "rationale": "ok"}]})
    res = li.inspect("[[REF: A1]] and [[REF: A2]]", {"A1", "A2"},
                     lambda k: {"cite": k, "title": k, "url": "u", "text": text[k]},
                     client=make_stub(payload), model="m")
    by = {v["cite"]: v for v in res["verdicts"]}
    assert set(by) == {"A1", "A2"}
    assert by["A2"]["unreviewed"] is True
    assert by["A2"]["supports_conclusion"] == "unclear"
    assert res["summary"]["unclear"] == 1 and res["summary"]["pass"] == 1


def test_inspect_field_offline_flags_invented(monkeypatch):
    payload = json.dumps({"verdicts": [{
        "cite": "18-C §3-401", "supports_conclusion": "pass",
        "quote": "", "rationale": "ok"}]})
    monkeypatch.setattr(li, "_client", lambda: make_stub(payload))
    draft = ("The petitioner has standing under [[REF: 18-C §3-401]], and also "
             "[[REF: Totally_Made_Up]].")
    res = mdb.inspect_field("DE-101", draft, fetch_text=False)
    assert res["form_id"] == "DE-101"
    assert res["invented"] == ["Totally_Made_Up"]
    assert "EXPERIMENTAL" in res["disclaimer"]
