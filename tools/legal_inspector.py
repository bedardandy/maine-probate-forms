#!/usr/bin/env python3
"""Generic, closed-vocabulary legal-citation hallucination inspector.

Provider- and corpus-agnostic core. The idea: never let an LLM draft legal text
from memory. Force it to cite *only* by emitting closed-vocabulary placeholders —
``[[REF: KEY]]`` — where every KEY must come from a supplied allow-list. Then
deterministically substitute each placeholder with the *real* authority text and
run a separate "cold-eyes" inspector LLM that checks, per citation, whether the
draft's conclusion is actually supported by that authority.

Two hard-fail gates make an invented citation impossible to pass silently:

  Gate A (substitute): a ``[[REF: KEY]]`` whose KEY is not in the closed
          vocabulary is recorded as ``invented`` — no model in the loop. This is
          the structural fix for the "let the model make up a descriptive key"
          flaw: even if the draft model ignores the prompt and invents a key, it
          cannot resolve to authority text, so it can never be scored ``pass``.
  Gate B (resolve):   an in-vocabulary KEY whose authority text cannot be
          resolved (e.g. a live statute fetch fails) is recorded as
          ``unresolved`` — visibly distinct from "resolved but unsupported".

The inspector LLM call follows the same pluggable, OpenAI-compatible pattern as
``tools/route_form.py`` (temperature 0, JSON-validated, retried), reading
``INSPECTOR_BASE_URL`` / ``INSPECTOR_MODEL`` / ``INSPECTOR_API_KEY`` and falling
back to the ``ROUTER_*`` values. It is OPT-IN and non-deterministic — never wire
it into the deterministic fill path.

This module knows nothing about Maine, statutes, or PDFs; ``maine_citation_db``
supplies the vocabulary and resolver. Not legal advice.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

# Reuse route_form's robust "last/outer JSON object" extractor.
try:                                    # imported as a tools/ sibling
    from route_form import _extract_json
except ImportError:                     # pragma: no cover - import from elsewhere
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from route_form import _extract_json


PLACEHOLDER = re.compile(r"\[\[REF:\s*(?P<key>[^\]]+?)\s*\]\]")
_VERDICTS = {"pass", "fail", "unclear"}

DRAFT_SYSTEM_HEADER = (
    "You are a legal drafting assistant. Draft the requested analysis, but you are "
    "STRICTLY FORBIDDEN from writing out the text of any statute, rule, regulation, "
    "or case from memory. Whenever you rely on a legal authority you MUST cite it "
    "ONLY by emitting a placeholder of the exact form [[REF: KEY]], copying the KEY "
    "verbatim from the ALLOWED CITATIONS list below. You may use ONLY keys from "
    "that list. If no listed authority fits the point you want to make, say so in "
    "plain words — do NOT invent a key, a citation, or statutory text. Focus on the "
    "logical argument; the exact authority text is filled in from a verified "
    "database, not by you."
)

INSPECT_SYSTEM = (
    "You are a senior legal hallucination inspector. You are given a legal draft in "
    "which every citation has been replaced with the VERBATIM text of the cited "
    "authority, inside blocks delimited by lines '=== AUTHORITY [cite] ===' and "
    "'=== END [cite] ==='. For EACH authority block, judge ONLY against the literal "
    "text shown (use no outside legal knowledge) whether the draft's conclusions "
    "that rely on that authority are actually supported. Respond with ONLY compact "
    "JSON and nothing else: {\"verdicts\":[{\"cite\":\"<the bracketed cite>\","
    "\"supports_conclusion\":\"pass|fail|unclear\",\"quote\":\"<exact span copied "
    "verbatim from the authority text you relied on>\",\"rationale\":\"<one "
    "sentence>\"}]}. 'pass' = the authority's text supports the draft's use of it; "
    "'fail' = the draft mischaracterizes, overstates, or contradicts the authority; "
    "'unclear' = the quoted text is insufficient to tell. The 'quote' MUST be "
    "copied verbatim from inside the authority block, never paraphrased."
)


def extract_refs(draft: str) -> list[str]:
    """Ordered, de-duplicated list of citation KEYs referenced in ``draft``."""
    seen: dict[str, None] = {}
    for m in PLACEHOLDER.finditer(draft or ""):
        seen.setdefault(m.group("key").strip(), None)
    return list(seen)


def draft_system_prompt(vocabulary: dict) -> str:
    """Build the closed-vocabulary draft-generator system prompt.

    ``vocabulary`` maps KEY -> metadata ({title/name, ...}); the keys are
    enumerated so the model can cite only by copying one into a ``[[REF: KEY]]``
    placeholder. The enumeration is advisory — Gate A in :func:`substitute` is the
    actual guarantee that an off-list key can never pass.
    """
    lines = []
    for key, meta in vocabulary.items():
        label = (meta.get("title") or meta.get("name") or "") if isinstance(meta, dict) else ""
        lines.append(f"  [[REF: {key}]]  {label}".rstrip())
    return DRAFT_SYSTEM_HEADER + "\n\nALLOWED CITATIONS:\n" + "\n".join(lines)


def substitute(draft: str, vocabulary, resolver: Callable[[str], Optional[dict]]):
    """Replace every ``[[REF: KEY]]`` placeholder with the cited authority text.

    Uses ``re.sub`` over the captured spans (not ``str.replace``) so duplicate or
    substring keys are handled exactly. ``vocabulary`` is the set of allowed keys;
    ``resolver(key)`` returns an authority ``{cite, text, title?, url?}`` or
    ``None``. Returns ``(substituted_text, citations)`` where each citation record
    carries a ``status`` of ``resolved`` / ``unresolved`` / ``invented``.
    """
    vocabulary = set(vocabulary)
    citations: list[dict] = []
    index: dict[str, dict] = {}        # key -> record (dedupe repeated cites)

    def _block(rec: dict) -> str:
        cite = rec.get("cite", rec["key"])
        if rec["status"] == "resolved":
            title = rec.get("title")
            head = f"\n=== AUTHORITY [{cite}] ===\n"
            if title:
                head += f"Title: {title}\n"
            return head + rec["text"].strip() + f"\n=== END [{cite}] ===\n"
        if rec["status"] == "dead_link":
            return f"[[DEAD LINK: {rec['key']}]]"
        if rec["status"] == "unresolved":
            return f"[[UNRESOLVED: {rec['key']}]]"
        return f"[[INVENTED: {rec['key']}]]"

    def _repl(m: re.Match) -> str:
        key = m.group("key").strip()
        rec = index.get(key)
        if rec is None:
            if key not in vocabulary:                 # Gate A
                rec = {"key": key, "status": "invented"}
            else:
                try:
                    auth = resolver(key)
                except Exception as exc:              # treat as unresolved, keep why
                    auth = None
                    rec = {"key": key, "status": "unresolved",
                           "error": f"{type(exc).__name__}: {exc}"}
                if rec is None:
                    if auth and not auth.get("text") and auth.get("dead_link"):
                        rec = {"key": key, "status": "dead_link"}
                        for k in ("cite", "title", "url"):
                            if auth.get(k):
                                rec[k] = auth[k]
                    elif not auth or not auth.get("text"):   # Gate B
                        rec = {"key": key, "status": "unresolved"}
                        if auth:
                            for k in ("cite", "title", "url"):
                                if auth.get(k):
                                    rec[k] = auth[k]
                    else:
                        rec = {"key": key, "status": "resolved",
                               "cite": auth.get("cite", key),
                               "title": auth.get("title"),
                               "url": auth.get("url"),
                               "text_verified": auth.get("text_verified"),
                               "text": auth["text"]}
            index[key] = rec
            citations.append(rec)
        return _block(rec)

    return PLACEHOLDER.sub(_repl, draft or ""), citations


def _client():
    from openai import OpenAI       # local import so importing this module is cheap
    base = (os.environ.get("INSPECTOR_BASE_URL")
            or os.environ.get("ROUTER_BASE_URL", "http://127.0.0.1:8088/v1"))
    key = (os.environ.get("INSPECTOR_API_KEY")
           or os.environ.get("ROUTER_API_KEY", "x"))
    return OpenAI(base_url=base, api_key=key)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _validate_verdicts(raw: list, auth_by_cite: dict) -> list[dict]:
    """Coerce model verdicts into the structured shape and ground each ``quote``.

    A verdict whose ``quote`` is not actually contained (case/space-insensitive) in
    the resolved authority text is marked ``quote_grounded=False``; if such a
    verdict claimed ``pass`` it is downgraded to ``unclear`` — a fabricated
    supporting quote is exactly the hallucination this tool exists to catch.
    """
    out = []
    for v in raw:
        if not isinstance(v, dict):
            continue
        cite = v.get("cite")
        verdict = v.get("supports_conclusion")
        if verdict not in _VERDICTS:
            verdict = "unclear"
        quote = v.get("quote") or ""
        auth = auth_by_cite.get(cite)
        grounded = True
        if quote and auth is not None:
            if _norm(quote) not in _norm(auth.get("text", "")):
                grounded = False
                if verdict == "pass":
                    verdict = "unclear"
        out.append({
            "cite": cite,
            "supports_conclusion": verdict,
            "quote": quote,
            "quote_grounded": grounded,
            "rationale": v.get("rationale"),
            "resolved": auth is not None,
        })
    return out


def _add_unreviewed(verdicts: list[dict], resolved: list[dict]) -> None:
    """Require a verdict for *every* resolved authority. If the inspector omitted
    one (returned verdicts for a subset of the cited authorities), record it as
    ``unreviewed``/``unclear`` so a partially-checked draft can't report clean —
    the missing authority still counts toward ``needs_review``.
    """
    seen = {v.get("cite") for v in verdicts}
    for c in resolved:
        cite = c.get("cite", c["key"])
        if cite not in seen and c["key"] not in seen:
            verdicts.append({
                "cite": cite,
                "supports_conclusion": "unclear",
                "quote": "",
                "quote_grounded": False,
                "rationale": "inspector returned no verdict for this citation",
                "resolved": True,
                "unreviewed": True,
            })


def _summary(result: dict) -> dict:
    counts = {"pass": 0, "fail": 0, "unclear": 0}
    for v in result.get("verdicts", []):
        counts[v["supports_conclusion"]] = counts.get(v["supports_conclusion"], 0) + 1
    counts["unresolved"] = len(result.get("unresolved", []))
    counts["dead_links"] = len(result.get("dead_links", []))
    counts["invented"] = len(result.get("invented", []))
    return counts


def inspect(draft: str, vocabulary, resolver: Callable[[str], Optional[dict]], *,
            model: str | None = None, client=None, retries: int = 4) -> dict:
    """Substitute citations, then score each with the inspector LLM.

    Returns ``{ok, substituted, citations, verdicts, invented, unresolved,
    dead_links, summary}``. ``invented`` / ``unresolved`` / ``dead_links`` are
    populated deterministically (no LLM); ``verdicts`` carry the per-citation
    ``supports_conclusion`` plus the grounded ``quote``. ``ok`` is ``False`` only
    when the LLM call could not be completed — the *content* findings (fail /
    invented / unresolved / dead_link) live in the summary, for the caller to gate
    on.
    """
    substituted, citations = substitute(draft, vocabulary, resolver)
    resolved = [c for c in citations if c["status"] == "resolved"]
    result = {
        "ok": True,
        "substituted": substituted,
        "citations": citations,
        "invented": [c["key"] for c in citations if c["status"] == "invented"],
        "unresolved": [c["key"] for c in citations if c["status"] == "unresolved"],
        "dead_links": [c["key"] for c in citations if c["status"] == "dead_link"],
        "verdicts": [],
    }
    if not resolved:
        result["note"] = "no resolved citations to inspect"
        result["summary"] = _summary(result)
        return result

    auth_by_cite: dict[str, dict] = {}
    for c in resolved:
        auth_by_cite[c.get("cite", c["key"])] = c
        auth_by_cite[c["key"]] = c

    model = (model or os.environ.get("INSPECTOR_MODEL")
             or os.environ.get("ROUTER_MODEL", "Qwen3.6-27B-FP8"))
    if client is None:
        try:                              # fail soft: keep the deterministic findings
            client = _client()
        except Exception as e:
            result["ok"] = False
            result["error"] = f"inspector client unavailable: {type(e).__name__}: {e}"
            result["summary"] = _summary(result)
            return result
    no_think = os.environ.get("ROUTER_NO_THINK", "1") == "1"
    extra: dict = {}
    if no_think:
        extra = {"chat_template_kwargs": {"enable_thinking": False},
                 "reasoning": {"enabled": False}}

    prompt = ("LEGAL DRAFT (each citation replaced with the verbatim authority "
              f"text):\n\n{substituted}\n\nJSON:")
    last_exc = None
    for _ in range(retries):
        try:
            r = client.chat.completions.create(
                model=model, temperature=0, max_tokens=1500, timeout=120,
                extra_body=extra,
                messages=[{"role": "system", "content": INSPECT_SYSTEM},
                          {"role": "user", "content": prompt}])
            ch = r.choices[0].message
            msg = ch.content or getattr(ch, "reasoning_content", "") or ""
            raw = _extract_json(msg).get("verdicts")
            if isinstance(raw, list) and raw:
                verdicts = _validate_verdicts(raw, auth_by_cite)
                _add_unreviewed(verdicts, resolved)
                result["verdicts"] = verdicts
                result["summary"] = _summary(result)
                return result
        except Exception as e:                  # keep retrying, keep the why
            last_exc = f"{type(e).__name__}: {e}"
    result["ok"] = False
    result["error"] = "no valid verdicts after retries" + (
        f" (last error: {last_exc})" if last_exc else "")
    result["summary"] = _summary(result)
    return result
