# Maine Probate Forms AcroForm Pipeline

This project turns Maine Probate Court PDF forms into structured, fillable AcroForm PDFs.

Most Maine probate forms published at `maineprobate.net` date from the 2019-2020 overhaul of the Maine probate code. Many are well-structured visually, but the PDF files are flat. A person can print and handwrite them, or type into a viewer's annotation layer, but software cannot discover, name, validate, or fill the intended fields.

The goal is to close that gap for both people filling forms by hand and automated legal workflows. The pipeline downloads the source PDFs, analyzes their geometry and text, detects candidate fillable regions, validates and names those candidates, writes PDF form widgets, then builds higher-level logic: normalized field names, radio groups, checkbox dependencies, form trees, and companion schemas.

This is part of a broader "Law Firm in a Box" effort. The output is meant for reuse beyond one private workflow: permissively licensed tooling and fillable forms for other people, clinics, firms, courts, and legal tech projects.

> **Companion project:** Maine Judicial Branch *court* forms (the unified-court
> portal) live in [**`maine-court-forms`**](https://github.com/bedardandy/maine-court-forms).
> Those PDFs already have AcroForm
> fields, so that project *maps* them to a canonical fact object. The probate
> PDFs here are flat, so this project *creates* the fields first. Court forms
> need field mapping; probate forms need field creation.

## Why This Exists

Probate forms are standardized enough that automation should be possible, but local court practice and form design make them harder than ordinary web forms:

- The official PDFs often contain visual blanks, checkboxes, signature lines, and repeated sections without embedded fields.
- Some categories already contain AcroForm widgets, while others are fully flat.
- Field intent is often conveyed by layout rather than by explicit labels.
- A checkbox may mean a simple boolean, a radio option, an enabler for a later subsection, or one side of an "or" branch.
- A form that is easy for a person to read can still be ambiguous for software.

The project treats a fillable PDF as only the first layer. The fuller target is a form package that supports automated filling, review, validation, deterministic rules, model-assisted drafting, and integration with case-management systems.

## Current Pipeline

The workflow:

1. Download and catalog forms from `maineprobate.net`.
2. Render and analyze each PDF page with PyMuPDF and pdfplumber.
3. Detect likely field regions using geometry heuristics.
4. Validate, reject, classify, and semantically name candidates with a local VLM.
5. Write AcroForm widgets into the PDFs.
6. Realign and normalize fields through iterative audits.
7. Promote checkbox clusters into radio groups where the form logic requires mutual exclusion.
8. Build form trees that describe branching, enablers, repeated groups, and implicit choices.
9. Generate companion `schema.json`, `fields.csv`, and form-specific guidance for downstream automation.
10. Simulate fills to catch field logic failures before relying on the output.

The repository also includes an MCP server for interactive inspection and alignment from Codex or Claude Code.

## Documentation

- `docs/architecture.md` — how the packages were built, what is authoritative, and how geometry is validated.
- `docs/integrating.md` — consuming the packages, the trust model, and reading the coverage report.
- `docs/automation-quickstart.md` — end-to-end walkthrough of one form.
- `docs/agent-workflow.md` / `docs/fact-pattern-example.md` — driving a fill from a plain-language situation.
- `docs/maintenance.md` — regenerating derived geometry and coverage.
- `docs/statute-reference/` — per-form **statutes for consideration**: each form mapped to the Title 18-C sections worth weighing when answering its questions, with a former-Title-18-A transition note and **Maine Law Court cases** (`caselaw.md`) tied to forms through the statutes they construe. Generated from per-form `repo/forms/<ID>/statutes.json` sidecars; start at `docs/statute-reference/README.md`. ⚠️ **Experimental — AI/LLM-generated and not attorney-reviewed. Considerations, not legal advice.**
- `docs/digital-assets-access.md` — accessing a deceased person's online accounts (Google, Apple, Meta, Microsoft, Yahoo/AOL, X, Amazon), grounded in 18-C Article 10 (Maine RUFADAA) and tied to the forms that produce a fiduciary's authority.
- `catalog/geometry_coverage.json` — every fillable widget per form, mapped to a field or recorded as a known gap.

> ⚠️ The statute and case-law layer is **experimental, AI/LLM-generated, and not attorney-reviewed** — an aid for filling these forms, **not legal advice** and not a substitute for a Maine attorney. Statute section text is quoted from legislature.maine.gov; the selection of statutes/cases and any holdings are the model's annotations and may be wrong. Which code governs an estate can turn on the date of death (Title 18-C took effect 2019-09-01). Rebuild it with `make statutes`; validate with `make statutes-check`.

## Repository Layout

```text
config.py              Configuration, thresholds, paths, VLM settings
download.py            Scrape and catalog Maine probate PDFs
pipeline.py            Main pipeline orchestration
normalize_fields.py    Field naming normalization
realign_fields.py      Field rectangle adjustment helpers
mcp_server.py          FastMCP server for interactive field inspection/alignment

modules/
  pdf_analyzer.py      Page rendering, text, line, and rectangle extraction
  field_detector.py    Heuristic text, checkbox, signature, and table detection
  vlm_validator.py     VLM gating and semantic field decisions
  schema.py            Pydantic models for detection output
  taxonomy.py          Field type taxonomy
  acroform_writer.py   AcroForm widget writing
  form_filler.py       Fill existing AcroForm PDFs with data
  preview.py           Visual field previews

scripts/
  build_form_digest.py       Convert a PDF into a VLM-readable digest
  build_form_tree.py         Extract logical form trees from digests
  apply_tree.py              Apply tree logic back to PDF widgets
  build_form_schema.py       Build schema.json and fields.csv from tree output
  recursive_improvement.py   Audit/fix loop for generated PDFs
  simulate_fills.py          Exercise generated form logic
  validate_existing_widgets.py
  promote_to_radio_group.py
  geometric_snap.py
  local_alignment_review.py
  opus_alignment_review.py

forms/                 Downloaded source PDFs
originals_clean/       Clean source copies
intermediate/          Analysis and validation artifacts
output_fused/          Fused generated AcroForms
output_recursive/      Iterative audit/fix outputs
output_tree/           Tree-applied outputs
repo/forms/<ID>/       Per-form package: schema.json, fields.csv, metadata.json
reports/               Audit and recursive improvement reports
trees/                 Logical form tree YAML
```

## What Worked

Geometry-first detection worked better than asking a model to "find all fields" from a page image. The forms have strong visual regularities: underlines, boxes, table cells, signature lines, and repeated caption blocks. Traditional PDF analysis is fast, cheap, inspectable, and good at producing candidate rectangles.

VLMs serve better as validators than as primary detectors. The validator acts as a gate: for each heuristic candidate, keep or reject it, assign a field type, and produce a semantic snake_case name. This keeps the model's job narrow and makes failures easier to audit.

Interactive alignment through MCP matters. A small set of tools (listing fields, reading page dimensions, updating rectangles, aligning groups to a reference field) makes it possible to fix forms conversationally without rebuilding the pipeline.

Tree extraction became necessary once the project moved beyond "can I type into this blank?" A court form contains logic: choose probate or district court, choose one appointment type, fill extra details only if a checkbox applies, or pick either "no party objects" or one or more objecting parties. Encoding that logic separately from widget coordinates makes the PDFs more useful in automated systems.

The package-building work separates automation strategy from appearance. Each form carries a fillable PDF plus a schema, field catalog, risk tiers, conditional rules, and form-specific instructions. Deterministic code, model-assisted drafting, and human review all operate against the same explicit form contract.

## What Did Not Work Cleanly

Visual-only AcroForm generation is brittle. A field that looks centered in a rendered screenshot can have a rectangle that behaves poorly in a PDF viewer. Small differences in widget height, baseline, or checkbox placement matter.

Vertical placement and text-overlap issues have become tractable, but pixel-perfect glyph alignment remains difficult. The remaining roughness is horizontal: getting typed characters to sit where a reader expects on an old underline-based PDF can require per-form or per-field tuning.

Checkboxes are hard. The same small square can represent an independent boolean, a radio option, a grouped multi-select, a branch enabler, or a visual marker that should not become a widget. Layout and nearby words like "or" often matter more than the box itself.

Deleting fields during iterative repair caused regressions. The recursive improvement script now treats deletes conservatively, because removing a field it judged spurious can be worse than leaving a questionable one for review.

Naming is not cosmetic. Names are the interface for automated filling. A field named `date` or `name_3` is fillable but useless to a program. Stable semantic names let a case system map client data into court forms.

## Design Tradeoffs

### Human-filled PDFs versus automated systems

For human filling, the priorities are visual placement, tab order, legibility, and compatibility with common PDF viewers. A person can resolve ambiguity by reading the surrounding form.

For automation, determinism and semantics matter more. Field names need to be stable. Radio groups need to be real radio groups. Data needs to carry mutually exclusive branches directly rather than leaving them implied by layout. Repeated people, addresses, heirs, interested persons, and fiduciaries need predictable naming patterns.

The project supports both, with a real tension between them. The best widget structure for a human PDF viewer is sometimes not the best logical model for a case-generation system.

### Heuristics versus models

The pipeline uses heuristics first and models second. Heuristics are easier to debug and run in bulk. Models are better at semantic judgment, but asking them to do geometry from scratch wastes the structure the PDF already contains.

This division also makes the project more portable. A user can improve the geometry detector, swap models, run a local router, or hand-review outputs without changing the whole architecture.

### Form trees versus flat field catalogs

A flat catalog is useful for filling values. A tree is useful for deciding which values should exist. Probate forms need both.

The tree layer captures concepts like:

- `select_one` radio groups
- `select_many` checkbox sets
- `enabler` checkboxes that gate later questions
- `when` conditions for branch logic
- virtual choices implied by multi-column layout
- mirrored widgets that represent one logical answer in multiple visual locations

This layer is still experimental. It is what turns the forms from "PDFs with boxes" into reusable legal workflows.

### Form packages versus single artifacts

A generated PDF is only one artifact. Each form ships as a package under
`repo/forms/<ID>/`:

- `schema.json` — field metadata, constraints, risk, fill strategy, and embedded
  operating guidance (under `_skill_metadata_override`)
- `fields.csv` — reviewable field inventory
- `metadata.json` — form id, title, category, and **`source_url`** (the official
  maineprobate.net PDF)

> **PDFs are not in this repo.** The blank source PDFs are public records on
> maineprobate.net. Fetch them from each form's `source_url` (consolidated in
> `catalog/source_urls.json`). They are **flat** (no form fields). To produce a
> *filled* PDF, `tools/fill_pdf.py` injects resolved values straight onto the
> fetched source using the shipped `fill_geometry.json` — no detection pipeline
> or VLM needed at fill time. (The pipeline in `modules/` is only for
> *regenerating* that geometry.) See `docs/automation-quickstart.md`.

