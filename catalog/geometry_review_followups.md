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

Candidate detector: a text widget that (a) follows a colon/question prompt with
**no underline segment** under or beside it (the sweep's `no_line_support`
flag, 125 corpus-wide) and (b) is single-line height, is a strong candidate to
become a multi-line box seated below the prompt. This is a tree/schema change,
not a rect nudge -- prototype against the `no_line_support` population before
trusting it.

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
