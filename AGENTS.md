# Maine Probate Forms — agent guide

Route a plain-language probate situation to the right Maine probate form and
write a filled PDF. The runtime path is **deterministic and VLM-free**. The
detection pipeline that originally built the form packages is maintainer
tooling — it is not needed to fill forms, and its inputs/outputs are not in
this repo (see "Maintainer / build-time" at the bottom).

Not legal advice; output is a draft to verify against the official form.

## Runtime path: route → plan → fill → verify

1. **Route** the situation to a form id. You are the router:
   - Primary: read `cat_surgical` from `catalog/router_catalog.json`
     (~1.5k tokens — id | category | title + disambiguation notes) and pick
     directly.
   - Shortcut: `python3 tools/find_forms.py "<situation>"` for a keyword
     shortlist (an exact id in the query, e.g. "DE-101", wins outright; bare estate ids are the FORMAL petitions, informal applications carry an "(I)" suffix, e.g. "DE-101(I)").
   - `python3 tools/route_form.py` only when an OpenAI-compatible router
     endpoint is configured (`ROUTER_BASE_URL`/`ROUTER_MODEL`).
2. **Understand the form:** read `repo/forms/<ID>/skill.md` (filer role,
   statutes, parties, slot groups) and `metadata.json` (title, `source_url`).
   `skill.md` is the **only per-form file you need to read** — for per-field
   detail run `python3 tools/fill_plan.py --form <ID> --case case.json --full`
   instead of reading `schema.json`.
3. **Plan the fill:** build a canonical fact object `{matter, parties, party,
   facts}` (probate-native roles: `applicant`, `petitioner`, `decedent`,
   `adoptee`, `guardian`, `minor`, `conservator`, `attorney`, …) and run
   `python3 tools/fill_plan.py --form <ID> --case case.json`. Buckets:
   **resolved** `{field_id: value}`, **narrative** (the `llm_over_narrative`
   fields *you* compose), **recompute** (derived), **blank** (signatures /
   human decisions), **unresolved** (missing facts), **skipped** (`when`-gated
   off).
4. **Compose narrative fields** from the fact pattern; put them back under
   `narrative_facts[field_id]` (or canonical `facts`) and re-run — they fold
   into `resolved`.
5. **Write the filled PDF:**
   `python3 tools/fill_pdf.py --form <ID> --case case.json --out filled.pdf`.
   The flat source is auto-fetched from `source_url` (cached, SHA-256-verified
   against `catalog/pdf_manifest.json`); pass `--source <pdf>` to fill a copy
   you already have. Check `source_verified` in the result — `false` means the
   court re-issued the form since the geometry was measured.
6. **Verify:** `python3 tools/verify_filled.py --form <ID> --case case.json
   --filled filled.pdf` re-opens the output and diffs widget values against
   the plan. Report failures, the narrative fields you composed, and any
   unresolved facts.

Worked example: `docs/agent-workflow.md` and
`repo/forms/DE-101(I)/examples/case.example.json`. The packaged skill lives at
`skills/probate-route-and-fill/SKILL.md`.

### MCP (recommended for agents)

> Two MCP servers ship in this repo — don't confuse them:
> **`tools/agent_server.py`** is the forms-library server
> (`find_forms` / `get_form` / `fill_form`) — use that to route and fill forms.
> **`mcp_server.py`** (repo root) is a *separate maintainer tool* for
> interactive PDF field-rect alignment.

```bash
codex mcp add maine-probate-forms -- python3 tools/agent_server.py
```

`fill_form(form_id, case, out_dir)` returns the plan, auto-fetches the flat
source, writes the PDF, and reports `source_verified` plus a `verified_fill`
read-back summary; `fill_form_from_source` fills a flat copy you already
have. The server is built on the shared
[`maine-forms-engine`](https://github.com/bedardandy/maine-forms-engine) MCP
scaffold (standard `query`/`case`/`out_dir` parameters, one error shape), as
are the drift check (`tools/check_upstream.py` — now with
`--update-manifest`) and `tools/accessibility/` (schema-label /TU naming);
the geometry fill path stays this repo's own. (`.mcp.json` registers the
same server for Claude Code.)

## Repository layout

```text
tools/                 Runtime: find_forms, fill_plan, fill_pdf, verify_filled,
                       fetch, canonical_adapter, route_form, agent_server (MCP),
                       api_server (HTTP), enhance, accessibility/, export/, curate/
repo/forms/<ID>/       Per-form package: skill.md, metadata.json, schema.json,
                       fill_geometry.json, fields.csv, statutes.json (+ examples/)
catalog/               router_catalog.json, pdf_manifest.json, source_urls.json,
                       field_alignment.json, coverage reports
skills/                probate-route-and-fill (the agent skill)
docs/                  agent-workflow, automation-quickstart, integrating, …
router/                routing *evaluation harness* (runtime routing is tools/)
scripts/               maintenance + research scripts — see scripts/README.md
modules/, pipeline.py, download.py, config.py, loop.py, normalize_fields.py,
realign_fields.py, underline_heuristic.py, field_catalog.csv, mcp_server.py
                       Maintainer / build-time detection pipeline (below)
```

---

## Maintainer / build-time (not needed to fill forms)

Everything below documents the pipeline that *built* the form packages:
download Maine Probate Court PDFs, detect fillable regions with heuristics +
VLM validation, write AcroForm widgets, and derive the shipped geometry. It
operates on working directories that are **not in this repo** (`forms/`,
`originals_clean/`, `intermediate/`, `output_*/`, `reports/`, `trees/` — the
maintainer's private pipeline checkout; see `Makefile` `PIPELINE=`).

- `config.py` — paths, thresholds, VLM settings
- `download.py` — stage 1: scrape PDF forms from maineprobate.net
- `pipeline.py` / `loop.py` — orchestration and audit/fix loops
- `modules/` — pdf_analyzer, field_detector, vlm_validator, schema, catalog,
  taxonomy, acroform_writer, form_filler, preview
- `normalize_fields.py`, `realign_fields.py`, `underline_heuristic.py` —
  naming/geometry repair passes

**VLM:** the validator targets a local, OpenAI-compatible VLM endpoint
(`config.py`); any server works if responses match
`modules/vlm_validator.py:FieldDecision`. It runs in gating mode: each
heuristic candidate gets keep/reject + a snake_case semantic name + field type
+ confidence.

### Alignment MCP server (`mcp_server.py`)

A FastMCP server for interactive PDF field-rect work during geometry repair.
Runs over stdio; register it only if you maintain geometry:

```bash
codex mcp add --transport stdio pdf-forms -- python3 mcp_server.py
```

| Tool | Purpose |
|---|---|
| `list_fields(pdf_path)` | List form fields with name, type, page, rect, value |
| `get_page_dimensions(pdf_path)` | Width/height of each page |
| `update_field_rects(pdf_path, updates, output_path?)` | Batch-update field rectangles |
| `align_fields(pdf_path, reference_field, target_fields, axis, output_path?)` | Align fields to a reference on x, y, or both |

Coordinates: top-left origin, PDF points (72/inch), letter page 612×792,
rect `[x0, y0, x1, y1]`, y increases downward.

### Dependencies

`pip install -r requirements.txt` — PyMuPDF (fitz), pdfplumber, Pillow,
openai (VLM client), pydantic, mcp[cli]. FastAPI/uvicorn are optional (HTTP
surface only; see the commented block in requirements.txt).