Low-risk values fill deterministically, ambiguous values route to a model-assisted or human workflow, and high-risk legal decisions stay reviewable.

### Staying current — detecting a re-issued form

Because fills draw text at the coordinates in `fill_geometry.json`, a form's
layout shifting upstream is the worst-case failure: the source still downloads,
but the text lands in the wrong place. `catalog/pdf_manifest.json` pins the
SHA-256 of the exact revision each form's geometry was measured against (build it
with `make manifest`; the bootstrap cross-checks every PDF's page count and size
against its geometry, so a mismatch is reported rather than silently pinned).

```bash
python3 tools/check_upstream.py            # re-probe source URLs; flag CHANGED / GONE
```

maineprobate.net filenames are revision-stamped, so a re-issued form usually
turns up as `GONE` (the pinned URL stops resolving) rather than `CHANGED`. The
probe is read-only and exits non-zero on any change, so it runs as a weekly
early-warning (`.github/workflows/drift.yml`). When a form is flagged, re-derive
its geometry, then rebuild the manifest. At **fill time**, `tools/fill_pdf.py`
verifies the source PDF against the manifest first — `MCF_VERIFY_BLANK=warn`
(default), `strict`, or `off` — so a re-issued source can't be filled unnoticed.

## License

Apache-2.0. See `LICENSE`.

