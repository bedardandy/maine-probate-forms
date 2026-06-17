# Geometry review — non-extrapolatable follow-ups

These poll decisions could **not** be expanded into a programmatic rule. They
need a schema/tree change or fill-logic change, not a rect nudge, so they are
recorded here for per-field (often attorney-level) follow-up rather than
auto-applied.

## Multiline-below — the reviewer's articulated rule (detector candidate)

The second poll batch stated a general principle worth turning into a detector
rather than per-field nudges:

> "A non-underlined answer generally means open-ended length, so it usually
> needs a large multi-line text box **underneath** the prompt (not on the same
> line), spanning the margins; if there are too many, fall back to a
> 'see Exhibit A' setup."

Fields the reviewer assigned to this class:

| form | field | note |
|---|---|---|
| DE-403 | `personal_property_surety_1_description` | wide multi-line box under the line; overflow -> "see Exhibit A" |
| DE-403 | `personal_property_surety_2_description` | non-underlined open answer -> large box below, not on the same level |
| MISC-101 | `service_recipients` | description belongs underneath, multi-line, full margin width |
| PP-405 | `desc_personal_property_1` | under the prompt text, multi-line, span the width |
| AD-008 | `*_expenses_details` (w0/w1) | 2-line continuation: line 1 at the prompt's trailing underline, run over to line 2 |

Detector (built — `scripts/geometry_review/detect_multiline_below.py`): the
seed is **semantic, not geometric**. Calibration showed the reviewer's canonical
cases are NOT `no_line_support` — they are single-line widgets on a short
underline whose `fill_strategy.source == "llm_over_narrative"` (composed free
text). So the seed = narrative + every widget single-line (<=16pt) + field_id is
not an obvious short fact (date/age/name/number/fee/rate/...).

**Geometry QA was the real filter.** The first cut proposed *margin-wide* boxes
and 17 of 22 poll units collided with a neighboring widget — `room_below` only
looked at printed text. Three corrections, all in the detector now:

1. **Widget-aware room** — other-field widgets are obstacles too, so a box stops
   before the next field, not just the next printed line.
2. **Column-aware width** — a box on a multi-column row is sized to *its* column
   (midpoint to the nearest same-row widget on each side), not the full margins.
   This is what makes DE-403's two side-by-side surety descriptions get two
   non-overlapping half-width boxes (left `[92,361]`, right `[377,517]`, 16pt
   gutter) instead of one box stomping the other.
3. **Table-cell drop** — if ≥2 other widgets share the candidate's exact x-span
   on other rows, it's an aligned grid column (GS-014 `funds_received_*_N`) where
   box-below makes no sense; dropped as `table_cell`.
   A final proposed-rect-vs-all-widgets overlap test drops any residual as
   `widget_collision` (sibling box already there → structural).

Corpus result (511 narrative single-line non-shortfact fields, after the
2026-06-17 geometry corrections below): **44 clean box below** (15 column-width,
29 margin-wide) · 23 table/grid cells · 13 structural sibling-collisions · 431 no
room below (overflow / "see Exhibit A" class — now served by the addendum engine,
below).

Of the 44, only single-widget fields go to the poll (a multi-widget field already
has a continuation chain that wraps). A local fleet pass
(`classify_multiline.py`, Qwen + gemma) labels each paragraph vs short-value, and
**only both-models-agree** units are polled — a fleet split means the field is
ambiguous (almost always a mismapped table cell, e.g. AF-104
`reason_not_contacting`, whose widget sits under the *Name* column header while
`name_not_contacted` sits under *Reason*). Splits go to
`multiline_review_needed.jsonl`, not the poll. That left **3** render-validated,
collision-free units (`build_multiline_poll.py`, A=current vs B=box below): the
reviewer's canonical DE-403 ×2 (column-width) and MISC-101 (margin-wide).

### Votes (2026-06-17) and what they corrected

All three came back "other" with rich notes — the box-below *idea* confirmed,
the *geometry* corrected:

- **DE-403 `personal_property_surety_1/2_description`** — box-below ✅, two
  columns ✅, but: (a) the two columns must be **symmetric** (each flush to its
  page margin with a small gap), not sized to the off-centre underlying widget;
  (b) the box should run **down to the next question (comfortably) or the 1"
  bottom margin**, not clamp to ~4 lines. Both extrapolated into the detector:
  `column_bounds` now divides the body into N **equal** columns with a `COL_GAP`
  gutter; `proposed_box` runs to `min(next_obstacle - COMFORT_PAD, page_h -
  BOTTOM_MARGIN)`; and a post-pass snaps multi-col siblings to a **common top
  and bottom** so the pair is identical. Result: two 204×167 boxes, 18pt gap,
  both to y353. **APPLIED to `repo/forms/DE-403/fill_geometry.json`** (log:
  `multiline_applied.jsonl`); the rule is now validated for the column-width
  class.
