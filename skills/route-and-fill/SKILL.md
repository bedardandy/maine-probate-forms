---
name: route-and-fill
description: Go from a plain-language fact pattern to a filled Maine probate court form. Use when the user describes a probate situation (someone died, a guardianship/conservatorship, a name change, an adoption, an estate filing) and wants the right form selected and filled. Selects the form, fetches the flat source PDF, builds the fill plan, and writes a filled PDF — all via the deterministic tools/ path (no VLM at fill time).
---

# Route and fill a Maine probate form

This repository fills Maine probate court forms. The fill path is **deterministic
and VLM-free** — follow it directly; do not explore the heavy detection pipeline.

## Use these, ignore those

- **USE** the `tools/` directory: `route_form.py`, `fill_plan.py`, `fill_pdf.py`,
  `canonical_adapter.py`, and the catalogs under `catalog/` and `repo/forms/<id>/`.
  To drive this library from an agent over MCP, use `tools/agent_server.py` (the
  forms-library MCP server: find/get/fill).
- **IGNORE** for filling: `modules/`, `pipeline.py`, `download.py`,
  `field_catalog.csv`, `output_*/` (these regenerate form geometry), and the
  root `mcp_server.py` — that is a *separate* PDF field-rect alignment tool, NOT
  the forms-library server (`tools/agent_server.py` is).

## Workflow

1. **Route** the situation to a form id:
   ```bash
   python3 tools/route_form.py --json "my mother died without a will, I'm her daughter"
   ```
   Returns `{form_id, alternates, confidence}`. `form_id` is `"NONE"` if no form
   applies. If confidence is low or the top-2 are a known-confusable pair
   (e.g. CN-1 vs AF-103, DE-405 vs PP-406), confirm with the user.

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
   `unresolved` are acceptable.

4. **Fill** the PDF (fetches the flat source from `metadata.json.source_url`):
   ```bash
   python3 tools/fill_pdf.py --form <FORM_ID> --case case.json \
     --source "<fetched source>.pdf" --out /tmp/<FORM_ID>.filled.pdf
   ```

## Notes

- Probate PDFs are flat and **not shipped** — fetch each form's
  `repo/forms/<id>/metadata.json` → `source_url`.
- The plan/fill layer is a pure function of the case object: same case in → same
  output. Good for golden-testing or mapping `resolved` to template tokens.
- Not legal advice — always verify the filled form against the official version.
