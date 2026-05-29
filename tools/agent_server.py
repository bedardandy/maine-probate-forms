#!/usr/bin/env python3
"""MCP server for agent-driving the Maine probate forms library.

Exposes three tools so a Claude Code / codex agent can go from a plain-language
fact pattern to a structured fill plan:

  * find_forms(query)        -> candidate forms (keyword routing over metadata)
  * get_form(form_id)        -> title, source_url, parties, field buckets, skill
  * fill_form(form_id, facts)-> a fill plan: deterministically resolved fields,
                                the narrative worklist the agent composes from
                                the fact pattern, recompute + blank-by-design.

`facts` may be a court-style canonical fact object ({matter, parties, party,
facts}) — it's adapted to probate's case object automatically — or an already
native case object ({case_dict, <role>_record, narrative_facts}).

Register:  claude mcp add maine-probate-forms -- python3 tools/agent_server.py

This is the routing + planning layer. Probate PDFs are flat and not shipped;
applying a plan to a document requires generating the fillable form (see
docs/agent-workflow.md). Not legal advice.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from canonical_adapter import to_case_object        # noqa: E402
from fill_plan import build_plan                     # noqa: E402
from fill_pdf import fill_pdf as _fill_pdf           # noqa: E402
import find_forms as _find                           # noqa: E402

from mcp.server.fastmcp import FastMCP               # noqa: E402

mcp = FastMCP("maine-probate-forms")

_SOURCE_BUCKET = {
    "llm_over_narrative": "narrative", "recompute_from_dependencies": "recompute",
    "wet_ink": "blank", "human_decision": "blank", "left_blank": "blank",
    "triage": "blank",
}


def _form_dir(form_id: str) -> pathlib.Path:
    return ROOT / "repo" / "forms" / form_id


@mcp.tool()
def find_forms(query: str) -> dict:
    """Route a fact pattern to candidate probate forms (keyword over metadata)."""
    return _find.find_forms(query)


@mcp.tool()
def get_form(form_id: str) -> dict:
    """Metadata, parties, source link, field buckets, and skill guide for a form."""
    fd = _form_dir(form_id)
    if not (fd / "schema.json").exists():
        return {"ok": False, "error": f"unknown form {form_id!r}"}
    schema = json.loads((fd / "schema.json").read_text())
    meta = json.loads((fd / "metadata.json").read_text()) if (
        fd / "metadata.json").exists() else {}

    buckets: dict[str, int] = {}
    for f in schema.get("fields", []):
        src = (f.get("fill_strategy") or {}).get("source") or ""
        ns = src.split(".", 1)[0]
        if src.startswith("case_dict.") or ns.endswith("_record"):
            b = "resolvable"
        else:
            b = _SOURCE_BUCKET.get(src, "other")
        buckets[b] = buckets.get(b, 0) + 1

    skill = ""
    if (fd / "skill.md").exists():
        skill = (fd / "skill.md").read_text()[:1500]
    return {
        "ok": True, "form_id": form_id,
        "title": meta.get("title"), "category": meta.get("category"),
        "source_url": meta.get("source_url"), "n_fields": meta.get("n_fields"),
        "field_buckets": buckets,
        "skill_excerpt": skill,
        "fill_with": "fill_form(form_id, facts) — facts as a canonical fact "
                     "object {matter, parties, party, facts}.",
    }


@mcp.tool()
def fill_form(form_id: str, facts: dict, source_pdf: str = "",
              out_dir: str = "/tmp") -> dict:
    """Resolve a fact pattern into a fill plan, and write a filled PDF if able.

    Returns the plan: resolved {field_id: value}, the `narrative` worklist for
    the agent to compose from the fact pattern, `recompute` (derived), `blank`
    (signatures / human decisions), and `unresolved` (missing facts).

    If `source_pdf` is given (the flat form fetched from
    `get_form(...).source_url`) and the form has `fill_geometry.json`, the
    resolved values and checked options are written onto it and the output PDF
    path is returned under `pdf`. Compose `narrative` fields and pass them back
    in `facts.facts[field_id]` to fold them into the written text.
    """
    case = to_case_object(facts)
    plan = build_plan(form_id, case)
    if source_pdf and (_form_dir(form_id) / "fill_geometry.json").exists():
        out = str(pathlib.Path(out_dir) / f"{form_id}.filled.pdf")
        res = _fill_pdf(form_id, case, source_pdf, out)
        if res.get("ok"):
            plan["pdf"] = {"out": res["out"], "text_written": res["text_written"],
                           "options_checked": res["options_checked"]}
        else:
            plan["pdf_error"] = res.get("error")
    elif source_pdf:
        plan["pdf_error"] = (f"{form_id} has no fill_geometry.json "
                             "(plan-only form)")
    return plan


if __name__ == "__main__":
    mcp.run()
