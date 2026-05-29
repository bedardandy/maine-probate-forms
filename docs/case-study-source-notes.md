# Case Study Source Notes

Working notes on the project's history and the methodology behind the public
writeup. They are distilled from repository artifacts and development sessions,
and sanitized: raw session logs can contain private context, prompts, and local
paths that should not be published as-is.

## Narrative Spine

The public case study should not be framed as "I used AI to make PDFs fillable."
That undersells the hard part. The more accurate version:

1. Maine Probate forms encode legal workflow in visual layout, but many PDFs lack machine-readable fields.
2. A first pass can recover geometry, but geometry alone does not recover legal meaning.
3. A useful system needs layered structure: widgets, names, groups, validation, branching, and review state.
4. LLM/VLM review is valuable when scoped narrowly and checked against deterministic artifacts.
5. Automated repair is useful only when it is conservative and rollback-aware.
6. The end product is an inspectable form package: the PDF plus schemas, field catalogs, form-specific skills, classifications, formulas, trees, and review artifacts that support both deterministic automation and model-assisted workflows.

Publishing posture: this is not ready to present as a complete form release. The
pipeline has not been expanded across most forms or tested enough to justify
publishing generated PDFs as production-ready. The case study should say plainly
that current work is about methodology, infrastructure, and failure analysis.
Releasing polished examples too early risks the "gold document" problem:
overfitting the process to a few forms that got intense hand attention while the
broader corpus still contains unresolved edge cases.

## Timeline And Process

### 1. Field Geometry First

The project started from one observation: the source PDFs already contain a lot
of extractable structure, including underlines, square boxes, caption blocks,
table cells, signature lines, section labels, and repeated rows.

The better early strategy was deterministic PDF analysis:

- render pages for preview
- extract text lines and drawing elements
- detect horizontal rules as text fields
- detect small square rectangles as checkbox candidates
- infer labels from nearby text
- use table/grid context for repeated rows

This geometry-first approach lives in `modules/field_detector.py` and the related
analyzer code. It made the pipeline cheap to run and easy to debug. It also
exposed a clear limitation: geometry can say "there is a blank here," but not
always "what legal answer belongs here."

### 2. VLM As Gate, Not Primary Detector

The project moved toward VLM validation as a bounded decision layer. Instead of
asking a model to discover every field from a page image, the pipeline asks it to
review candidate fields produced by heuristics.

That division worked better because the model's job became narrow:

- keep or reject a candidate
- classify the field type
- assign a semantic snake_case name
- provide confidence or rationale

This produced a more inspectable failure mode. A bad decision traces to one
candidate, one page, and one semantic label instead of an opaque page-level
extraction.

`AGENTS.md` describes this as gating semantics through
`modules/vlm_validator.py:FieldDecision`, with a local OpenAI-compatible VLM
endpoint.

### 3. Alignment Was A Separate Problem

Semantic validation did not make the fields line up visually. Audits repeatedly
flagged fields that were named plausibly but placed incorrectly:

- text fields sitting on printed labels rather than underlines
- caption fields overlapping `PROBATE COURT` or `Docket No.` text
- boxes too tall for a single underline
- row assignments shifted by one row in table-like regions
- widgets with sentinel coordinates, effectively off-page

This led to a separate alignment and tooling layer:

- MCP tools for listing fields and moving rectangles
- local visual audit scripts
- tile-based page review for higher fidelity
- geometric snapping
- CommonForms/FFDetr comparison and crop-based realignment experiments

The lesson for the writeup: field detection, semantic naming, and visual
alignment are three different problems. Treating them as one problem creates
confusing regressions.

Recent work changed the alignment story. Vertical placement and text-overlap
problems are largely solved: fields no longer routinely sit on top of printed
labels or collide with surrounding text. The remaining hard problem is closer to
pixel-perfect glyph alignment, especially horizontal placement and the visual
feel of typed text inside underscored blanks. At that level the target is not
"the widget covers the blank" but "the rendered glyphs look like they belong on
the original form." That is a harder and more viewer-dependent standard.

