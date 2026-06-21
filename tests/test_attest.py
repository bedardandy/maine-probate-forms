"""Offline tests for attestation (signed receipts + hash-chained log) and guard."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import attest                               # noqa: E402
import guard                                # noqa: E402


_RESULT = {
    "ok": True, "form_id": "DE-101",
    "summary": {"fail": 1, "unresolved": 0, "dead_links": 0, "invented": 1},
    "invented": ["Made_Up"], "unresolved": [], "dead_links": [],
    "verdicts": [{"cite": "18-C §3-401", "supports_conclusion": "fail"}],
    "scan": {"leaked": ["18-C §3-203"], "unresolvable": [], "fabricated_urls": []},
}


# --- receipt + signature ---------------------------------------------------- #
def test_receipt_binds_input_and_findings():
    r = attest.make_receipt("the draft text", _RESULT)
    assert r["schema"] == attest.SCHEMA
    assert r["input_sha256"] == attest.sha256_text("the draft text")
    assert r["needs_review"] is True
    assert r["findings"]["invented"] == ["Made_Up"]


def test_sign_and_verify_roundtrip_hmac():
    key = b"operator-secret"
    signed = attest.sign_receipt(attest.make_receipt("draft", _RESULT), key=key)
    assert signed["signed"] is True
    ok, detail = attest.verify_receipt(signed, key=key)
    assert ok, detail


def test_tampered_receipt_fails_signature():
    key = b"operator-secret"
    signed = attest.sign_receipt(attest.make_receipt("draft", _RESULT), key=key)
    signed["receipt"]["needs_review"] = False           # forge "it passed"
    ok, detail = attest.verify_receipt(signed, key=key)
    assert not ok and "mismatch" in detail


def test_wrong_key_fails():
    signed = attest.sign_receipt(attest.make_receipt("draft", _RESULT), key=b"k1")
    ok, _ = attest.verify_receipt(signed, key=b"k2")
    assert not ok


def test_unsigned_receipt_is_tamper_evident():
    signed = attest.sign_receipt(attest.make_receipt("draft", _RESULT), key=None)
    assert signed["signed"] is False
    assert attest.verify_receipt(signed)[0] is True
    signed["receipt"]["needs_review"] = False
    assert attest.verify_receipt(signed)[0] is False


def test_input_binding_check():
    key = b"k"
    signed = attest.sign_receipt(attest.make_receipt("the real draft", _RESULT), key=key)
    assert attest.verify_receipt(signed, key=key, input_text="the real draft")[0]
    ok, detail = attest.verify_receipt(signed, key=key, input_text="a different draft")
    assert not ok and "input" in detail


# --- hash-chained log ------------------------------------------------------- #
def test_log_chain_appends_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTEST_HMAC_KEY", "secret")
    log = tmp_path / "inspection_log.jsonl"
    for t in ("draft one", "draft two", "draft three"):
        attest.record_inspection(t, _RESULT, log_path=str(log))
    ok, problems = attest.verify_log(str(log))
    assert ok, problems
    assert len([ln for ln in log.read_text().splitlines() if ln.strip()]) == 3


def test_log_detects_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTEST_HMAC_KEY", "secret")
    log = tmp_path / "log.jsonl"
    attest.record_inspection("a", _RESULT, log_path=str(log))
    attest.record_inspection("b", _RESULT, log_path=str(log))
    lines = log.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["signed"]["receipt"]["needs_review"] = False  # tamper with entry 0
    lines[0] = attest.canonical(entry)
    log.write_text("\n".join(lines) + "\n")
    ok, problems = attest.verify_log(str(log))
    assert not ok and problems                          # signature + chain break


def test_record_inspection_returns_signed_with_log_pointer(tmp_path):
    log = tmp_path / "l.jsonl"
    signed = attest.record_inspection("x", _RESULT, log_path=str(log))
    assert signed["log"]["seq"] == 0 and signed["receipt"]["input_sha256"]


# --- guard core (shared by hook + proxy) ------------------------------------ #
def test_guard_blocks_on_fabricated_url_offline():
    res = guard.evaluate("cite https://example.com/x and 18-C §9-999", attest=False)
    assert res["block"] is True
    assert "fabricated" in res["reason"].lower() or "index" in res["reason"].lower()


def test_guard_allows_clean_text():
    res = guard.evaluate("The petitioner has standing.", attest=False)
    assert res["block"] is False and res["reason"] == ""


def test_guard_attests_when_requested(tmp_path):
    res = guard.evaluate("see 18-C §9-999", log_path=str(tmp_path / "g.jsonl"))
    assert res["attestation"]["receipt"]["tool"] == "citation_guard"
    assert (tmp_path / "g.jsonl").exists()


# --- OpenAI-compatible proxy core ------------------------------------------- #
def _resp(text):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}]}


def test_proxy_annotates_and_fails_closed():
    import inspect_proxy
    body = inspect_proxy.apply_guard(_resp("rely on https://example.com/fake ruling"),
                                     fail_closed=True)
    insp = body["x_citation_inspection"]
    assert insp["flagged"] is True and insp["blocked"] is True
    assert "withheld" in body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "content_filter"
    assert insp["attestation"]["receipt"]["tool"] == "citation_guard"


def test_proxy_passes_clean_response_through():
    import inspect_proxy
    body = inspect_proxy.apply_guard(_resp("A plain sentence."), fail_closed=True)
    assert body["x_citation_inspection"]["flagged"] is False
    assert body["choices"][0]["message"]["content"] == "A plain sentence."
