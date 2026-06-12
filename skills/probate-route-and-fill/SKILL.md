---
name: probate-route-and-fill
description: Go from a plain-language fact pattern to a filled Maine probate court form. Use when the user describes a probate situation (someone died, a guardianship/conservatorship, a name change, an adoption, an estate filing) and wants the right form selected and filled. Selects the form, fetches the flat source PDF, builds the fill plan, and writes a filled PDF — all via the deterministic tools/ path (no VLM at fill time).
---

# Route and fill a Maine probate form

This repository fills Maine probate court forms. The fill path is **deterministic
and VLM-free** — follow it directly; do not explore the heavy detection pipeline.

## Use these, ignore those

- **USE** the `tools/` directory: `find_forms.py`, `fill_plan.py`, `fill_pdf.py`,
  `verify_filled.py`, `canonical_adapter.py`, and the catalogs under `catalog/`
  and `repo/forms/<id>/`. To drive this library from an agent over MCP, use
  `tools/agent_server.py` (the forms-library MCP server: find/get/fill).
- **IGNORE** for filling: `modules/`, `pipeline.py`, `download.py`,
  `field_catalog.csv` (these regenerate form geometry), and the root
  `mcp_server.py` — that is a *separate* PDF field-rect alignment tool, NOT
  the forms-library server (`tools/agent_server.py` is).

## Workflow

1. **Route** the situation to a form id. You are the router — no external LLM
   endpoint is needed:
   - **Primary:** read `cat_surgical` from `catalog/router_catalog.json`
     (~1.5k tokens — every form id | category | title + disambiguation notes)
     and pick the best id directly.
   - **Shortcut:** `python3 tools/find_forms.py "<situation>"` for a keyword
     shortlist (an exact form id in the query, e.g. "DE-101", wins outright; bare estate ids are the FORMAL petitions, informal applications carry an "(I)" suffix, e.g. "DE-101(I)").
   - **Only if** an OpenAI-compatible router endpoint is configured
     (`ROUTER_BASE_URL`/`ROUTER_MODEL`):
     `python3 tools/route_form.py --json "<situation>"`.

   If the top candidates are a known-confusable pair (e.g. CN-1 vs AF-103,
   DE-405 vs PP-406 — see `disambiguated` in the catalog), confirm with the
   user. Read `repo/forms/<id>/skill.md` for the chosen form — it is the only
   per-form file you need to read (metadata.json has the source_url).

2. **Build the case object** from the fact pattern. Either a court-style canonical
   object (`{matter, parties, party, facts}`) or a native case object
   (`{case_dict, <role>_record, narrative_facts}`); `canonical_adapter` converts
   the former. Save it as `case.json`.

3. **Plan** (deterministic — no LLM, no network):
   ```bash
   python3 tools/fill_plan.py --form <FORM_ID> --case case.json
   ```
   Buckets: `resolved` (filled), `narrative` (compose from the facts, place under
   `narrative_facts[field_id]`, re-run), `unresolved` (missing facts to collect),
   `recompute`, `blank` (signatures/elections), `skipped` (gated off by a `when`
   condition). Compose the narrative fields, then re-plan until `narrative` and
   `unresolved` are acceptable. Use `--full` to see the per-field detail instead
   of reading `schema.json`.

4. **Fill** the PDF — the flat source is fetched automatically from
   `metadata.json.source_url` (cached, SHA-256-verified against
   `catalog/pdf_manifest.json`):
   ```bash
   python3 tools/fill_pdf.py --form <FORM_ID> --case case.json \
     --out /tmp/<FORM_ID>.filled.pdf
   ```
   Check `source_verified` in the result — `false` means the court re-issued the
   form since the fill geometry was measured.

5. **Verify** the output — reopen the filled PDF and diff the widget values
   against the plan:
   ```bash
   python3 tools/verify_filled.py --form <FORM_ID> --case case.json \
     --filled /tmp/<FORM_ID>.filled.pdf
   ```
   Report any field that did not place, alongside the narrative fields you
   composed and the unresolved facts.

## Notes

- Probate PDFs are flat and **not shipped** — `fill_pdf.py` fetches them on
  demand; pass `--source <pdf>` only to fill a copy you already have.
- The plan/fill layer is a pure function of the case object: same case in → same
  output. Good for golden-testing or mapping `resolved` to template tokens.
- Not legal advice — always verify the filled form against the official version.
