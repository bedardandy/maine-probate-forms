#!/usr/bin/env python3
"""Tier-1 semantic filter for multiline-below candidates (LLM proposes, human disposes).

detect_multiline_below.py seeds narrative single-line fields that have room for a
box below; many are still SHORT values (a narrative-sourced date, a one-line
relationship, a town). This asks a local model, per field, whether the answer is
an open-ended paragraph (-> wants a multi-line box) or a short value (-> leave as
one line). It is a FILTER feeding the human poll, not a final verdict, so one
model (Qwen by default) is enough; low-confidence still goes to the poll.

Endpoints come from $GEOM_LLM = "base_url|model" (OpenAI-style /chat/completions),
e.g. GEOM_LLM="http://localhost:8088/v1|Qwen3.6-27B-FP8". No endpoints in-repo.

    GEOM_LLM="http://localhost:8088/v1|Qwen3.6-27B-FP8" \
      python3 scripts/geometry_review/classify_multiline.py --fits-only
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "expects_paragraph": {"type": "boolean"},
        "kind": {"type": "string", "enum": [
            "short_value", "name_or_id", "date_or_number", "address",
            "description", "list", "explanation_or_reasons"]},
        "confidence": {"type": "number"},
    },
    "required": ["expects_paragraph", "kind", "confidence"],
}

PROMPT = """You are auditing a fillable government PDF form field on a Maine \
probate form. Decide what KIND of answer the field expects, to choose its box \
shape.

Form: {form}
Field id: {field}
Field label: {label}
Printed prompt text next to/above the blank: "{prompt}"

A field "expects_paragraph" = true ONLY if the natural answer is open-ended and \
usually spans multiple lines: a description of property, a list of people, an \
explanation, reasons, circumstances, narrative details. It is false for short \
values: a person's name, a date, a number, an age, a single address, a one-word \
or one-phrase answer, a relationship, a yes/no.

Reply with JSON only: expects_paragraph (bool), kind, confidence (0-1)."""


def classify(url, model, rec) -> dict:
    body = {
        "model": model, "max_tokens": 200, "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema":
                            {"name": "shape", "strict": True, "schema": SCHEMA}},
        "messages": [{"role": "user", "content": PROMPT.format(
            form=rec["form"], field=rec["field"], label=rec.get("label") or "",
            prompt=(rec.get("prompt") or "")[:120])}],
    }
    try:
        r = requests.post(url.rstrip("/") + "/chat/completions", json=body,
                          timeout=180)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"].get("content") or ""
    except Exception as e:
        return {"error": str(e)[:120]}
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return {"error": f"no json: {content[:80]}"}
    try:
        return json.loads(m.group(0).replace("“", '"').replace("”", '"'))
    except Exception:
        return {"error": f"bad json: {m.group(0)[:80]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--fits-only", action="store_true",
                    help="only classify candidates with room for a box")
    ap.add_argument("--name", default="multiline_classified.jsonl",
                    help="output filename (use distinct names per voter)")
    args = ap.parse_args()
    spec = os.environ.get("GEOM_LLM")
    if not spec or "|" not in spec:
        sys.exit('set GEOM_LLM="base_url|model" (no endpoints in-repo)')
    url, model = spec.split("|", 1)

    cand = [json.loads(l) for l in
            (args.out / "multiline_candidates.jsonl").open()]
    if args.fits_only:
        cand = [c for c in cand if c.get("fits_box")]
    outp = args.out / args.name
    fh = outp.open("w")
    n_par = 0
    for i, rec in enumerate(cand, 1):
        v = classify(url, model, rec)
        rec["shape"] = v
        if v.get("expects_paragraph"):
            n_par += 1
        fh.write(json.dumps(rec) + "\n")
        tag = ("PARA" if v.get("expects_paragraph") else
               ("err" if "error" in v else "short"))
        print(f"  [{i:3}/{len(cand)}] {rec['form']:9} {rec['field'][:30]:30} "
              f"{tag:5} {v.get('kind','')}/{v.get('confidence','')}")
    fh.close()
    print(f"\nclassified {len(cand)} -> {n_par} expects_paragraph; wrote {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
