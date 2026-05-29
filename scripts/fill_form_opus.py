#!/usr/bin/env python3
"""Opus-fills a form's fields based on a fact-pattern narrative. Mirror of
fill_form.py but routes through `claude -p --model opus` rather than the
local Qwen vLLM endpoint. No guided_json — Opus is strict enough to follow
the JSON-array instruction reliably, and we strip code fences defensively.

Output JSON shape matches fill_form.py so render_filled.py / eval_filled.py
work unchanged:

    {
      "pattern_id": 1,
      "form_id": "PP-507",
      "filler": "opus",
      "answers": {
        "county_probate_court": {"value": "...", "confidence": 0.95,
                                  "reasoning": "..."},
        ...
      }
    }
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import yaml

PROMPT_TEMPLATE = """You are filling out a Maine probate court form based
on a fact pattern narrative. For EVERY field listed in the form schema,
emit ONE JSON object inside a single JSON array. The output MUST be a
valid JSON array — no prose, no markdown fences, no commentary.

Each array element MUST have exactly these keys:
  {{"field": "<field_id>", "value": "<str>", "confidence": <float 0-1>, "reasoning": "<str>"}}

Rules:
- ALL fields from the form schema must appear, in the same order as the schema.
- value: best answer as a string. Use "" (empty string) if not specified.
- confidence: 0.0-1.0 reflecting evidence in narrative:
    * 0.9-1.0 = explicitly stated in the narrative
    * 0.6-0.85 = strongly implied or reasonable inference
    * 0.3-0.55 = weak inference, partly guessed
    * 0.0-0.25 = pure guess or absent — value SHOULD be ""
- reasoning: 1-2 sentences. Where in the narrative this came from, or why
  it's absent. Keep it brief.

DO NOT confabulate. If the narrative is silent on a field, confidence MUST
be low and value SHOULD be "".

CRITICAL — repeating slot rule. Many forms have repeated slots like
`notice_name_1`, ..., `notice_name_N`. RULE: count actual entities of that
kind in the narrative. If narrative provides K distinct entities and form
has N > K slots, fill slots 1..K and leave slots K+1..N with value="" and
confidence=0.0. NEVER duplicate.

CRITICAL — compound-prompt rule. If a field's prompt asks for two or more
things joined by "and" / "&", you MUST supply ALL components or mark the
field as partial: confidence must be ≤ 0.5.