- **MISC-101 `service_recipients`** — *reclassified*: not a box-below but a
  **table** (`service_recipient_name_1..N` narrow + `service_recipient_address_
  1..N` wide), and when the rows run out the **last row spans full width with a
  centred "See attached Addendum N for remaining service contacts."** This is a
  structural per-form re-map (see "Structural — service-recipient table" below)
  and the first table consumer of the overflow→addendum engine; a layout mockup
  was rendered for approval before authoring the widgets.

The box + schema multiline flag (rect.height>24) is fillable today; converting
the schema field to a declared paragraph type stays a pipeline follow-up.

## Overflow → addendum continuation pages (`tools/addendum.py`)

The reviewer's general rule for any field that can hold *multiple things* the
form has no room for: **say "See attached Addendum N for <subject>." and put the
full content on an appended continuation page** — one answer to a page (or more
for clarity), each page titled with the original question + " (continued)", and
the pages **continuing the form's own page numbering** "for clarity's sake".

`tools/addendum.py` implements this; `tools/fill_pdf.py` calls it (on by
default; `--no-addendum` / `overflow=False` to disable):

1. **Trigger** — a single paragraph box (the box-below class, rect.height>24)
   whose value will not wrap inside it (`addendum.fits`). Single-line and
   continuation-chain fields keep shrink-to-fit/split; they never spill.
2. **In-field reference** — the widget gets `field_reference(subject, n)` =
   *"See attached Addendum N for <subject>."*; `subject` is the (lower-cased)
   field label.
3. **Continuation pages** — one addendum per overflowed field, spilling onto as
   many sheets as needed (each still titled "(continued) [sheet k of m]"). Lists
   ('; '- or newline-delimited values) render as a numbered list; prose wraps.
4. **Page numbering** — `detect_page_scheme` finds the form's printed "Page N of
   M" token (top-right at 8pt for most forms; bottom-centre for a few). Addendum
   pages match that exact spot/size and read "Page N of TOTAL", and the base
   pages are rewritten from "of M" to "of TOTAL" (an opaque white overlay on the
   token bbox, *not* `apply_redactions`, which would erase the tightly-stacked
   line above — the id/rev/page header lines overlap vertically by ~1pt). Forms
   with no token fall back to a centred footer.
5. **Verify** — `tools/verify_filled.py` is overflow-aware: a widget holding a
   "See attached Addendum N for …" reference is a PASS when the expected value
   is found on a continuation page (`summary.overflowed_to_addendum`).

This is the deterministic home for the **431 "no room below"** fields (the
"see Exhibit A" class) and for any box-below value that outgrows its box.
Demonstrated end-to-end on DE-403 (long pledged-property list → Addendum 1,
packet renumbered 1..5 of 5).

### Modes and corpus enrolment

