# Maine Probate Forms — AcroForm Pipeline

## What This Project Does

Downloads Maine Probate Court PDF forms, detects fillable field regions using heuristics + VLM validation, and writes AcroForm widgets so the PDFs become truly fillable. Includes an MCP server for interactive field alignment via Claude Code.

## Project Structure

```
config.py              — All paths, thresholds, VLM settings
download.py            — Stage 1: scrape PDF forms from maineprobate.net
modules/
  pdf_analyzer.py      — Render pages, extract lines/text/rects
  field_detector.py    — Heuristic field detection (text inputs, checkboxes, signatures, etc.)
  vlm_validator.py     — VLM-based validation of detected fields
  schema.py            — Pydantic models for field definitions
  catalog.py           — Form catalog/inventory management
  taxonomy.py          — Field type taxonomy
  acroform_writer.py   — Write AcroForm widgets into PDFs
  form_filler.py       — Fill existing AcroForm PDFs with data
  preview.py           — Visual preview of detected fields
mcp_server.py          — MCP server for PDF field inspection/alignment (FastMCP, stdio)
forms/                 — Downloaded source PDFs (organized by category)
intermediate/          — Analysis JSON, detection results, validation output
output/                — Final fillable PDFs
```

## Key Commands

```bash
# Activate venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## MCP Server (`mcp_server.py`)

A FastMCP server registered with Claude Code for interactive PDF field operations. Runs over stdio.

### Tools

| Tool | Purpose |
|---|---|
| `list_fields(pdf_path)` | List all form fields with name, type, page, rect `[x0, y0, x1, y1]`, value |
| `get_page_dimensions(pdf_path)` | Get width/height of each page |
| `update_field_rects(pdf_path, updates, output_path?)` | Batch-update field rectangles. `updates`: `[{field_name, rect}]` |
| `align_fields(pdf_path, reference_field, target_fields, axis, output_path?)` | Align target fields to reference field's position. `axis`: `"x"`, `"y"`, or `"both"` |

### Registration

Already registered. Verify with:
```bash
claude mcp list   # should show pdf-forms ✓ Connected
```

To re-register if needed:
```bash
claude mcp add --transport stdio pdf-forms -- \
  /path/to/projects/probate-forms/.venv/bin/python3 \
  /path/to/projects/probate-forms/mcp_server.py
```

### Usage Examples

In Claude Code conversation:
- "List the fields in forms/estates/DE-104 PR Acceptance (Rev. 07-01-19).pdf"
- "Align all text fields on page 0 to the same Y as the COUNTY field"
- "Move field X to rect [54, 700, 200, 715]"

### Coordinate System

- Origin is top-left of the page (PDF user space, measured in points: 72pt = 1 inch)
- Standard letter page: 612 x 792 points
- Rect format: `[x0, y0, x1, y1]` where `(x0, y0)` is top-left, `(x1, y1)` is bottom-right
- Y increases downward
- `align_fields` with `axis="y"` copies the reference field's y0 and preserves each target's height

## Dependencies

- **PyMuPDF** (`fitz`) — PDF reading, widget manipulation, rendering
- **pdfplumber** — Supplementary PDF text/line extraction
- **Pillow** — Image handling for VLM page renders
- **openai** — VLM API client (OpenRouter)
- **pydantic** — Field schemas
- **mcp[cli]** — FastMCP server framework

## Notes

- Most source PDFs in `forms/` are flat (no AcroForm fields). The pipeline adds fields.
- Some forms already have fields (adoption, guardian_minor, name_change, affidavits, notices categories have fillable PDFs).
- `form_filler.py:list_form_fields()` and `mcp_server.py:list_fields()` do the same thing; the MCP version uses `name`/`value` keys while form_filler uses `field_name`/`current_value`.
- **VLM:** the validator targets a local, OpenAI-compatible VLM endpoint (default `http://localhost:8083/v1`); no API key is required. The default is a local vision-language model served behind a router that loads on demand and sleeps when idle. Any OpenAI-compatible server works as long as responses match the `FieldDecision` schema.
- **Validator semantics:** gating mode. Each heuristic candidate gets keep/reject + snake_case semantic name (e.g. `pr_full_legal_name`, `q3_decedent_dob`, `heir_address_row1`) + field type + confidence. Output schema: `modules/vlm_validator.py:FieldDecision`.

## For agents: fill a form from a fact pattern

Beyond building fillable PDFs (above), this repo can be driven from a
plain-language situation. See **`docs/agent-workflow.md`**:

1. **Route:** `python3 tools/find_forms.py "<situation>"` (keyword over metadata).
2. **Understand:** read `repo/forms/<ID>/skill.md` + `metadata.json` (source_url).
3. **Plan the fill:** build a canonical fact object `{matter, parties, party,
   facts}` (probate-native roles: `applicant`, `petitioner`, `decedent`,
   `adoptee`, `guardian`, `minor`, `attorney`, …) and run
   `python3 tools/fill_plan.py --form <ID> --case case.json`. This resolves every
   field's `fill_strategy.source` into: **resolved** `{field_id: value}` (from
   `case_dict.*` / `*_record.*`), a **narrative** worklist (the
   `llm_over_narrative` fields *you* compose from the fact pattern),
   **recompute** (derived), **blank** (signatures / human decisions), and
   **unresolved** (missing facts to collect).
4. **Compose narrative fields** from the fact pattern; put them back under
   `narrative_facts[field_id]` and re-run to fold them into `resolved`.
5. **Write the filled PDF:** fetch `metadata.json.source_url` (flat form), then
   `python3 tools/fill_pdf.py --form <ID> --case case.json --source <flat>.pdf
   --out filled.pdf`. Each form ships `fill_geometry.json` (`field_id → widget
   rects` from its aligned layout), so resolved text + checked options inject
   directly onto the flat source, with no pipeline at fill time (all 79
   forms).

`tools/canonical_adapter.py` bridges the court-style canonical object to
probate's case object; `tools/fill_plan.py` resolves it. Or run the **MCP
server**: `claude mcp add maine-probate-forms -- python3 tools/agent_server.py`
(`find_forms` / `get_form` / `fill_form`). This mirrors the companion
`maine-court-forms-oss` layer. Not legal advice; surface narrative/unresolved
fields and missing facts.
