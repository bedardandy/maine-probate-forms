#!/usr/bin/env python3
"""Fill a form's fields based on a fact-pattern narrative using a local LLM
(default Qwen3.6-27B-FP8 at localhost:8088).

For every field in the form, the model emits:
  - value: best answer or "" if absent
  - confidence: 0.0-1.0 reflecting how well the fact pattern supports it
  - reasoning: brief justification

Output JSON shape:
    {
      "pattern_id": 1,
      "form_id": "PP-507",
      "answers": {
        "county_probate_court": {"value": "Cumberland", "confidence": 0.95,
                                  "reasoning": "Petitioner lives in Brunswick..."},
        ...
      }
    }
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import sys
import urllib.request
import yaml

PROMPT_TEMPLATE = """You are filling out a Maine probate court form based
on a fact pattern narrative. For EVERY field listed in the form schema,
emit ONE JSON OBJECT PER LINE (JSONL format). Each line is:

  {{"field": "<field_id>", "value": "<str>", "confidence": <float>, "reasoning": "<str>"}}

Rules:
- One field per line. No commas between lines. No wrapping array or object.
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
`notice_name_1`, `notice_name_2`, ..., `notice_name_N`, or `heir_address_1`,
`heir_address_2`, etc. RULE: count the actual entities of that kind in the
narrative. If the narrative provides K distinct entities and the form has
N > K slots, fill slots 1..K and leave slots K+1..N with value="" and
confidence=0.0. NEVER duplicate an earlier entity into a later slot, and
NEVER stitch one entity's name onto another entity's address. Index
discipline is mandatory — slot 7's data must come from the 7th entity in
the narrative, not slot 1's data recycled.

CRITICAL — compound-prompt rule. If a single field's prompt asks for two
or more things joined by "and" / "&" (e.g. "name and address",
"name and title", "date and time"), you MUST supply ALL components or
mark the field as partial: value should be the parts you can support,
confidence must be ≤ 0.5, and reasoning must call out which component is
missing. Do not silently drop a component.

CRITICAL — currency-cell purity rule. Fields whose name ends in `_amount`,
`_value`, `_enc`, `_encumbrance`, `_fee`, `_cost`, `_price`, or `_balance`,
and any field starting with `gross_value_` / `calc_` / `total_` / `net_`,
hold a SINGLE DOLLAR AMOUNT and nothing else. ALLOWED formats:
"$12,345.67", "12345.67", "12345", "$0". DISALLOWED:
  - description + amount: "Mortgage to Bangor Savings of $92,400"
  - unit qualifiers: "$180/month", "$25 per visit"
  - multiple items: "$2,200 (stock), $600 (coins)"
  - placeholder words: "None", "N/A", "TBD"  (use "" instead)
  - prose: "Income: $2,400/month SSI and $42,000 trust..."
Descriptive context belongs in the sibling `_desc` / `_description` /
`_specify` field, not in the currency cell. If the form has no such
sibling and the narrative provides multiple amounts, sum them and put
the total in the currency cell — note the breakdown in `reasoning`.

CRITICAL — enumerated-value rule. If a field's schema row lists a
validator of the form `value_in(a, b, c, ...)`, the field is a radio
button on the form and you MUST emit EXACTLY ONE of the listed canonical
values. Do NOT paraphrase, expand, conjugate, or invent synonyms.
ALLOWED: `value_in(yes, no)` → emit "yes" or "no" or "". DISALLOWED:
"is_not" / "did" / "was" / "have_not_requested" / "intend_to_share" /
"has_minor_children" / "not_due" / "month" — these are paraphrases of
canonical values that the validator will reject. When the narrative
supports a value but the canonical token differs from the wording, MAP
to the canonical token (e.g. narrative says "lives alone" + enum
includes `alone` → emit "alone"; narrative says "weekly paycheck" but
form's `*_period` enum is weekly/biweekly/semi_monthly/monthly/annually/none
→ emit "weekly"). Confidence reflects the certainty of the mapping, not
the wording match.

Output a JSON ARRAY of objects (NOT JSONL, NOT a flat dict).

Example (3-field excerpt — your output will be much longer):
[
  {{"field": "county_probate_court", "value": "Cumberland", "confidence": 0.9, "reasoning": "Petitioner lives in Brunswick which is in Cumberland County, Maine."}},
  {{"field": "docket_no", "value": "", "confidence": 0.0, "reasoning": "Docket number is assigned by the court, not by the petitioner."}},
  {{"field": "respondent_name", "value": "Margaret Holloway", "confidence": 0.95, "reasoning": "Narrative states the respondent is Margaret Holloway."}}
]

The output schema is enforced by the server — your response is constrained
to this structure. Cover EVERY field id from the form schema, in schema order.

=== FORM SCHEMA ===
{form_md}

