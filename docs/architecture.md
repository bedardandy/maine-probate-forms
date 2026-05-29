# Architecture: how this repo was built

This explains how the form packages were produced and which artifacts to trust,
so an integrator can rely on them with eyes open. The headline: the source PDFs
are flat (no fields), and a layered pipeline recovers structure that was present
for human readers but absent from the file.

## The derivation chain

```
maineprobate.net PDF (flat)
   │  download + catalog
   ▼
heuristic field detection (geometry: lines, boxes, table cells)
   │  VLM gating (keep/reject + snake_case name + type) per candidate
   ▼
AcroForm widgets written into the PDF  →  output_fused/<form>_fused.pdf
   │  reading-order widget ids W001, W002, …  (build_form_digest)
   ▼
form tree  trees/<form>.yaml   (human/Opus-authored: field_id → W-id,
   │                            radio groups, enablers, when-conditions)
   ├─► schema.json + fields.csv     (build_form_schema: types, risk, fill strategy)
   └─► fill_geometry.json           (gen_fill_geometry: field_id → widget rects)
```

The fused PDFs and trees are build inputs that live in the separate detection
pipeline; they are not shipped. What ships per form is the package under
`repo/forms/<ID>/`: `schema.json`, `fields.csv`, `metadata.json`, `skill.md`,
and `fill_geometry.json`.

## What is authoritative, and what is not

- **The tree is the curated source of truth.** It binds each `field_id` to a
  physical widget id and encodes the form's logic. It was authored against the
  fused PDF and validated by rendering *filled* PDFs and auditing placement.
- **Detected widget names are not reliable.** The detector auto-names each
  widget (e.g. it may label the docket blank `county`). The tree deliberately
  overrides those names. Do not infer a field's meaning from a widget's detected
  name; trust the `field_id` in `schema.json`.
- **`fill_geometry.json` rects are derived, never hand-edited.** They come from
  the fused PDF the tree describes. When a form or alignment changes, regenerate
  (see `docs/maintenance.md`).

## Validation

Two cross-checks guard the geometry:

- `scripts/verify_fill_geometry.py` — every geometry `field_id` exists in the
  schema, every rect is well-formed and within page bounds, and every non
  plan-only form ships geometry. Wired into CI.
- A field-type vs widget-type cross-check (text/date/currency must bind a Text
  widget; an option must bind a checkbox/radio). This catches a tree bound to
  the wrong fused PDF: for example a form-id that matched a sibling revision
  (a "Formal Petition" vs an "(I) Informal" PDF that share a filename prefix).
  `gen_fill_geometry` now picks the fused PDF whose widget types best fit the
  tree, so the right source is used even when siblings exist.

## Coverage and known gaps

`scripts/geometry_coverage.py` writes `catalog/geometry_coverage.json`, which
accounts for **every** fillable widget on each form: mapped to a field, or
unmapped. Unmapped widgets ("orphans") are recorded with page, rect, detected
name, widget type, and a heuristic category (`mirror`, `court_or_sig`,
`likely_static`, `candidate_field`). The category is a review hint, not ground
truth, because detected names are unreliable. As of this writing every
widget-bearing field that the tree binds has correct geometry; the unmapped
widgets are either court/signature spots, static text the detector
over-captured, or input areas not yet modeled in a tree. Adding a not-yet-modeled
field is a pipeline task (it regenerates the enriched schema), not a hand edit.

> **Not legal advice.** Generated output is a draft to verify against the
> official form before filing.
