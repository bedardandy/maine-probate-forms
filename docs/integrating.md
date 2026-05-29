# Integrator guide

How to consume these form packages in your own system, and what guarantees to
rely on. Pair this with `docs/automation-quickstart.md` (end-to-end one-form
walkthrough) and `docs/architecture.md` (how the artifacts were built). To import
into a case-management / document-templating system (Clio, MyCase, PandaDoc,
DocuSign, HotDocs, Gavel), see `docs/templating-integration.md` +
`tools/export/`.

## What ships per form

Each `repo/forms/<ID>/` directory is self-contained:

| File | Use |
| --- | --- |
| `schema.json` | Field ids, labels, types, data constraints, risk tiers, `fill_strategy.source`, conditional logic. The integration contract. |
| `fields.csv` | Flat, reviewable field inventory. |
| `metadata.json` | Title, category, and **`source_url`** (the official flat PDF to fetch). |
| `skill.md` | Per-form operating notes, slot groups, known failure modes. |
| `fill_geometry.json` | `field_id → widget rects`. Inject resolved values onto the fetched flat PDF with no pipeline at fill time. |

The blank PDFs are not redistributed here; fetch each from its `source_url`
(consolidated in `catalog/source_urls.json`).

## The trust model

- **Key on `field_id`, never on a widget's detected name.** Detected widget
  names are auto-generated and can be wrong; the curated binding lives in the
  tree and surfaces as `schema.json` `field_id`. See `docs/architecture.md`.
- **`fill_geometry.json` rects are vision-audited and within page bounds.** A
  field may carry more than one rect (a value that prints in several places).
- **Risk tiers and `fill_strategy` tell you what is safe to automate.** Fill
  `deterministic` values from your data, route `llm_eligible` through a
  model-with-review step, and leave `human_required` (signatures, legal
  elections) blank for a person.

## Coverage: what is mapped, and what is not

`catalog/geometry_coverage.json` accounts for every fillable widget on every
form: mapped to a `field_id`, or unmapped. Check it before you assume a form is
fully covered. Each unmapped widget records its page, rect, detected name, and a
heuristic category:

| category | meaning |
| --- | --- |
| `mirror` | a duplicate of a mapped field (same value, second location) |
| `court_or_sig` | a signature or court/clerk-filled spot (not litigant-filled) |
| `likely_static` | printed text the detector over-captured (not an input) |
| `candidate_field` | an apparent input not yet modeled in a tree (a known gap) |

Categories are review hints, not authoritative (detected names are unreliable).
Treat `candidate_field` entries as the documented edge of coverage for that
form.

Each unmapped widget also carries an `assessment` (and a `reason`, plus a
`suggested_field_id` for the actionable ones) — a per-widget verdict from a
visual audit, meant as a worklist for a pipeline binding pass:

| assessment | do |
| --- | --- |
| `candidate_input` | apparent blank with no field — bind after confirming |
| `ambiguous` | overlaps a bound widget — verify duplicate vs mis-bind first |
| `spurious` | not a real input (over static text or a table corner) — skip |
| `review_marker` | checkbox-sized — confirm it is a real input |
| `bind_as_duplicate` | mirror of a mapped field — bind to the same field |
| `leave_unmapped` | static text or court/signature widget — leave as-is |

Binding a `candidate_input`/`ambiguous` widget is a pipeline task: it needs the
alignment + vision-audit loop to confirm placement against filled output, then
the schema build (which carries risk/eval enrichment). It is not a safe hand
edit.

## Regenerating

`fill_geometry.json` and the coverage report are derived. If you fix an
alignment or a source form changes, regenerate them from the pipeline build
outputs (`docs/maintenance.md`); do not hand-edit. Adding a not-yet-modeled
field runs the full schema build, which carries risk/eval enrichment; that too
is a pipeline task, not a manual schema edit.

## MCP

```bash
claude mcp add maine-probate-forms -- python3 tools/agent_server.py
```

`find_forms(query)`, `get_form(form_id)`, `fill_form(form_id, facts, source_pdf)`.

> **Not legal advice.** Output is a draft to verify against the official form.
