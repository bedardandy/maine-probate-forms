#!/usr/bin/env python3
"""Tamper-evident attestation that the citation inspector actually ran.

A guard you can't prove ran is only as trustworthy as the operator's word. This
emits a signed, independently-verifiable *receipt* for each inspection and chains
receipts into an append-only log, so you can later prove: (a) it ran, (b) on this
exact input, (c) with nothing suppressed, and (d) let anyone re-check.

    receipt   = {schema, tool, git_commit, config_digest, model, input_sha256,
                 summary, findings, verdict_digest, needs_review, timestamp, nonce}
    signed    = {receipt, alg, signature}          # HMAC-SHA256 over canonical JSON
    log entry = {seq, time, prev_hash, receipt_hash, signed}  # hash-chained JSONL

The signing key (``$ATTEST_HMAC_KEY``) is held by the operator, NOT the agent, so
a model can't forge "it passed". Without a key the receipt is still produced and
chained (a record), but flagged ``signed: false``. The *deterministic* findings
(invented / unresolved / dead_link / fabricated_url / leaked) are reproducible —
re-run the scanner on the same input to confirm them independently. Note: a
receipt proves the inspector ran and what it found; it does not prove the agent
heeded a failure — only a fail-closed gate does that. Not legal advice.

    python3 tools/attest.py verify receipt.json --input draft.txt
    python3 tools/attest.py verify-log inspection_log.jsonl
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
IDX = ROOT / "docs" / "statute-reference" / "_index"

SCHEMA = "maine-citation-attestation/v1"
GENESIS = "0" * 64


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _git_commit() -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:
        return None


def config_digest() -> str:
    """Hash of the inputs that determine the vocabulary/extraction, so a receipt
    records WHICH index revision produced it."""
    h = hashlib.sha256()
    for name in ("18c-sections.json", "caselaw.json", "cross-refs.json"):
        p = IDX / name
        if p.exists():
            h.update(p.read_bytes())
    try:
        import fetch_statute_text as fst
        h.update(str(fst.EXTRACTOR_VERSION).encode())
    except Exception:
        pass
    return h.hexdigest()[:16]


def _findings(result: dict) -> dict:
    scan = result.get("scan") or {}
    summ = result.get("summary") or {}
    return {
        "fail": summ.get("fail", 0),
        "invented": result.get("invented", []),
        "unresolved": result.get("unresolved", []),
        "dead_links": result.get("dead_links", []),
        "leaked": scan.get("leaked", []),
        "fabricated_urls": scan.get("fabricated_urls", []),
        "out_of_vocab": scan.get("out_of_vocab", []),
    }


def needs_review(result: dict) -> bool:
    f = _findings(result)
    return (not result.get("ok", True)) or f["fail"] > 0 or any(
        f[k] for k in ("invented", "unresolved", "dead_links", "leaked",
                       "fabricated_urls"))


def make_receipt(input_text: str, result: dict, *, tool: str = "inspect_citations",
                 model: str | None = None) -> dict:
    return {
        "schema": SCHEMA,
        "tool": tool,
        "git_commit": _git_commit(),
        "config_digest": config_digest(),
        "model": model or result.get("model") or os.environ.get("INSPECTOR_MODEL"),
        "form_id": result.get("form_id"),
        "input_sha256": sha256_text(input_text),
        "summary": result.get("summary"),
        "findings": _findings(result),
        "verdict_digest": sha256_text(canonical(result.get("verdicts", []))),
        "needs_review": needs_review(result),
        "ok": result.get("ok", True),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "nonce": secrets.token_hex(16),
    }


def _key(key=None) -> bytes | None:
    if key is not None:
        return key if isinstance(key, bytes) else key.encode("utf-8")
    env = os.environ.get("ATTEST_HMAC_KEY")
    return env.encode("utf-8") if env else None


def sign_receipt(receipt: dict, *, key=None) -> dict:
    k = _key(key)
    payload = canonical(receipt)
    if k:
        sig = hmac.new(k, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return {"receipt": receipt, "alg": "HMAC-SHA256", "signature": sig, "signed": True}
    return {"receipt": receipt, "alg": "none", "signature": sha256_text(payload),
            "signed": False}


def verify_receipt(signed: dict, *, key=None, input_text: str | None = None):
    """Return ``(ok, detail)``. Checks the signature (or content hash if unsigned)
    and, when ``input_text`` is given, that it matches ``receipt.input_sha256``."""
    receipt = signed.get("receipt", {})
    payload = canonical(receipt)
    if signed.get("signed"):
        k = _key(key)
        if not k:
            return False, "receipt is signed but no key ($ATTEST_HMAC_KEY) to verify"
        expect = hmac.new(k, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, signed.get("signature", "")):
            return False, "signature mismatch (tampered or wrong key)"
    elif sha256_text(payload) != signed.get("signature"):
        return False, "content hash mismatch (unsigned receipt was altered)"
    if input_text is not None and sha256_text(input_text) != receipt.get("input_sha256"):
        return False, "input does not match receipt.input_sha256"
    return True, "ok"


def _hash_line(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def append_log(signed: dict, log_path) -> dict:
    """Append a hash-chained entry; each entry pins the previous raw line's hash."""
    p = pathlib.Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    prev_hash, seq = GENESIS, 0
    if p.exists():
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            prev_hash = _hash_line(lines[-1])
            seq = json.loads(lines[-1]).get("seq", len(lines) - 1) + 1
    entry = {
        "seq": seq,
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "prev_hash": prev_hash,
        "receipt_hash": sha256_text(canonical(signed["receipt"])),
        "signed": signed,
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(canonical(entry) + "\n")
    return entry


def verify_log(log_path, *, key=None):
    """Walk the chain: every ``prev_hash`` must match the prior raw line, every
    receipt must verify, every ``receipt_hash`` must match. Returns ``(ok, problems)``."""
    p = pathlib.Path(log_path)
    if not p.exists():
        return False, [f"no log at {p}"]
    problems, prev_hash = [], GENESIS
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        try:
            entry = json.loads(ln)
        except Exception as e:
            problems.append(f"entry {i}: not JSON ({e})")
            prev_hash = _hash_line(ln)
            continue
        if entry.get("prev_hash") != prev_hash:
            problems.append(f"entry {i} (seq {entry.get('seq')}): chain break")
        signed = entry.get("signed", {})
        ok, detail = verify_receipt(signed, key=key)
        if not ok:
            problems.append(f"entry {i}: {detail}")
        if sha256_text(canonical(signed.get("receipt", {}))) != entry.get("receipt_hash"):
            problems.append(f"entry {i}: receipt_hash mismatch")
        prev_hash = _hash_line(ln)
    return (not problems), problems


def record_inspection(input_text: str, result: dict, *, tool: str = "inspect_citations",
                      model: str | None = None, log_path=None, key=None) -> dict:
    """Build + sign a receipt and (if a log path is configured) chain it. Returns
    the signed receipt, with a ``log`` pointer when appended."""
    signed = sign_receipt(make_receipt(input_text, result, tool=tool, model=model),
                          key=key)
    log_path = log_path or os.environ.get("ATTEST_LOG") or None
    if log_path:
        entry = append_log(signed, log_path)
        signed["log"] = {"seq": entry["seq"], "path": str(log_path)}
    return signed


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="verify a signed receipt JSON")
    v.add_argument("receipt")
    v.add_argument("--input", help="bind-check: the text that was inspected")
    vl = sub.add_parser("verify-log", help="verify a hash-chained log")
    vl.add_argument("log")
    sub.add_parser("show-config", help="print git_commit + config_digest")
    a = ap.parse_args()

    if a.cmd == "verify":
        signed = json.loads(pathlib.Path(a.receipt).read_text(encoding="utf-8"))
        intext = pathlib.Path(a.input).read_text(encoding="utf-8") if a.input else None
        ok, detail = verify_receipt(signed, input_text=intext)
        print(f"{'OK' if ok else 'FAIL'} — {detail}")
        return 0 if ok else 1
    if a.cmd == "verify-log":
        ok, problems = verify_log(a.log)
        if ok:
            print("OK — log chain intact and all receipts verify")
        else:
            for p in problems:
                print("  - " + p, file=sys.stderr)
            print(f"FAIL — {len(problems)} problem(s)", file=sys.stderr)
        return 0 if ok else 1
    if a.cmd == "show-config":
        print(json.dumps({"git_commit": _git_commit(),
                          "config_digest": config_digest()}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
