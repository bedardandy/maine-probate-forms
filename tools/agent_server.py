#!/usr/bin/env python3
"""MCP server for agent-driving the Maine probate forms library.

Built on the shared ``maine-forms-engine`` MCP scaffold
(``maine_forms_engine.mcp``): this module supplies the repo backend (keyword
routing, field-bucket payloads, the geometry fill-plan path); the scaffold
supplies the standardized tool surface (``query`` / ``case`` / ``out_dir``)
and the one error shape (failures are always ``{"ok": False, "error": ...,
"error_type": ...}``).

Tools (stdio, FastMCP):
  find_forms(query)               -> candidate forms (keyword routing over
                                     metadata; this repo's bucket shape)
  get_form(form_id)               -> title, source_url, parties, field
                                     buckets, skill
  fill_form(form_id, case, out_dir) -> a fill plan: deterministically resolved
                                     fields, the narrative worklist the agent
                                     composes, recompute + blank-by-design —
                                     and a written PDF when the form has
                                     fill_geometry.json (official flat source
                                     fetched + manifest-verified automatically)
  fill_form_from_source(form_id, case, source_pdf, out_dir)
                                  -> same, filling a flat source copy you
                                     already have                [extra tool]
  inspect_citations(form_id, field_texts) -> per-citation hallucination check
                                     over composed narrative text ([[REF: cite]]
                                     placeholders -> verified authority -> a
                                     cold-eyes inspector LLM). OPT-IN, LLM-backed,
                                     never on the fill path           [extra tool]

``case`` may be a court-style canonical fact object ({matter, parties, party,
facts}) — it's adapted to probate's case object automatically — or an already
native case object ({case_dict, <role>_record, narrative_facts}).

Register:  claude mcp add maine-probate-forms -- python3 tools/agent_server.py

This is the routing + planning layer; the root mcp_server.py is a different
(enrichment-pipeline) server. Probate PDFs are flat and not shipped. Not
legal advice.
"""
from __future__ import annotations

import json
import pathlib
import sys

from maine_forms_engine.mcp import UnknownFormError
from maine_forms_engine.mcp.server import main as _scaffold_main

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from canonical_adapter import to_case_object        # noqa: E402
from fetch import fetch_source                       # noqa: E402
from fill_plan import build_plan                     # noqa: E402
from fill_pdf import fill_pdf as _fill_pdf           # noqa: E402
from verify_filled import verify_filled as _verify_filled   # noqa: E402
import find_forms as _find                           # noqa: E402

_SOURCE_BUCKET = {
    "llm_over_narrative": "narrative", "recompute_from_dependencies": "recompute",
    "wet_ink": "blank", "human_decision": "blank", "left_blank": "blank",
    "triage": "blank",
}


def _form_dir(form_id: str) -> pathlib.Path:
    return ROOT / "repo" / "forms" / form_id


def get_form_payload(form_id: str) -> dict:
    """Metadata, parties, source link, field buckets, and skill guide."""
    fd = _form_dir(form_id)
    if not (fd / "schema.json").exists():
        raise UnknownFormError(form_id)
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

    # The full per-form guide (max ~8.4KB across all 79 forms) — truncating it
    # mid-field costs more agent turns than the tokens it saves.
    skill = ""
    if (fd / "skill.md").exists():
        skill = (fd / "skill.md").read_text()
    return {
        "form_id": form_id,
        "title": meta.get("title"), "category": meta.get("category"),
        "source_url": meta.get("source_url"), "n_fields": meta.get("n_fields"),
        "field_buckets": buckets,
        "skill": skill,
        "fill_with": "fill_form(form_id, case, out_dir) — case as a canonical "
                     "fact object {matter, parties, party, facts}.",
    }