## Local VLM Setup

The validator currently targets a local llama-router fleet. The default endpoint is:

```text
http://localhost:8083/v1
```

The validator uses a local model fleet for privacy, cost control, and repeatable bulk validation. The endpoint accepts OpenAI-compatible requests, so you can swap model providers as long as the response schema stays compatible.

## MCP Server

The project includes a FastMCP server for interactive field operations:

| Tool | Purpose |
| --- | --- |
| `list_fields(pdf_path)` | List AcroForm fields with name, type, page, rect, and value |
| `get_page_dimensions(pdf_path)` | Return width and height for each page |
| `update_field_rects(pdf_path, updates, output_path?)` | Batch-update widget rectangles |
| `align_fields(pdf_path, reference_field, target_fields, axis, output_path?)` | Align fields to a reference on the x axis, y axis, or both |

The coordinate system is top-left origin, measured in PDF points. A standard letter page is usually `612 x 792`.

## Scope and status

What ships and is usable today: the per-form packages (schema, field catalog,
fill geometry, form-specific guidance), the deterministic fill path
(`tools/fill_pdf.py`), routing, the export and accessibility layers, and the
tooling to regenerate geometry. All 79 forms have geometry and fill end to end.

What still needs a human in the loop: the generated forms are not a
battle-tested form set. Treat the output as a draft mapping — review every filled
form against the official PDF before relying on it. The most valuable artifact is
the workflow, the per-form structure, and the failure analysis, not a guarantee
of court-ready PDFs.

