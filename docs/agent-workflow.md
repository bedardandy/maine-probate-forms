# Agent workflow: fill a probate form from a fact pattern

How an agent (Claude Code / codex) drives this repo from a plain-language
situation. The source PDFs are **flat** (no AcroForm fields), but every form
ships a **`field_id → physical-widget` binding** (`fill_geometry.json`), so a
*filled document* injects directly onto the fetched flat source, with no pipeline
at fill time. On top of that, a rich semantic schema where **every field carries
a `fill_strategy.source`** lets a canonical fact object resolve into a
**fill plan**.

> **Not legal advice.** Output is a draft to verify against the official form.

## Steps

**1. Route: which form?** An agent IS the router — read `cat_surgical` from
`catalog/router_catalog.json` (~1.5k tokens: id | category | title +
disambiguation notes) and pick directly. Or shortlist by keyword:
```bash
python3 tools/find_forms.py "informal probate of a will"
```
(an exact form id in the query, e.g. "DE-101", wins outright). Use
`tools/route_form.py` only when an OpenAI-compatible router endpoint is
configured (`ROUTER_BASE_URL`/`ROUTER_MODEL`).

**2. Read the form package.** `repo/forms/<ID>/`:
- `skill.md` — filer role, statutes, parties, slot groups. **For agents:
  skill.md is the only per-form file you need to read.**
- `metadata.json` — title, category, **`source_url`** (official flat PDF), n_fields

Don't read `schema.json` (it is large); when you need per-field detail, run
`python3 tools/fill_plan.py --form <ID> --case case.json --full` — it returns
every field's bucket, label, prompt, and data type.

**3. Build a canonical fact object + plan the fill.**
Translate the fact pattern into the canonical shape (probate-native party roles):
```jsonc
{ "matter":  { "court_county", "docket_number", "filing_date" },
  "parties": { "applicant": {full_name, address, city, state, zip, phone, email},
               "decedent":  {full_name, date_of_birth, date_of_death, domicile},
               "attorney":  {name, address, phone, bar_number, email} },
  "party":   { "full_name", "address", ... },        // the filing party
  "facts":   { /* narrative facts, keyed freely */ } }
```
Then:
```bash
python3 tools/fill_plan.py --form <ID> --case case.json
```
`tools/canonical_adapter.py` converts this to probate's case object
(`case_dict` + `<role>_record` + `narrative_facts`); `tools/fill_plan.py`
resolves each field's `fill_strategy.source` and buckets it:

| bucket | meaning | who fills |
|---|---|---|
| `resolved` | `case_dict.*` / `*_record.*` looked up from the case object | done |
| `narrative` | `llm_over_narrative` fields | **the agent**, from the fact pattern |
| `recompute` | `recompute_from_dependencies` / formula | derived |
| `blank` | `wet_ink` / `human_decision` / `left_blank` / `triage` | signature / human |
| `unresolved` | a `case_dict.*` / `*_record.*` source with no value | collect as missing |
| `skipped` | a field whose `when` condition is false for this case (e.g. an "Other:" write-in when the choice isn't "other") | nobody — not applicable |

A field is gated off only when its controlling field is *known* and the
condition is definitively false; an unknown controller leaves the field in its
normal bucket.

**4. Compose the narrative fields.** For each item in `narrative`, write a value
from the fact pattern. Put them back under `narrative_facts[field_id]` and re-run
`fill_plan.py`; they fold into `resolved`. Leave genuine unknowns out and report
them.

**5. Write the filled PDF.**
```bash
python3 tools/fill_pdf.py --form <ID> --case case.json --out filled.pdf
```
The flat source is fetched automatically from `metadata.json.source_url`
(cached and SHA-256-verified against `catalog/pdf_manifest.json`); pass
`--source <pdf>` to fill a copy you already have. The result includes
`source_verified` — `false` means the court re-issued the form since the fill
geometry was measured. Each form ships `repo/forms/<ID>/fill_geometry.json`
(`field_id → widget rects` derived from the form's aligned layout), so the
resolved text and checked options inject **directly onto the flat source**, no
pipeline needed at fill time. (All 79 forms ship geometry.) Geometry is
*derived*: when a form changes or an alignment is fixed, regenerate it (never
hand-edit). See **`docs/maintenance.md`**.

**6. Verify & report.** Re-open the output and diff what actually landed
against the plan:
```bash
python3 tools/verify_filled.py --form <ID> --case case.json --filled filled.pdf
```
Per-field `{placed, expected, actual, page}` plus a summary; exits non-zero if
any resolved field failed to place. Then flag the form's risk tiers, the
narrative fields you composed, and any unresolved/missing facts. Must be
reviewed before filing.

## MCP (recommended for agents)
```bash
claude mcp add maine-probate-forms -- python3 tools/agent_server.py
```
Tools: `find_forms(query)`, `get_form(form_id)`,
`fill_form(form_id, facts, source_pdf="")`. `fill_form` returns the plan and
writes the filled PDF (path under `pdf`), auto-fetching the flat source when
`source_pdf` is omitted; the result carries `source_verified` and a
`verified_fill` read-back summary. This mirrors the companion
`maine-court-forms` MCP layer.