def fill_form_payload(form_id: str, facts: dict, source_pdf: str = "",
                      out_dir: str = "/tmp") -> dict:
    """Resolve a fact pattern into a fill plan, and write a filled PDF if able.

    Returns the plan: resolved {field_id: value}, the `narrative` worklist for
    the agent to compose from the fact pattern, `recompute` (derived), `blank`
    (signatures / human decisions), and `unresolved` (missing facts).

    If the form has `fill_geometry.json`, the resolved values and checked
    options are written onto the flat source and the output PDF path is
    returned under `pdf`. When `source_pdf` is omitted the official flat PDF is
    fetched from `metadata.json.source_url` automatically (cached and verified
    against catalog/pdf_manifest.json). `pdf.source_verified` reports whether
    the source matched the manifest revision the geometry was measured
    against, and `pdf.verified_fill` summarizes a read-back of the written
    widget values. Compose `narrative` fields and pass them back in
    `facts.facts[field_id]` to fold them into the written text.
    """
    case = to_case_object(facts)
    plan = build_plan(form_id, case)
    if not plan.get("ok"):
        return plan
    if not (_form_dir(form_id) / "fill_geometry.json").exists():
        if source_pdf:
            plan["pdf_error"] = (f"{form_id} has no fill_geometry.json "
                                 "(plan-only form)")
        return plan
    if not source_pdf:
        try:
            source_pdf = str(fetch_source(form_id))
        except Exception as e:
            plan["pdf_error"] = f"could not fetch the flat source PDF: {e}"
            return plan
    out = str(pathlib.Path(out_dir) / f"{form_id}.filled.pdf")
    res = _fill_pdf(form_id, case, source_pdf, out)
    if res.get("ok"):
        plan["pdf"] = {"out": res["out"], "text_written": res["text_written"],
                       "options_checked": res["options_checked"],
                       "source_verified": res.get("source_verified"),
                       "source_verify_detail": res.get("source_verify_detail")}
        try:                       # cheap read-back of what actually landed
            chk = _verify_filled(form_id, case, res["out"])
            plan["pdf"]["verified_fill"] = chk.get("summary")
            if not chk.get("all_placed"):
                plan["pdf"]["verify_failures"] = {
                    fid: e for fid, e in chk.get("fields", {}).items()
                    if not e.get("placed")}
        except Exception as e:
            plan["pdf"]["verified_fill"] = f"verify_filled failed: {e}"
    else:
        plan["pdf_error"] = res.get("error")
    return plan


class Backend:
    """maine_forms_engine.mcp.FormsBackend for this repo."""

    name = "maine-probate-forms"

    def find_forms(self, query: str, top_k: int = 5) -> dict:
        """Route a fact pattern to candidate probate forms (keyword over
        metadata); returns this repo's bucket shape."""
        return _find.find_forms(query)

    def get_form(self, form_id: str) -> dict:
        """Metadata, parties, source link, field buckets, and skill guide."""
        return get_form_payload(form_id)

    def fill_form(self, form_id: str, case: dict, out_dir: str) -> dict:
        """Resolve a fact pattern into a fill plan (and a written PDF when the
        form has fill_geometry.json); see fill_form_payload."""
        return fill_form_payload(form_id, case, out_dir=out_dir)


def fill_form_from_source(form_id: str, case: dict, source_pdf: str,
                          out_dir: str = "/tmp") -> dict:
    """fill_form against a flat source PDF you already have on disk (skips the
    official fetch; the copy is still verified against the manifest)."""
    return fill_form_payload(form_id, case, source_pdf=source_pdf,
                             out_dir=out_dir)


def inspect_citations(form_id: str, field_texts, fetch_text: bool = True) -> dict:
    """Inspect LLM-composed narrative-field text for citation hallucinations.

    OPT-IN and LLM-backed — needs an OpenAI-compatible endpoint configured
    (INSPECTOR_BASE_URL/_MODEL/_API_KEY, falling back to ROUTER_*); it is NEVER
    part of the deterministic fill path. ``field_texts`` is the composed text for
    ``llm_over_narrative`` fields — a single string, or a {field_id: text} object.

    Each ``[[REF: cite]]`` placeholder is substituted with the cited authority
    (statutes fetched live + manifest-verified from legislature.maine.gov; cases
    from caselaw.json) and a cold-eyes inspector LLM scores, per citation, whether
    the draft's conclusion is supported (pass/fail/unclear with a grounded quote).
    A cite not in this form's vocabulary is flagged ``invented``; one whose text
    can't be fetched is ``unresolved`` — both deterministically, no model needed.
    Experimental — not legal advice.
    """
    try:
        import maine_citation_db as mdb        # lazy: keeps server import cheap
    except Exception as e:
        return {"ok": False, "error": f"inspector unavailable: {e}",
                "error_type": type(e).__name__}
    try:
        if isinstance(field_texts, dict):
            fields = {fid: mdb.inspect_field(form_id, txt, fetch_text=fetch_text)
                      for fid, txt in field_texts.items()}
            return {"ok": all(f.get("ok") for f in fields.values()),
                    "form_id": form_id, "fields": fields,
                    "disclaimer": mdb.DISCLAIMER}
        return mdb.inspect_field(form_id, str(field_texts), fetch_text=fetch_text)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "error_type": type(e).__name__}


EXTRA_TOOLS = (fill_form_from_source, inspect_citations)


def main():
    return _scaffold_main(Backend(), extra_tools=EXTRA_TOOLS)


if __name__ == "__main__":
    raise SystemExit(main())
