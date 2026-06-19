#!/usr/bin/env python3
"""Route a plain-language fact pattern to the best Maine probate form.

A single LLM call over a compact, cacheable catalog (catalog/router_catalog.json)
replaces multi-turn repo exploration. The 82 catalog entries (79 base forms plus
3 versioned variants) fit in ~1.5k
tokens, so no embeddings / vector DB are needed. The pick is enum-validated
against the catalog and the call is retried on an empty/invalid response, so any
OpenAI-compatible model can be used safely.

Engine is pluggable via env (defaults to a local vLLM Qwen on :8088):
    ROUTER_BASE_URL   default http://127.0.0.1:8088/v1
    ROUTER_MODEL      default Qwen3.6-27B-FP8
    ROUTER_API_KEY    default "x" (any non-empty string for local servers)
    ROUTER_NO_THINK   "1" to append /no_think (Qwen thinking models) — default 1

Returns {form_id, alternates, confidence, valid}. form_id may be "NONE" when no
form applies. Not legal advice.

    python3 tools/route_form.py "my mother died without a will, I'm her daughter"
    ROUTER_MODEL=google/gemma-4-31b-it ROUTER_BASE_URL=https://openrouter.ai/api/v1 \
        ROUTER_API_KEY=$OPENROUTER_API_KEY python3 tools/route_form.py "..."
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "catalog" / "router_catalog.json"

SYSTEM = (
    "You are a routing classifier for Maine probate court forms. Given a person's "
    "situation, choose the SINGLE best-matching form from the catalog. If NO catalog "
    "form reasonably applies — e.g. the request is not a Maine probate court filing, "
    'or is just a general question — respond with form_id "NONE". Respond with ONLY '
    'compact JSON and nothing else: {"form_id":"<id or NONE>","alternates":["<id>",'
    '"<id>"],"confidence":0.0-1.0}. Every id MUST be copied exactly from the catalog.'
)


def _load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text())


def _extract_json(text: str) -> dict:
    """Return the last valid JSON object, including objects with nesting."""
    text = text or ""
    decoder = json.JSONDecoder()
    candidates = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, length = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((index + length, -index, value))
    if candidates:
        # Prefer the object ending latest in the response. If an outer object
        # and one of its nested objects share an end position, prefer the outer.
        return max(candidates, key=lambda item: (item[0], item[1]))[2]
    return {}


def _client():
    from openai import OpenAI  # local import so importing this module is cheap
    base = os.environ.get("ROUTER_BASE_URL", "http://127.0.0.1:8088/v1")
    key = os.environ.get("ROUTER_API_KEY", "x")
    return OpenAI(base_url=base, api_key=key)


def route(fact: str, catalog: dict | None = None, *, model: str | None = None,
          client=None, retries: int = 4) -> dict:
    """Resolve a fact pattern to a form. Validates the pick against the catalog and
    retries on an empty or out-of-vocabulary response."""
    cat = catalog or _load_catalog()
    valid = set(cat["form_ids"]) | {"NONE"}
    model = model or os.environ.get("ROUTER_MODEL", "Qwen3.6-27B-FP8")
    client = client or _client()
    no_think = os.environ.get("ROUTER_NO_THINK", "1") == "1"
    suffix = " /no_think" if no_think else ""
    extra: dict = {}
    if no_think:
        # works on vLLM Qwen (chat_template_kwargs) and OpenRouter (reasoning)
        extra = {"chat_template_kwargs": {"enable_thinking": False},
                 "reasoning": {"enabled": False}}

    prompt = f"CATALOG:\n{cat['cat_surgical']}\n\nSITUATION: {fact}{suffix}\n\nJSON:"
    last_pick = None
    last_exc = None
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=model, temperature=0, max_tokens=512, timeout=120,
                extra_body=extra,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": prompt}])
            ch = r.choices[0].message
            msg = ch.content or getattr(ch, "reasoning_content", "") or ""
            js = _extract_json(msg)
            pick = js.get("form_id")
            last_pick = pick
            if pick in valid:
                alts = [a for a in (js.get("alternates") or []) if a in valid][:2]
                return {"form_id": pick, "alternates": alts,
                        "confidence": js.get("confidence"), "valid": True}
            # empty or out-of-vocabulary -> retry
        except Exception as e:                  # keep retrying, but keep the why
            last_exc = f"{type(e).__name__}: {e}"
    err = "no valid form_id after retries"
    if last_exc:
        err += f" (last error: {last_exc})"
    return {"form_id": last_pick, "alternates": [], "confidence": None,
            "valid": False, "error": err}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fact", help="plain-language description of the situation")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = route(a.fact)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        cat = _load_catalog()
        titles = {}
        # cheap title lookup from cat_title
        for line in cat["cat_title"].splitlines():
            fid = line.split(" | ", 1)[0]
            titles[fid] = line.split(" | ")[-1]
        fid = res["form_id"]
        print(f"{fid}  {titles.get(fid, '')}  (confidence {res.get('confidence')})")
        for a_ in res["alternates"]:
            print(f"  alt: {a_}  {titles.get(a_, '')}")
        if not res["valid"]:
            print("  [warning] no valid form_id returned — try rephrasing")
    return 0 if res.get("valid") else 1


if __name__ == "__main__":
    sys.exit(main())