Active development continues on normalization across form families, radio-group
promotion, logical form trees, audit/fix loops, and simulation tests.

## Open design questions

Contributions and discussion welcome on the harder modeling questions:

- How much form logic should live inside the PDF versus in a companion schema?
- How should user-facing field names balance legal precision against broad reusability?
- What level of automated confidence is enough before a form should still require human review?
- How should local court variation be represented without fragmenting the form model?

## License

Licensed under the **Apache License 2.0** (see [`LICENSE`](LICENSE)) — permissive
and compatible with commercial use. The goal is to make the workflow and forms
useful to as many people as possible: self-represented litigants, legal aid
organizations, small firms, researchers, and commercial legal-tech projects.

This repository is not legal advice, and generated forms should be reviewed
before use in real cases.

## Case Study Notes

This section is a living draft for a fuller writeup.

See `docs/case-study-source-notes.md` for working notes on the project's history and the methodology behind this writeup.

The core lesson so far: legal form automation is mostly about recovering structure that was present for human readers but absent from the file format. The visible form already carries a lot of knowledge: indentation, repeated rows, section headings, "or" dividers, caption columns, signature blocks, and grouped checkboxes. The hard part is translating that into a durable machine interface.

The most promising architecture has been layered:

- Detect geometry with deterministic code.
- Use a VLM for bounded semantic decisions.
- Write ordinary AcroForm widgets for broad PDF compatibility.
- Normalize field names for integration.
- Build a tree model for legal and workflow logic.
- Build companion schemas and field catalogs for downstream automation.
- Audit visually and structurally.
- Keep a human review path for ambiguous forms.

"Fillable" is not a binary property. A PDF can be fillable yet unpleasant to use, visually correct yet semantically useless, or well-named yet unsafe for automation when mutually exclusive options are built as unrelated checkboxes. The target is a form package that is fillable, inspectable, semantically stable, and explicit about its own uncertainty.

One planned part of the case study is an annotated visual gallery: PNG crops of alignment failures, checkbox/radio ambiguity, and nested branch logic. Those examples should make the problem legible without implying that the entire form corpus is already production-ready.