The case study should say plainly that horizontal glyph alignment may need
per-form or even per-field tweaks. This is a practical limit of rebuilding
invisible PDF form semantics onto static documents designed for human
handwriting. The pipeline can solve broad geometry and prevent text overlap, but
the last few points of x-positioning may stay craft work.

### 4. CommonForms Was Useful, But Not A Drop-In Replacement

The CommonForms comparison report shows why the project did not replace the local
detector with an external one.

For sample forms, many fields matched well, but each side had different misses:

- `DE-101(I)`: 98 local widgets vs 100 CommonForms widgets, 72 matched
- `DE-104`: 8 local widgets vs 6 CommonForms widgets, 6 matched
- `PP-205`: 116 local widgets vs 112 CommonForms widgets, 95 matched
- `NC-001`: 43 local widgets vs 40 CommonForms widgets, 36 matched
- `DE-405`: 98 local widgets vs 95 CommonForms widgets, 94 matched

The useful insight was that two detectors produce complementary evidence. Fusion
improved some complex forms, but the full sweep showed mixed results: 16 clear
fused wins, 20 clear v2 wins, and 43 washes across 79 audited forms.

Write this as a sober engineering tradeoff. Fusion helps in some form families
and hurts in others. A production pipeline needs per-form audit metrics, not
faith in one detector.

### 5. Naming Became An Interface Contract

A large amount of audit work focused on field naming. The history includes
repeated cases where a field was fillable but useless or misleading:

- fields named from nearby pronouns like `i`
- fields named from preceding sentence fragments
- duplicate or numbered names that did not match legal roles
- caption fields confused between county, location, and docket number
- checkbox names copied from long body text rather than the option label

The case-study point: field names are API surface. They are how a case system
maps structured client and case data into a court form. A fillable PDF with
unstable or misleading names is not production-ready.

Good names encode legal role and local context, not just nearby text. Examples:

- `decedent_name`
- `personal_representative_name`
- `county_probate_court`
- `guardian_address_row1`
- `appointment_type_standard`
- `objecting_parties_other`

### 6. Checkboxes Were The Hardest Primitive

Both the history and the current scripts show that checkboxes caused
disproportionate complexity.

A square can mean:

- a standalone boolean
- one option in a radio group
- one member of a multi-select set
- an enabler for a following subquestion
- one side of an "or" branch
- a mirrored summary of a later choice
- a decorative or already-printed artifact that should not be a widget

PB-007 became a useful stress case because it contained appointment-type logic,
repeated visual choices, and branching relationships. The tree scripts now model:

- `select_one`
- `select_many`
- `enabler`
- virtual choices
- mirrored widgets
- `when` conditions

The public writeup should spend real time here. "Checkbox to radio promotion"
sounds small, but it is where legal logic starts to enter the PDF.

### 7. Tree Logic Was The Step Beyond AcroForms

Writing AcroForm widgets made the PDFs fillable. It did not make them
understandable as workflows.

The tree layer emerged because form logic is not always embedded in widgets:

- multi-column captions can imply a virtual choice, such as probate versus district court
- "or" patterns can make one real checkbox mutually exclusive with a virtual branch
- a checkbox can enable an explanation field below it
- repeated options can represent one logical value in multiple locations

The `build_form_tree.py` prompt captures a mature version of this. It asks the
model to transform a digest of text and widget IDs into a human-reviewable YAML
tree, which catches bad logic before the PDF is regenerated.

This is one of the most important points for the README and case study: a form
tree is not just metadata. It is what lets automated systems know which questions
apply.

This connects the project to existing legal automation tools like Docassemble.
Those systems are built around interviews: the next question depends on prior
answers. That is the right mental model for legal documents. A PDF is the visible
artifact; the real workflow is a decision tree with legal consequences. AcroForms
preserve a fillable surface, and a tree schema is what lets the system ask the
right next question, hide inapplicable branches, and leave a reviewable reasoning
trail for an attorney or trained reviewer.

