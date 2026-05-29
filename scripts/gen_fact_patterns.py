#!/usr/bin/env python3
"""Generate plausible fact-pattern scenarios for a probate form via headless
Claude Code (Opus). Five patterns by default, ranging from complete to
deliberately sparse — the sparse ones test whether the filler model marks
unspecified fields with low confidence rather than confabulating.

Output is YAML so downstream steps stay parseable:

    patterns:
      - id: 1
        title: <short title>
        complexity: complete | partial | edge_case
        narrative: |
          <free-form scenario description as a real person would describe it>
"""
from __future__ import annotations
import argparse
import os
import pathlib
import subprocess
import sys

PROMPT_TEMPLATE = """You are generating realistic fact patterns that a real
litigant or attorney would face when filling out the Maine probate court
form below. Generate exactly {n} distinct scenarios.

Coverage requirements:
- 1 COMPLETE scenario: every field of the form should be answerable from
  the narrative
- 2 PARTIAL scenarios: the narrative omits 20-40% of the form's fields,
  varying which ones are missing
- 1 EDGE_CASE: an unusual but legitimate situation (e.g. multiple
  petitioners, contested guardianship, decedent without a will, etc.)
- 1 SPARSE scenario: only the bare minimum is specified — most fields
  must be left unanswered or guessed at low confidence

Write each narrative as a continuous paragraph in the voice of a real person
describing what happened — NOT a structured list of field values. Use
concrete names, dates, addresses, dollar amounts. Vary names across
scenarios to avoid the filler model latching onto patterns.

The narratives should feel like real client intake notes — facts a paralegal
would jot down talking to someone over the phone.

Output VALID YAML ONLY. No prose, no markdown fences, no explanations.

Schema:
patterns:
  - id: 1
    title: <one-line label>
    complexity: complete | partial | edge_case | sparse
    narrative: |
      <multi-line narrative>
  - id: 2
    ...

Form schema (markdown):
---
{form_md}
---

Output the YAML now.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("form_md", type=pathlib.Path,
                    help="markdown form schema (from form_to_markdown.py)")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--model", default="opus")
    args = ap.parse_args()
    if not args.form_md.exists():
        print(f"missing: {args.form_md}", file=sys.stderr)
        return 2

    form_md = args.form_md.read_text()
    prompt = PROMPT_TEMPLATE.format(n=args.n, form_md=form_md)

    # Strip ANTHROPIC_API_KEY so headless CC falls back to keychain OAuth.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    print(f"[gen_fact_patterns] form={args.form_md.name} model={args.model} "
          f"n={args.n} prompt={len(prompt)} bytes")
    proc = subprocess.run(
        ["claude", "-p", "--model", args.model],
        input=prompt, capture_output=True, text=True, env=env,
        timeout=600,
    )
    if proc.returncode != 0:
        print(f"claude -p failed (rc={proc.returncode})", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return 3
    out = proc.stdout
    # Strip code fences if Claude added them despite instructions.
    if "```" in out:
        parts = out.split("```")
        # Take the longest fenced section that looks like YAML
        candidates = [p for p in parts if "patterns:" in p]
        if candidates:
            out = max(candidates, key=len)
            # Drop the language tag line if present
            if out.lstrip().startswith("yaml"):
                out = out.split("\n", 1)[1] if "\n" in out else ""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(out)
    print(f"wrote {args.out} ({len(out)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
