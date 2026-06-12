#!/usr/bin/env python3
"""Tier 2 of the geometry review: Codex adjudication of disputed units.

Local voters disagreed (or a lone major). Each disputed unit goes to the
Codex CLI (gpt-5.5) with the red-boxed crop attached plus all deterministic
evidence and the local votes. The adjudicator answers the same micro-schema
and its verdict is final for the queue (the deterministic gates already
carried safety; see feedback_codex_gpt55_adjudicator).

    python3 scripts/geometry_review/adjudicate_codex.py --out ~/geom-review-out
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

PROMPT = """\
You are adjudicating one disputed finding from a PDF form-fill geometry audit.
The attached image shows a filled Maine probate form region; the RED BOX is
where the software places the typed value (token {token} or an X mark).

Deterministic evidence: {evidence}
Local model votes: {votes}

Decide from the IMAGE. Is the typed value misplaced — overlapping printed
text, off its line vertically, or in the wrong horizontal position? Touching
an underscore/blank line is correct placement, not a defect.

Reply with ONLY this JSON:
{{"verdict": "ok|minor|major", "axis": "vertical|horizontal|overlap|none",
  "note": "<one sentence>"}}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    adj_p = args.out / "adjudications.jsonl"
    seen = set()
    if adj_p.exists():
        for line in adj_p.open():
            seen.add(json.loads(line)["key"])
    out_f = adj_p.open("a")

    disputed = [json.loads(l) for l in (args.out / "consensus.jsonl").open()
                if json.loads(l)["status"] == "disputed"]
    if args.limit:
        disputed = disputed[: args.limit]
    print(f"{len(disputed)} disputed units")

    for u in disputed:
        if u["key"] in seen or not u.get("crop"):
            continue
        prompt = PROMPT.format(token=u.get("token") or "X",
                               evidence=json.dumps(u["evidence"]),
                               votes=json.dumps(u["votes"]))
        try:
            # -i is variadic: the prompt must arrive via stdin ("-"), not as
            # a positional (it would be parsed as another image path).
            r = subprocess.run(
                ["codex", "exec", "--skip-git-repo-check", "-i", u["crop"],
                 "-"],
                input=prompt, capture_output=True, text=True, timeout=300)
            m = re.findall(r"\{[^{}]*\"verdict\"[^{}]*\}", r.stdout)
            verdict = json.loads(m[-1]) if m else {"error": r.stdout[-200:]}
        except Exception as e:
            verdict = {"error": str(e)[:150]}
        if "usage limit" in (r.stdout + r.stderr if 'r' in dir() else ""):
            print("usage limit hit — stopping; re-run after reset")
            break
        out_f.write(json.dumps({"key": u["key"], "form": u["form"],
                                "field": u["field"], **verdict}) + "\n")
        out_f.flush()
        print(u["key"], "->", verdict.get("verdict", verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