The "no one objects" pattern is the clearest example:

- `No party objects` is a real checkbox, mutually exclusive with the objection branch.
- `Petitioner objects`, `Respondent objects`, and `Other objects` are not mutually exclusive with each other.
- There is no explicit checkbox labeled `Someone objects`.
- The logical "someone objects" branch is virtual, inferred when any objecting-party box is checked.

A flat widget list cannot represent that cleanly. A tree can model it as a
`select_one` between `none` and a virtual `objects`, followed by a gated
`select_many` of objecting parties.

### 8. The Form Package Became The Product

The project has broadened beyond "PDF plus tree." Each form is moving toward a
small document bundle that different automation layers can use:

- `form.pdf` — the tree-built AcroForm surface.
- `schema.json` — structured field metadata, risk tiers, data types, constraints, fill strategies, and conditional rules.
- `fields.csv` — a reviewable tabular catalog for humans and scripts.
- `skill.md` — form-specific operational guidance for agents or downstream tools.
- `classifications.yaml` — optional classification/review metadata.
- `formulas.yaml` — optional deterministic computed-field logic.
- `trees/<form>.yaml` — logical form structure and widget bindings.

This bundle matters because legal automation has more than one execution mode.
Some fields should be deterministic: copied from known case data, computed from
other values, or selected from controlled choices. Other fields are LLM-eligible:
a model can draft or classify, but the answer needs provenance and review. Some
fields stay human-required.

`build_form_schema.py` captures this transition. It classifies fields into
categories, assigns risk scores and tiers, tracks data constraints, records
`writable_when` and `required_when` boolean logic, and distinguishes
`deterministic`, `llm_eligible`, and `human_required` fill strategies. This is
the bridge between a fillable PDF and an automation-ready legal document.

The case-study language should treat this as a core refinement: the durable
artifact is a form package that lets deterministic code, non-deterministic
reasoning, and human review meet at explicit boundaries.

### 9. Recursive Repair Needed Guardrails

The recursive improvement loop was an important experiment:

1. audit current PDF
2. collect actionable fixes
3. apply rename/move/add/delete operations
4. re-audit
5. keep improving while issue count drops
6. stop or roll back when it regresses

The human review queue shows both promise and limits. Some forms dropped sharply:
PB-007 went from 147 initial issues to 20 before rollback status. Many other
forms improved but still needed review.

The main lesson was that deletes are risky. A model can confidently call a widget
spurious when it is actually a needed field. The code now gates deletes more
conservatively and uses rollback thresholds.

This belongs in the writeup because it is honest: the project did not discover a
magic self-healing pipeline. It found that self-healing has to be bounded,
auditable, and humble.

### 10. Human Review Remained Part Of The Architecture

The current `reports/human_review_queue.md` is part of the system design, not a
failure artifact.

Legal forms are high-stakes. An automatic pass/fail threshold helps, but
ambiguous forms still need human review. The project tracks:

- initial issue counts
- final issue counts
- iteration counts
- rollback versus needs-review status

This keeps uncertainty visible, which beats pretending the pipeline can silently
certify all outputs.

## Specific Failure Modes To Mention

These recur often enough to deserve a public "what went wrong" section:

- Overlapping text fields where one visual blank was split incorrectly.
- Fields placed on static labels instead of intended underlines.
- Header/caption fields confusing court type, county, location, and docket number.
- Table row shifts, especially when continuation rows resemble answer rows.
- Checkbox groups incorrectly left as independent booleans.
- Radio groups with invalid/sentinel rectangles after promotion.
- Names generated from nearby prose instead of legal meaning.
- Fused detector output helping dense forms but hurting others.
- Recursive repair improving issue counts while occasionally introducing regressions.
- Horizontal glyph alignment staying visually imperfect even after vertical/text-overlap fixes.
- Automation schemas needing to decide whether a value is deterministic, model-eligible, or human-required.
- Companion documents drifting out of sync unless the tree/schema/PDF generation path is treated as one package.

## Visual Evidence To Publish