The per-field flag now exists: `catalog/overflow_fields.json` declares
`mode: list | paragraph | table`. `list` routes to an addendum once a field has
2+ items even if one line would fit (the "route lists to See attached" rule);
`table` draws a column table with a full-width centred overflow row (MISC-101);
`paragraph` is the box-below auto-trigger. `scripts/geometry_review/audit_overflow_fields.py`
inventories the corpus into `catalog/overflow_coverage.md` (3 classes) and
enables the safe slice. As of the 2026-06-17 rollout: **12 list-mode fields**
(local-fleet both-agree-confirmed; currency/number/date guarded out — the fleet
mislabelled DE-401's $ totals from their names) + the **MISC-101 table**.

**Box-below poll converged.** With DE-403 applied and list-mode enabled, the
both-agree box-below class is empty: the "open-ended paragraph" and "list" sets
overlapped almost entirely, so every high-confidence single-widget candidate is
now either applied (box-below) or routed (list/table). Of 13 single-widget fits
left, 9 are short-values (no box) and 1 is AF-104 `reason_not_contacting` (the
swapped-table structural case). Nothing remains to poll for box geometry.

**Repeating-group overflow is the next feature.** 44 entities are modelled as
fixed-capacity numbered records (`heir_1..12`, `interested_party_1..11`,
`distributee_1..12`, `notify_person_1..15`). When the actual count exceeds the
form's printed rows, the rows should fill 1..N and the rest go to an addendum
(behind a "see Addendum N" note in the last row). This needs a repeating-group
abstraction (group by entity, count vs capacity); inventory + capacities are in
`catalog/overflow_coverage.md`.

## Continuation — "part 1 of 2" line-split answers

Several notes flag an answer that wraps across two printed lines (line 1 ends at
the prompt's trailing blank, line 2 is the blank on the next row). Needs a
multi-widget continuation chain, not a single rect:

| form | field |
|---|---|
| N-118 | `change_in_dwelling_new_address`, `conservators_report_and_accounting_court_address` (address split from prior line) |
| N-115 | `pr_address` (check for a first part after "the address") |
| AD-028 | `putative_parent_likely_address` (2nd line; line 1 at "following address:") |

## Structural — the widget set itself is wrong (needs tree/schema edit)

| form | field | what the reviewer found | action |
|---|---|---|---|
| AF-105 | `stocks_bonds_specify` | two fields in one: a description on the left and a `$` dollar amount after the printed `$` | split into `stocks_bonds_specify` (text) + `stocks_bonds_value` (currency) in the tree |
| AF-105 | `expected_payments_explanation` | not a fillable blank — item 6 is an overarching question with sub-questions | remove the field from the schema |
| AF-105 | `dependents_list` (w2) | placement ambiguous ("tough to tell what this goes with") | re-derive against the form; likely a stray widget |
| AF-105 | `insurance_pension_value` | answer blank isn't where the widget is ("doesn't show where the spot is") | re-locate the widget from the source layout |
| AD-008 | `medical_/foster_care_/living_expenses_details` | 2-line continuation: line 1 should start at the underline after "child." / "birth mother." and overflow to line 2; long values reach the blank's right end | model as a multi-widget continuation chain (line1 + line2) |
| PP-405 | `corporate_surety_address` | belongs under "1. Name of corporate surety:"; if a field already exists there, this is a meta-question -> delete | re-point or remove from schema |
| AF-104 | `reason_not_contacting` / `name_not_contacted` | the bottom is a "Name \| Date \| Reason for not contacting" table, but `reason_not_contacting` is mapped under the *Name* column and `name_not_contacted` under the *Reason* column — columns are swapped, there is no Date widget, and no data rows | re-map widgets to the correct columns and add a multi-row continuation chain (found by the multiline-below fleet split) |
| MISC-101 | `service_recipients` | the Certificate-of-Service block is a "Name \| Mailing Address" table, but only ONE stray single-line widget is mapped (mis-placed at the header row). Vote: make it a real table, name column narrower than address | re-map to `service_recipient_name_1..3` (x≈72–224) + `service_recipient_address_1..3` (x≈232–540) data rows under the printed headers; fill distributes a recipients list across rows; when rows overflow, the **last row spans full width, centred** `table_overflow_row("service contacts", n)` and the remainder goes to an addendum (engine ready; widgets + fill-distribution pending). Mockup approved-pending. |

## Semantic — fill logic, not geometry

| form | field | what the reviewer found | action |
|---|---|---|---|
| AF-102 | `notary_county` | the printed context is "STATE OF … COUNTY OF …" (all caps); the value should be upper-cased on fill, and the right edge may run long for long county names | add an upper-case transform for this field (fill-time), allow overflow |
| DE-403 | `condition_decedent_residence` | should be town/city + state (e.g. "Falmouth, ME"), not a full street address | adjust the field's expected value / sample, not its rect |
| N-115 | `probate_court_address` | drop the state and zip -- the state is pre-printed (", Maine") and zip isn't needed; trim x1 before ", Maine" | town/city only; fill-logic + rect trim |
| AD-008, NC-001 | `notary_date` | filled by the notary at notarization; box spans the whole page but should be only wide enough for the widest date format at the default font (or omit the field) | size-to-content (date width); consider not auto-filling |

## Poll-harness improvements (process, surfaced by the 2nd batch)

- **Offer a combined lift+snap candidate.** The N-118 cluster all answered
  "other" because they need BOTH a vertical lift AND a horizontal snap, but the
  poll only offered them as separate A/B/C options. `build_poll.py` should emit
  a candidate that applies both at once.
- **Ease the right-trim suggestion.** Two N-118 notes said the poll's
  `trim_right` candidate was "1-2 character widths too aggressive". `build_poll`
  uses `GAP=3.0`; the snapped geometry uses `snap_horizontal.GAP=1.5`. Widen the
  poll's right-trim GAP toward the segment end so the suggestion matches what
  gets applied.
- **Wider context crops.** ~5 notes were "can't tell from here / is there a
  part 1 above?"; the crop window hides the prompt row above. Increase the
  vertical pad in `build_poll` so the question line is visible.

## Notes

- The **vertical "too low / merges underline"** trend was extrapolated
  programmatically — see `snap_underline.py`. Round 1 lifted ~146 rects to
  underline+0.5; the 2nd poll batch showed that was still a hair low (descenders
  on the line), so the rule was corrected to seat the bottom 1.5pt **above** the
  line and re-applied corpus-wide (1704 rects; sits_below_line 46->10). The
  items above are what remained after that and the horizontal snap-to-underline.
- The horizontal "box ≠ underline segment" pattern was applied only to the
  reviewer-flagged units (not blanket-extrapolated): of 1907 single-line
  fields, 1043 already match, 348 are multi-segment table rows, and the 379
  right-overruns are mostly into empty space — only the ones that cause real
  print-overlap are flagged by the sweep and trimmed there.