=== FACT PATTERN {pattern_id} ({complexity}) ===
{narrative}
{prior_answers_block}
Output the JSON array now. Cover ALL fields from the schema."""


ANSWER_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "field": {"type": "string"},
            "value": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reasoning": {"type": "string"},
        },
        "required": ["field", "value", "confidence", "reasoning"],
        "additionalProperties": False,
    },
}


def call_qwen(url: str, model: str, prompt: str, *,
              max_tokens: int, temperature: float,
              field_count: int) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        # vLLM guided JSON enforces the response matches this schema.
        # Critical because Qwen otherwise collapses long-context outputs
        # to a flat {field: value} dict, losing confidence + reasoning.
        "extra_body": {"guided_json": ANSWER_SCHEMA},
        # Some vLLM builds put guided_json at top level too:
        "guided_json": ANSWER_SCHEMA,
    }
    req = urllib.request.Request(
        f"{url}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        d = json.load(resp)
    msg = d["choices"][0]["message"]
    # Qwen-thinking puts the final answer in "content"; if absent, fall back
    # to "reasoning" (some Qwen builds emit content there).
    return msg.get("content") or msg.get("reasoning") or ""


SLOT_RE = re.compile(r"^(.+?)_(\d+)(?:_(.+))?$")


def build_prior_answers_block(answers: dict[str, dict]) -> str:
    """Recap slot-pattern fields already placed in earlier chunks.

    The LLM otherwise can't follow the "no duplicate slot" rule, because
    each chunk call is a fresh context. Listing the placed slot rows by
    family (e.g. tang_prop_*_desc) keeps later chunks from re-emitting
    items 1..K when the form's slots K+1..N are still empty.
    """
    if not answers:
        return ""
    by_family: dict[str, list[tuple[int, str, str]]] = {}
    for fid, a in answers.items():
        m = SLOT_RE.match(fid)
        if not m:
            continue
        val = (a.get("value") if isinstance(a, dict) else a) or ""
        if not val.strip():
            continue
        prefix, idx, suffix = m.group(1), int(m.group(2)), m.group(3) or ""
        # Group by the "anchor" suffix (typically _desc / _name) so each
        # entity occupies one line of the recap. Skip the _value / _enc /
        # _address siblings — listing the primary descriptor is enough
        # for the LLM to recognize the item.
        if suffix and suffix not in ("desc", "description", "name"):
            continue
        family = f"{prefix}_*" + (f"_{suffix}" if suffix else "")
        by_family.setdefault(family, []).append((idx, fid, val))
    if not by_family:
        return ""
    lines = ["", "=== ALREADY PLACED IN EARLIER CHUNKS ===",
             "These slots are filled. DO NOT re-emit the same entity into "
             "a later slot — leave later slots empty (value=\"\", "
             "confidence=0.0) if no new entity remains in the narrative."]
    for family in sorted(by_family):
        lines.append(f"  {family}:")
        for idx, fid, val in sorted(by_family[family]):
            short = val if len(val) <= 70 else val[:67] + "..."
            lines.append(f"    [{idx}] {short}")
    return "\n".join(lines) + "\n"


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Drop leading ```json or ```
        text = text.split("\n", 1)[1] if "\n" in text else text
    if text.rstrip().endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("form_md", type=pathlib.Path)
    ap.add_argument("patterns_yaml", type=pathlib.Path)
    ap.add_argument("--pattern-id", type=int, required=True)
    ap.add_argument("--form-id", required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--url", default="http://localhost:8088")
    ap.add_argument("--model", default="Qwen3.6-27B-FP8")
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--chunk-size", type=int, default=20,
                    help="Split form into chunks of N fields. Qwen "
                         "follows the JSON schema reliably at short "
                         "contexts but collapses to flat dicts on long "
                         "ones — chunking gets reliable schema-compliant "
                         "answers at the cost of N calls per pattern.")
    ap.add_argument("--no-canonicalize", action="store_true",
                    help="Skip the post-fill enum canonicalization pass. "
                         "By default, canonicalize_enums.py runs on the "
                         "output to fix Qwen's value_in paraphrase drift "
                         "(true→yes, month→monthly, etc.); pass this flag "
                         "to inspect the raw model output.")
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
        "patterns", []
    )
    pattern = next((p for p in patterns if p["id"] == args.pattern_id), None)
    if pattern is None:
        print(f"pattern id {args.pattern_id} not in {args.patterns_yaml}",
              file=sys.stderr)
        return 2

    form_md_full = args.form_md.read_text()
    # Split form into chunks of N fields each. Markdown sections start
    # with "## " — find chunk boundaries by counting headers.
    parts = form_md_full.split("\n## ")
    # parts[0] is the "# Form X" preamble + first field; rejoin into
    # field-blocks where each block starts with "## ".
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
    print(f"[fill_form] form={args.form_id} pattern={args.pattern_id} "
          f"complexity={pattern['complexity']} fields={len(blocks)} "
          f"chunks={len(chunks)}")

    answers: dict[str, dict] = {}
    bad_lines = 0
    for ci, chunk_md in enumerate(chunks, 1):
        prompt = PROMPT_TEMPLATE.format(
            form_id=args.form_id,
            form_md=chunk_md,
            pattern_id=args.pattern_id,
            complexity=pattern["complexity"],
            narrative=pattern["narrative"],
            prior_answers_block=build_prior_answers_block(answers),
        )
        try:
            raw = call_qwen(args.url, args.model, prompt,
                            max_tokens=args.max_tokens,
                            temperature=args.temperature,
                            field_count=args.chunk_size)
        except Exception as e:
            print(f"  chunk {ci}/{len(chunks)} call failed: {e}",
                  file=sys.stderr)
            continue
        raw = strip_fences(raw)
        rows: list[dict] = []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                rows = parsed
            elif isinstance(parsed, dict) and isinstance(
                    parsed.get("answers"), list):
                rows = parsed["answers"]
        except json.JSONDecodeError:
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("//"):
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
            answers[fid] = {
                "value": obj.get("value", ""),
                "confidence": float(obj.get("confidence", 0.0)),
                "reasoning": obj.get("reasoning", ""),
            }
            chunk_got += 1
        print(f"  chunk {ci}/{len(chunks)}: {chunk_got} fields")
    out_obj = {
        "pattern_id": args.pattern_id,
        "form_id": args.form_id,
        "answers": answers,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out_obj, indent=2, ensure_ascii=False))
    if not answers:
        # Save raw for inspection
        raw_path = args.out.with_suffix(".raw.txt")
        raw_path.write_text(raw)
        print(f"NO ANSWERS PARSED — raw saved to {raw_path}",
              file=sys.stderr)
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