Before publishing generated forms, it may be more useful to publish visual
examples of the problem space:

- PNGs of alignment failures with whiteboard-style markup showing where a widget landed versus where it should have landed.
- Cropped form portions showing nested checkbox/radio ambiguity.
- Examples where one option is mutually exclusive with a group, while the group itself allows multiple selections.
- Before/after crops for field snapping, row shifts, and caption-field confusion.
- A small annotated tree-schema example beside the corresponding form crop.

These visuals make the case study accessible without implying the whole form set
is production-ready. They also make the engineering pain points legible to
non-PDF specialists.

## Public Claims That Are Safe To Make

The README can claim:

- The project uses deterministic PDF geometry plus VLM validation.
- VLMs are used as bounded validators, not trusted end-to-end extractors.
- Field naming is treated as a semantic integration layer.
- Tree logic is being developed to represent branching and mutual exclusion.
- Forms are packaged with companion schemas, CSV catalogs, and form-specific skills so they integrate with deterministic and model-assisted workflows.
- Human review remains part of the quality process.
- The project is intended for permissive release and real-world testing, not as legal advice.

Avoid claiming:

- That every generated form is production-ready.
- That the pipeline fully automates legal judgment.
- That VLM decisions are authoritative.
- That CommonForms or any detector was universally better.
- That AcroForm fillability alone is the end goal.
- That the current form set has been tested across the whole Maine Probate corpus.
- That pixel-perfect glyph placement is solved across viewers and form families.

## Suggested README/Case Study Sections

1. **Problem:** official-looking PDFs without machine-readable structure.
2. **Why Probate Forms Are Hard:** layout-driven legal meaning, local practice, repeated parties, checkbox semantics.
3. **Architecture:** geometry detector, VLM validator, writer, audit loop, MCP tools, tree layer, schema/package builder.
4. **What I Tried:** heuristics, direct VLM, CommonForms fusion, visual audit, recursive repair.
5. **What Worked:** bounded model use, visual audits, semantic naming, tree extraction, vertical overlap fixes.
6. **What Failed Or Stayed Hard:** checkbox logic, row shifts, deletes, viewer compatibility, confidence thresholds, horizontal glyph alignment.
7. **Automation Versus Hand Filling:** different success criteria for humans and systems.
8. **Form Packages:** PDF plus schema, fields CSV, skills, formulas, classifications, and tree.
9. **Current Status:** usable pipeline under active review, not blanket certification.
10. **Visual Failure Gallery:** annotated crops showing alignment and logic issues.
11. **Open Source Intent:** permissive licensing, reuse by firms, legal aid, clinics, and legal tech.

## LFIB Framing

LFIB is the umbrella concept, not a dependency or a promise that this project
automates an entire law firm today.

The grounded framing:

- LFIB is an attempt to digitize and catalog as much of the legal workflow as possible.
- The goal is persistence: decisions, mappings, form logic, field names, review outcomes, and implementation traces should survive beyond a single model session or one-off script.
- As model reasoning improves, the stored artifacts give future systems better starting points.
- The project creates visibility into how decisions were made, which is essential in legal work.
- The near-term value is better structured forms and review workflows; the long-term ambition is a durable knowledge-work substrate.

This is stronger than "AI will automate legal documents." The claim is that AI
becomes useful once the underlying documents, schemas, decisions, and review
process are digitized enough for reasoning to attach to them.

## Open Follow-Up Questions

- Which generated PDFs are trusted enough for real production testing?
- Should public release include all output PDFs, or only forms passing a review threshold?
- Which permissive license fits best: MIT, Apache-2.0, BSD-3-Clause, or another?
- Should raw detector/intermediate artifacts be published, or only scripts and final outputs?
- Should the tree schema be project-specific or proposed as a general legal-form schema?
- How much LFIB context should the public README mention without making the project feel dependent on LFIB?
- Which 3-5 form crops best demonstrate the tree-schema problem visually?
- Should the first public artifact be a methodology post plus annotated PNGs rather than generated PDFs?
