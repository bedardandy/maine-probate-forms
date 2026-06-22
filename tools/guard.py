#!/usr/bin/env python3
"""Shared citation-guard core for harness integrations (hook + proxy).

One function, :func:`evaluate`, runs the deterministic citation scanner over a
piece of model output and decides whether to *block*: it fires on a leaked cite
(written outside the ``[[REF:]]`` protocol), an unresolvable cite (not in the
trusted index), or a fabricated URL. Deterministic and offline by default — no
LLM, no network — which is what you want inside a blocking hook. With
``llm=True`` (and a ``form_id`` + a configured endpoint) it additionally runs the
full inspector so a mischaracterization (`fail`), `invented`, or `dead_link`
contributes to the decision.

Every evaluation is attested: a signed receipt is produced and, when a log path
is configured (``$ATTEST_LOG`` or ``log_path``), chained — so you can prove the
guard ran on this exact output. Not legal advice.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _reason(scan: dict, result: dict) -> str:
    bits = []
    if scan.get("leaked"):
        bits.append(f"citations written outside the [[REF:]] protocol: {', '.join(scan['leaked'])}")
    if scan.get("unresolvable"):
        bits.append(f"citations not in the trusted index: {', '.join(scan['unresolvable'])}")
    if scan.get("out_of_vocab"):
        bits.append(f"citations outside the form's allowed scope: {', '.join(scan['out_of_vocab'])}")
    if scan.get("fabricated_urls"):
        bits.append(f"fabricated/placeholder URLs: {', '.join(scan['fabricated_urls'])}")
    if result.get("invented"):
        bits.append(f"invented placeholder cites: {', '.join(result['invented'])}")
    if result.get("dead_links"):
        bits.append(f"dead authority links: {', '.join(result['dead_links'])}")
    if (result.get("summary") or {}).get("fail"):
        fails = [v["cite"] for v in result.get("verdicts", [])
                 if v.get("supports_conclusion") == "fail"]
        bits.append(f"conclusions unsupported by the cited authority: {', '.join(fails)}")
    return "Citation guard blocked: " + "; ".join(bits) + "." if bits else ""


def evaluate(text: str, *, form_id: str | None = None, llm: bool = False,
             attest: bool = True, log_path=None) -> dict:
    """Return ``{block, reason, scan, attestation?}`` for a piece of model output."""
    import citation_scan
    result = {"ok": True, "form_id": form_id, "verdicts": [],
              "summary": {"fail": 0, "unresolved": 0, "dead_links": 0, "invented": 0}}
    if llm and form_id:
        import maine_citation_db as mdb
        result = mdb.inspect_field(form_id, text or "", fetch_text=False)
        scan = result.get("scan") or {}
    else:
        scan = citation_scan.report(text or "", form_id=form_id)
        result["scan"] = scan

    block = bool(scan.get("leaked") or scan.get("unresolvable")
                 or scan.get("out_of_vocab") or scan.get("fabricated_urls"))
    if llm:
        s = result.get("summary") or {}
        block = block or s.get("fail", 0) > 0 or bool(result.get("invented")) \
            or bool(result.get("dead_links"))

    out = {"block": block, "reason": _reason(scan, result) if block else "",
           "scan": scan}
    if attest:
        import attest as _attest
        out["attestation"] = _attest.record_inspection(
            text or "", result, tool="citation_guard", log_path=log_path)
    return out
