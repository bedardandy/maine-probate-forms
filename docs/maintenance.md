# Maintenance: regenerating fill geometry

`repo/forms/<ID>/fill_geometry.json` (`field_id → widget rects`) is what lets
`tools/fill_pdf.py` write a real filled PDF onto the fetched flat source. It is
**derived** from a separate field-detection pipeline's build outputs, so when
source forms are updated or an alignment is fixed, it must be regenerated, never
hand-edited.

## The derivation chain

```
field_id ──(trees/<ID>.yaml)──▶ W-id ──(build_form_digest on output_fused/)──▶ rect/page
```

`trees/` and `output_fused/` are gitignored build artifacts that live in the
**separate detection pipeline** (not part of this repo). `scripts/gen_fill_geometry.py`
reads them; `scripts/regen_fill_geometry.py` orchestrates a full regenerate +
validate + write; `scripts/verify_fill_geometry.py` is the standalone CI gate;
`scripts/geometry_coverage.py` writes `catalog/geometry_coverage.json`, the
per-form account of every fillable widget (mapped or unmapped). When a form-id
has sibling fused PDFs (e.g. a "Formal Petition" and an "(I) Informal" that
share a prefix), the generator picks the one whose widget types best fit the
tree, so the right source is used; regenerate coverage after any geometry change.

## When to regenerate

- **A source form changed** (new revision on maineprobate.net). Re-run the
  detection pipeline (download → detect → realign → fuse → tree) so
  `output_fused/` + `trees/` reflect the new form, then regenerate (below).
- **An alignment is wrong.** Fix it on the fused PDF (the `pdf-forms` MCP
  `align_fields` / `update_field_rects` tools, or by re-running the realign
  step), then regenerate. Geometry follows the fused PDF; don't edit the JSON.

## How to regenerate

You need a local checkout of the detection pipeline (the separate project that
produces `trees/` + `output_fused/`); point `--pipeline-root` at it. Geometry is
written into this repo (`--repo .`, the default).

```bash
# regenerate all forms (or a subset after a targeted fix)
python3 scripts/regen_fill_geometry.py \
    --pipeline-root /path/to/detection-pipeline \
    --repo .
# subset:  --forms DE-101,PP-203

# validate, review, commit
python3 scripts/verify_fill_geometry.py --repo .
git status
git add repo/forms catalog && git commit -m "Regenerate fill_geometry"
```

`regen` **validates before writing**: invalid geometry (rect off the page, a
field_id not in the schema) is reported and skipped, never committed. It also
rewrites `catalog/fill_geometry_status.json`, the ledger of generated vs.
`plan_only` forms (any form with no fillable widgets; it legitimately has no
geometry and falls back to the fill *plan*). Variant ids (`AF-101.vA`) carry no
fused PDF of their own; the generator resolves them against their base form's
fused layout, so they generate normally. (Currently all 79 forms generate.)

Or use the make targets: `make geometry PIPELINE=/path/to/detection-pipeline`
and `make verify`.

## Detecting staleness

After re-running the pipeline (or any time you're unsure), check whether the
shipped geometry still matches its build inputs:

```bash
make check PIPELINE=/path/to/detection-pipeline      # or:
python3 scripts/regen_fill_geometry.py --pipeline-root <pipeline> --repo . --check
```

This regenerates every form's geometry *in memory* and diffs it against the
shipped files (writing nothing) and exits non-zero if anything drifted,
listing each form as `CHANGED` (rects moved), `NEW` (now fillable, not yet
shipped), or `REGRESSED` (had geometry, now binds 0 widgets; investigate the
tree/fuse step). Then run `make geometry` to refresh.

> Why content-diff and not file mtimes? Git does not preserve modification
> times (a fresh checkout or worktree stamps every file with the checkout
> time), so "the fused PDF is newer than its geometry" is unreliable here.
> Regenerate-and-diff is authoritative because regeneration is deterministic.

## Verifying (CI)

`scripts/verify_fill_geometry.py` is pure stdlib (no PyMuPDF, no build
artifacts) and checks, per form: every `field_id` exists in the schema, every
rect is well-formed and within page bounds, and every non-`plan_only` form
ships geometry. It exits non-zero on errors; wire it into CI (see
`.github/workflows/verify-geometry.yml`).

## Schema `when` conditions

Conditional fields carry a `when` string on the schema field (e.g.
`"when": "applicant_legal_interest == 'other'"`). `tools/fill_plan.py` evaluates
it against the case to gate fields in or out (the `skipped` bucket). These are
**derived** from the node-level `when:` on the detection pipeline's `trees/<ID>.yaml` nodes
(node-id ↔ field-id), so a schema rebuild must re-harvest them from the trees —
they are not authored on the published schema by hand.
