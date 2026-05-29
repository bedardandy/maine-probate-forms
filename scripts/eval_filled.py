#!/usr/bin/env python3
"""Opus-evaluates a filled form against its source fact pattern. For each
field, scores (a) factual accuracy vs the narrative, (b) whether the
model's confidence is well-calibrated to the evidence actually in the
narrative, and (c) whether the value satisfies what the form is asking
for (semantic comprehension).

Output YAML:

    pattern_id: 1
    form_id: PP-507
    summary:
      accuracy: X/N
      well_calibrated: X/N
      overconfident: X
      underconfident: X
      comprehension_issues: X
    per_field:
      <field_id>:
        accuracy: matches | partial | wrong | not_applicable
        calibration: well | overconfident | underconfident | absent_ok
        comprehension: ok | misunderstood | unclear
        note: <opus comment>

Calls headless `claude -p --model opus` with the env-stripped OAuth fallback.
"""
from __future__ import annotations
import argparse
import os
import pathlib
import re
import subprocess
import sys
import yaml


def repair_yaml_notes(text: str) -> str:
    """Best-effort repair of YAML emitted by Opus that fails to parse.

    Common failure: a `note:` value uses single quotes around an inner
    phrase, e.g.  `note: 'Other' not selected.` — YAML reads 'Other' as
    a complete single-quoted scalar then chokes on " not selected.".

    Strategy: for each line matching `<indent>note: <unquoted-content>`
    or `note: '<phrase>' <rest>`, rewrite the value as a double-quoted
    string with embedded quotes escaped. We deliberately only touch
    note: / overall_note: lines so we don't perturb structural keys.
    """
    out_lines: list[str] = []
    note_re = re.compile(r"^(\s*(?:note|overall_note):\s*)(.*)$")
    for line in text.splitlines():
        m = note_re.match(line)
        if not m:
            out_lines.append(line)
            continue
        prefix, value = m.group(1), m.group(2)
        v = value.rstrip()
        # Already a clean double-quoted scalar? leave alone.
        if v.startswith('"') and v.endswith('"') and v.count('"') == 2:
            out_lines.append(line)
            continue
        # Block scalar (| or >)? leave alone.
        if v.startswith(("|", ">")):
            out_lines.append(line)
            continue
        # Empty after the colon? leave alone (parses as null).
        if not v:
            out_lines.append(line)
            continue
        # Otherwise wrap in double quotes, escape embedded doubles.
        v = v.replace("\\", "\\\\").replace('"', '\\"')
        out_lines.append(f'{prefix}"{v}"')
    return "\n".join(out_lines)

