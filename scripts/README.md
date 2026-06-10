# scripts/

140-odd scripts live here; **only the ones below are live** — wired into the
Makefile, CI, docs, or `tools/`. Everything else is research / one-off tooling
from building the form packages: it operates on private detection-pipeline
outputs (`forms/`, `intermediate/`, `output_*/`, `trees/` — not in this repo)
and is kept for provenance, not for use. If you are filling forms, you don't
need anything in this directory (use `tools/` — see `docs/agent-workflow.md`).

## Live scripts

| Script | Used by |
|---|---|
| `verify_fill_geometry.py` | `make verify`, CI `verify-geometry.yml` — validates shipped fill_geometry.json |
| `verify_manifest.py` | `make manifest-check`, CI `manifest.yml` — validates catalog/pdf_manifest.json |
| `regen_fill_geometry.py` | `make check` / `geometry` / `geometry-commit` (needs the maintainer `PIPELINE=` checkout) |
| `author_field_align.py` | `make align` — rebuilds catalog/field_alignment.json from the schemas |
| `verify_field_align.py` | `make align-check` |
| `build_statute_index.py` | `make statutes` (step 1) |
| `author_statutes.py` | `make statutes` (step 2) |
| `build_statute_reference.py` | `make statutes` (step 3) — regenerates docs/statute-reference/ |
| `verify_statutes.py` | `make statutes-check` |
| `fieldmap_pdf.py` | imported by `tools/enhance.py` (the field-map debug overlay step) |
| `gen_fill_geometry.py` / `geometry_coverage.py` | geometry regeneration + coverage, per `docs/maintenance.md` (need the pipeline checkout) |
| `lint_schema_sources.py` | schema hygiene — flags two-dot / malformed `fill_strategy.source` values |

## Everything else

Research / one-off (detection sweeps, bake-offs, audit loops, naming passes,
`infer_*.py` fact-pattern generators, fix batches). These assume the private
pipeline working tree and local model endpoints; they are not maintained as
public entry points and may not run outside the maintainer environment.
