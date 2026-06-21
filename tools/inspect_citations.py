#!/usr/bin/env python3
"""Inspect an LLM-composed legal draft for citation hallucinations (Maine probate).

A closed-vocabulary placeholder inspector for the ``llm_over_narrative`` narrative
fields the agent composes — the one drafting surface ``verify_filled.py`` leaves
unchecked. Two modes:

  --emit-prompt --form DE-101
      Print the draft-generator system prompt + the form's ALLOWED [[REF: KEY]]
      citations, so an LLM can compose narrative text that cites only verified
      authorities (statutes/cases from this form's statutes.json). Deterministic,
      no LLM, no network.

  --form DE-101 --draft draft.txt            (default: inspect)
      Substitute each [[REF: cite]] with the authority text (statutes fetched live
      from legislature.maine.gov; cases from caselaw.json), run the inspector LLM,
      and print a per-citation scorecard. Invented or unresolved citations are
      flagged deterministically — no model or network needed for those.

OPT-IN and non-deterministic — never part of the deterministic fill path. The
inspector LLM uses the same pluggable OpenAI-compatible endpoint as route_form
(INSPECTOR_BASE_URL / INSPECTOR_MODEL / INSPECTOR_API_KEY, falling back to the
ROUTER_* values). Exit status is non-zero when anything needs a human's eyes
(a fail/invented/unresolved citation, or the LLM call could not complete).
Not legal advice.

    python3 tools/inspect_citations.py --emit-prompt --form DE-101
    python3 tools/inspect_citations.py --form DE-101 --draft draft.txt --json
    echo "...[[REF: 18-C §3-401]]..." | python3 tools/inspect_citations.py --form DE-101
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import maine_citation_db as mdb            # noqa: E402


def _needs_review(res: dict) -> bool:
    s = res.get("summary", {})
    scan = res.get("scan", {})
    return (not res.get("ok")) or bool(res.get("invented")) or \
        bool(res.get("unresolved")) or s.get("fail", 0) > 0 or \
        bool(scan.get("leaked")) or bool(scan.get("unresolvable"))


def _print_scorecard(res: dict) -> None:
    s = res.get("summary", {})
    head = res.get("form_id", "")
    print(f"Citation inspection [{head}]  "
          f"pass={s.get('pass', 0)} fail={s.get('fail', 0)} "
          f"unclear={s.get('unclear', 0)} unresolved={s.get('unresolved', 0)} "
          f"invented={s.get('invented', 0)}")
    if res.get("invented"):
        print(f"  INVENTED (not in this form's vocabulary): {', '.join(res['invented'])}")
    if res.get("unresolved"):
        print(f"  UNRESOLVED (authority text unavailable): {', '.join(res['unresolved'])}")
    scan = res.get("scan", {})
    if scan.get("leaked"):
        print(f"  LEAKED (cited in prose, outside [[REF:]]): {', '.join(scan['leaked'])}")
    if scan.get("unresolvable"):
        print(f"  UNRESOLVABLE (bare cite not in index): {', '.join(scan['unresolvable'])}")
    if scan.get("out_of_vocab"):
        print(f"  OUT-OF-VOCAB (real cite, not for this form): {', '.join(scan['out_of_vocab'])}")
    for v in res.get("verdicts", []):
        mark = {"pass": "✓", "fail": "✗", "unclear": "?"}.get(v["supports_conclusion"], "?")
        gq = "" if v.get("quote_grounded", True) else "  [quote NOT found in authority]"
        print(f"  {mark} {v['supports_conclusion'].upper():7} {v.get('cite')}{gq}")
        if v.get("rationale"):
            print(f"        {v['rationale']}")
    if not res.get("ok"):
        print(f"  [warning] inspector LLM unavailable: {res.get('error')}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True, help="form id, e.g. DE-101")
    ap.add_argument("--draft", help="path to a draft containing [[REF: cite]] placeholders")
    ap.add_argument("--narrative", help="path to a JSON object {field_id: composed_text}")
    ap.add_argument("--emit-prompt", action="store_true",
                    help="print the draft-generator prompt + allowed cites, then exit")
    ap.add_argument("--no-fetch-text", action="store_true",
                    help="use section titles + relevance notes instead of live statute text")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.emit_prompt:
        print(mdb.draft_prompt(a.form))
        return 0

    fetch_text = not a.no_fetch_text
    if a.narrative:
        fields = json.loads(pathlib.Path(a.narrative).read_text(encoding="utf-8"))
        out = {fid: mdb.inspect_field(a.form, txt, fetch_text=fetch_text)
               for fid, txt in fields.items()}
        review = any(_needs_review(r) for r in out.values())
        if a.json:
            print(json.dumps({"ok": all(r.get("ok") for r in out.values()),
                              "form_id": a.form, "fields": out,
                              "disclaimer": mdb.DISCLAIMER}, indent=2, ensure_ascii=False))
        else:
            for fid, r in out.items():
                print(f"\n# field: {fid}")
                _print_scorecard(r)
            print(f"\n{mdb.DISCLAIMER}")
        return 1 if review else 0

    if a.draft:
        draft = pathlib.Path(a.draft).read_text(encoding="utf-8")
    else:
        draft = sys.stdin.read()
    if not draft.strip():
        ap.error("no draft text (pass --draft, --narrative, or pipe text on stdin)")

    res = mdb.inspect_field(a.form, draft, fetch_text=fetch_text)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        _print_scorecard(res)
        print(f"\n{mdb.DISCLAIMER}")
    return 1 if _needs_review(res) else 0


if __name__ == "__main__":
    raise SystemExit(main())