CRITICAL — currency-cell purity rule. Fields whose name ends in `_amount`,
`_value`, `_enc`, `_encumbrance`, `_fee`, `_cost`, `_price`, or `_balance`,
and any field starting with `gross_value_` / `calc_` / `total_` / `net_`,
hold a SINGLE DOLLAR AMOUNT and nothing else. ALLOWED: "$12,345.67",
"12345.67", "12345", "$0". DISALLOWED: descriptions ("Mortgage to X of
$92,400"), units ("$180/month"), multiple items ("$2,200 (stock), $600
(coins)"), placeholders ("None", "N/A" — use ""), prose. Descriptive
context belongs in the sibling `_desc` / `_specify` field. If multiple
amounts apply and no sibling exists, SUM them.

CRITICAL — enumerated-value rule. If a field's schema row lists a
validator of the form `value_in(a, b, c, ...)`, the field is a radio
button on the form and you MUST emit EXACTLY ONE of the listed canonical
values. Do NOT paraphrase, expand, conjugate, or invent synonyms.
ALLOWED: if `value_in(yes, no)` → emit "yes" or "no" or "". DISALLOWED:
"is_not" / "did" / "was" / "have_not_requested" / "intend_to_share" /
"has_minor_children" / "not_due" / "month" — these are paraphrases of
canonical values that the validator will reject. When the narrative
supports a value but the canonical token differs from the wording, MAP
to the canonical token (e.g. narrative says "lives alone" + enum
includes `alone` → emit "alone"; narrative says "weekly" but field is a
monthly summary + enum is monthly/annually/none → emit "monthly" or
"annually" after converting). Confidence reflects the certainty of the
mapping, not the wording match.

Example shape (your output will be much longer):
[
  {{"field": "county_probate_court", "value": "Cumberland", "confidence": 0.9, "reasoning": "Petitioner lives in Brunswick which is in Cumberland County, Maine."}},
  {{"field": "docket_no", "value": "", "confidence": 0.0, "reasoning": "Docket number is assigned by the court, not by the petitioner."}}
]

=== FORM SCHEMA ===
{form_md}

=== FACT PATTERN {pattern_id} ({complexity}) ===
{narrative}

Output the JSON array now. Cover ALL fields from the schema. Output ONLY
the array — no markdown fences, no leading or trailing text."""


def call_opus(prompt: str, model: str, timeout: int) -> str:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    proc = subprocess.run(
        ["claude", "-p", "--model", model],
        input=prompt, capture_output=True, text=True, env=env,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude -p failed rc={proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.lstrip().startswith(("json", "yaml")):
            text = text.split("\n", 1)[1] if "\n" in text else text
    if text.rstrip().endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def extract_json_array(text: str) -> str:
    """Find the outermost JSON array in text. Opus sometimes adds a
    sentence before/after despite instructions."""
    text = strip_fences(text)
    start = text.find("[")
    if start < 0:
        return text
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("form_md", type=pathlib.Path)
    ap.add_argument("patterns_yaml", type=pathlib.Path)
    ap.add_argument("--pattern-id", type=int, required=True)
    ap.add_argument("--form-id", required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--chunk-size", type=int, default=25,
                    help="Opus handles long contexts better than Qwen so we "
                         "can use larger chunks. Still chunk to keep each "
                         "call latency bounded.")
    ap.add_argument("--save-raw", action="store_true",
                    help="Save raw Opus output beside --out for debugging.")
    ap.add_argument("--no-canonicalize", action="store_true",
                    help="Skip post-fill enum canonicalization. Opus drifts "
                         "less than Qwen but still occasionally; on by "
                         "default for parity with fill_form.py.")
    ap.add_argument("--schema", type=pathlib.Path, default=None,
                    help="Schema path for canonicalization. Defaults to "
                         "repo/forms/<form-id>/schema.json if present.")
    args = ap.parse_args()
    if not args.form_md.exists():
        print(f"missing form: {args.form_md}", file=sys.stderr)
        return 2
    if not args.patterns_yaml.exists():
        print(f"missing patterns: {args.patterns_yaml}", file=sys.stderr)
        return 2
    patterns = yaml.safe_load(args.patterns_yaml.read_text()).get(
        "patterns", [])
    pattern = next((p for p in patterns if p["id"] == args.pattern_id), None)
    if pattern is None:
        print(f"pattern id {args.pattern_id} not in {args.patterns_yaml}",
              file=sys.stderr)
        return 2

    form_md_full = args.form_md.read_text()
    parts = form_md_full.split("\n## ")
    header = parts[0].split("\n## ", 1)
    preamble = header[0] if len(header) == 2 else "# Form\n"
    first_field = "## " + header[1] if len(header) == 2 else ""
    blocks = [first_field] + [f"## {p}" for p in parts[1:]]
    blocks = [b for b in blocks if b.strip().startswith("## ")]
    chunks = []
    for i in range(0, len(blocks), args.chunk_size):
        chunk_md = preamble + "\n\n" + "\n\n".join(
            blocks[i:i + args.chunk_size])
        chunks.append(chunk_md)
    print(f"[fill_form_opus] form={args.form_id} pattern={args.pattern_id} "
          f"complexity={pattern['complexity']} fields={len(blocks)} "
          f"chunks={len(chunks)}", flush=True)

    answers: dict[str, dict] = {}
    bad_lines = 0
    for ci, chunk_md in enumerate(chunks, 1):
        prompt = PROMPT_TEMPLATE.format(
            form_id=args.form_id,
            form_md=chunk_md,
            pattern_id=args.pattern_id,
            complexity=pattern["complexity"],
            narrative=pattern["narrative"],
        )
        try:
            raw = call_opus(prompt, args.model, args.timeout)
        except Exception as e:
            print(f"  chunk {ci}/{len(chunks)} call failed: {e}",
                  file=sys.stderr)
            continue
        if args.save_raw:
            (args.out.with_suffix(f".chunk{ci}.raw.txt")).write_text(raw)
        body = extract_json_array(raw)
        rows: list[dict] = []
        try:
            parsed = json.loads(body)
            if isinstance(parsed, list):
                rows = parsed
            elif isinstance(parsed, dict) and isinstance(
                    parsed.get("answers"), list):
                rows = parsed["answers"]
        except json.JSONDecodeError:
            for line in body.splitlines():
                line = line.strip().rstrip(",")
                if not line or not line.startswith("{"):
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    bad_lines += 1
        chunk_got = 0
        for obj in rows:
            if not isinstance(obj, dict):
                bad_lines += 1
                continue
            fid = obj.get("field")
            if not fid or not isinstance(obj.get("value"), str):
                bad_lines += 1
                continue
            try:
                conf = float(obj.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            answers[fid] = {
                "value": obj.get("value", ""),
                "confidence": conf,
                "reasoning": obj.get("reasoning", ""),
            }
            chunk_got += 1
        print(f"  chunk {ci}/{len(chunks)}: {chunk_got} fields",
              flush=True)
    out_obj = {
        "pattern_id": args.pattern_id,
        "form_id": args.form_id,
        "filler": "opus",
        "answers": answers,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out_obj, indent=2, ensure_ascii=False))
    if not answers:
        print("NO ANSWERS PARSED", file=sys.stderr)
        return 3
    print(f"wrote {args.out} ({len(answers)} fields filled, "
          f"{bad_lines} bad lines)")

    if not args.no_canonicalize:
        schema_path = args.schema or pathlib.Path(
            f"repo/forms/{args.form_id}/schema.json")
        if schema_path.exists():
            try:
                from canonicalize_enums import process as canon_process
            except ImportError:
                sys.path.insert(0, str(pathlib.Path(__file__).parent))
                from canonicalize_enums import process as canon_process
            schema = json.loads(schema_path.read_text())
            new_obj, changes = canon_process(schema, out_obj)
            if changes:
                args.out.write_text(json.dumps(new_obj, indent=2,
                                              ensure_ascii=False))
                print(f"canonicalized {len(changes)} enum value(s):")
                for fid, frm, to, method, _kind, _ch in changes:
                    print(f"  {fid}: {frm!r} -> {to!r} ({method})")
        else:
            print(f"  (skipping canon: no schema at {schema_path})",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
