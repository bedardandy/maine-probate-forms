# Vision audit findings (filled-render QA)

`scripts/vision_audit_oss.py` fills each form that ships a synthetic
`examples/case.example.json`, renders it with poppler, runs a FIND vision pass for
rendering defects, then gates every candidate through an adversarial VERIFY pass
(N skeptics, majority to survive). Vision is a *screen, not a verdict* — every
finding below was re-checked by hand against the actual field values and the
form's printed-text coordinates before any fix.

Run scope: the 4 forms with synthetic examples (DE-101, DE-301, PB-007, PP-406).
The raw run lands in `router/vision_audit_oss.tsv` (gitignored). This file records
the human adjudication and the fixes applied.

## Candidates and verdicts

| Form | Finding (vision) | Verify gate | Human verdict | Action |
|---|---|---|---|---|
| PB-007 | `minor_children_names` renders a raw `[{'name': ...}]` literal | **dropped** (0/2) | **REAL bug** — gate false-negative | Fixed: `fill_plan._render_value` |
| DE-301 | caption "Estate of Theodore J. Crawford" overlaps the printed "Estate of" | confirmed (2/2) | **REAL defect** | Fixed: value root + widget x |
| DE-101 | item 2 "Address, email, telephone of Applicant" blank | confirmed (2/2) | **False positive** | None (see below) |
| DE-101 | item 8 "Date of Decedent's death" blank | confirmed (2/2) | **False positive** | None (see below) |

## Fixes applied

**PB-007 — structured value printed as a Python repr (real; gate missed it).**
`minor_record.minor_children` is `[{name, dob}, ...]`. The deterministic resolver
returned the list and the fill str()'d it onto the form as
`[{'name': 'Aiden M. Reyes', 'dob': '2014-09-12'}]`. Added `_render_value()` in
`tools/fill_plan.py` at the resolve→write boundary: lists/dicts render as readable
text (each item's values joined by ", ", items by "; "); scalars pass through. Now
`Aiden M. Reyes, 2014-09-12`. Applied to both the deterministic and
narrative-supplied assignment paths. *Lesson: the verify gate's skeptic prompt
lists intentional-blank exclusions but has no notion of "machine-readable junk
text", so it wrongly read the literal as acceptable — a gate blind spot, logged.*

**DE-301 — caption double-prints "Estate of" and the widget clips the label (real).**
The form pre-prints `Estate of ____` (text at x72–108, underline from x110). The
`estate_of_decedent` field is typed `person_name`, but the example data and
`canonical_adapter` both baked an "Estate of" prefix into the value, and the
widget started at x97 — under the printed "of". Two coordinated fixes:
- **Value root:** `tools/canonical_adapter.py` now emits the decedent *name* for
  `estate_of_decedent` (the `caption` key still carries the full "Estate of X"
  string for any field that needs it). Mirrored in the DE-301 examples
  (`case.example.json`, `case.split.json`) and the unused DE-101 example key.
- **Geometry:** DE-301 `fill_geometry.json` widget x0 97 → 112, onto the underline
  (coordinate-verified against the printed-text band, then re-rendered: typed name
  now sits cleanly after the printed "Estate of", no overlap).

## False positives (no action)

Both DE-101 items are `fill_strategy.source = llm_over_narrative` fields
(`applicant_contact_info`, `decedent_date_of_death`) — the agent composes them from
the fact pattern and re-runs (see `docs/agent-workflow.md` step 4). The vision
audit fills **deterministically only** (no narrative pass), so these are *expected*
blanks, not binding/geometry defects. The verify gate confirmed them because its
skeptic prompt excludes signature/order/optional fields but not "narrative-deferred"
fields — a second gate blind spot, logged here rather than fixed in the prompt to
avoid teaching the gate to dismiss genuinely-blank required fields.

Minor upstream note (not fixed here — needs the non-vendored `trees/`):
`decedent_date_of_death` is classified `llm_over_narrative` while its sibling
`decedent_date_of_birth` is deterministic, even though the fact sits directly in
`decedent_record`. Defensible either way; flagged for the upstream classifier.

Outputs are drafts — not legal advice.