PROMPT_TEMPLATE = """You are a careful legal-forms auditor reviewing a
Maine probate court form that was filled out by a junior associate
based on a client intake narrative.

You will see (1) the narrative, (2) the field-by-field filled form with
the associate's confidence score and reasoning. Evaluate each field
along three axes:

1. **accuracy**: does the value match the narrative?
   - `matches` — the value is correct and present in the narrative
   - `partial` — partly correct (e.g. first name right, last name guessed)
   - `wrong` — the value contradicts the narrative
   - `not_applicable` — value is empty and narrative is silent (correct
     behavior — not a bug)

2. **calibration**: is the confidence score honest?
   - `well` — confidence matches how strongly the narrative supports it
   - `overconfident` — confidence is high (>=0.6) but evidence is weak,
     OR the value is wrong but confidence is high
   - `underconfident` — confidence is low (<0.6) but evidence is strong
     and value is correct
   - `absent_ok` — value is empty with low confidence; correctly conservative

3. **comprehension**: does the answer satisfy what the form is asking?
   - `ok` — value is the right kind of thing for the field
   - `misunderstood` — value is for the wrong concept (e.g. notice
     `Date/Time` filled with just a date when both are needed)
   - `unclear` — can't tell without seeing the actual form

Output VALID YAML ONLY. No prose, no markdown fences.

CRITICAL YAML HYGIENE — read carefully:
- ALL `note:` and `overall_note:` values MUST be wrapped in DOUBLE QUOTES
  ("..."). Never use single quotes — they break YAML parsing when the
  note itself contains an apostrophe or a single-quoted phrase like
  'Other' or "the petitioner's name".
- Inside a double-quoted note, you may use single quotes freely. Embedded
  double quotes must be doubled or escaped: \" or "" .
- Notes should be ONE LINE. If you need multiple sentences, use ". "
  separators inside the single double-quoted string.
- Field IDs that contain hyphens, apostrophes, or other YAML-special
  chars must be wrapped in double quotes as keys too.

Schema:
pattern_id: {pattern_id}
form_id: "{form_id}"
summary:
  total_fields: <int>
  matches: <int>
  partial: <int>
  wrong: <int>
  not_applicable: <int>
  well_calibrated: <int>
  overconfident: <int>
  underconfident: <int>
  comprehension_issues: <int>
  overall_note: "<one-sentence opus assessment, DOUBLE-QUOTED>"
per_field:
  <field_id>:
    accuracy: matches | partial | wrong | not_applicable
    calibration: well | overconfident | underconfident | absent_ok
    comprehension: ok | misunderstood | unclear
    note: "<opus comment, DOUBLE-QUOTED, may be empty string ''>"

=== NARRATIVE (pattern {pattern_id}, complexity {complexity}) ===
{narrative}

=== FILLED FORM ===
{filled_md}

Output the YAML now. Score EVERY field that appears in the filled form."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("patterns_yaml", type=pathlib.Path)
    ap.add_argument("filled_md", type=pathlib.Path)
    ap.add_argument("--pattern-id", type=int, required=True)
    ap.add_argument("--form-id", required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--save-raw", type=pathlib.Path)
    args = ap.parse_args()
    if not args.patterns_yaml.exists() or not args.filled_md.exists():
        print("missing inputs", file=sys.stderr)
        return 2
    patterns = yaml.safe_load(args.patterns_yaml.read_text()).get(
        "patterns", []
    )
    pattern = next((p for p in patterns if p["id"] == args.pattern_id), None)
    if pattern is None:
        print(f"pattern {args.pattern_id} not found", file=sys.stderr)
        return 2

    prompt = PROMPT_TEMPLATE.format(
        pattern_id=args.pattern_id,
        form_id=args.form_id,
        complexity=pattern["complexity"],
        narrative=pattern["narrative"],
        filled_md=args.filled_md.read_text(),
    )
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    print(f"[eval_filled] form={args.form_id} pattern={args.pattern_id} "
          f"prompt={len(prompt)} bytes")
    proc = subprocess.run(
        ["claude", "-p", "--model", args.model],
        input=prompt, capture_output=True, text=True, env=env,
        timeout=1200,
    )
    if proc.returncode != 0:
        print(f"claude -p failed (rc={proc.returncode})", file=sys.stderr)
        print(proc.stderr[:500], file=sys.stderr)
        return 3
    out = proc.stdout
    if args.save_raw:
        args.save_raw.parent.mkdir(parents=True, exist_ok=True)
        args.save_raw.write_text(out)
    # Strip code fences if any
    if "```" in out:
        parts = out.split("```")
        candidates = [p for p in parts if "pattern_id" in p
                      and "per_field" in p]
        if candidates:
            out = max(candidates, key=len)
            if out.lstrip().startswith("yaml"):
                out = out.split("\n", 1)[1] if "\n" in out else ""
    # Try parsing; if it fails, repair note/overall_note lines and retry.
    parsed = None
    try:
        parsed = yaml.safe_load(out)
    except Exception as e:
        repaired = repair_yaml_notes(out)
        try:
            parsed = yaml.safe_load(repaired)
            out = repaired
            print(f"  YAML repaired (was: {e})", file=sys.stderr)
        except Exception as e2:
            print(f"YAML parse failed even after repair: {e2}",
                  file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(out)
    if isinstance(parsed, dict):
        s = parsed.get("summary", {}) or {}
        print(f"wrote {args.out}")
        print(f"  total={s.get('total_fields')} matches={s.get('matches')} "
              f"wrong={s.get('wrong')} overconfident={s.get('overconfident')} "
              f"underconfident={s.get('underconfident')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
