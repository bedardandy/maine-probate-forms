# Geometry review — non-extrapolatable follow-ups

These poll decisions could **not** be expanded into a programmatic rule. They
need a schema/tree change or fill-logic change, not a rect nudge, so they are
recorded here for per-field (often attorney-level) follow-up rather than
auto-applied.

## Structural — the widget set itself is wrong (needs tree/schema edit)

| form | field | what the reviewer found | action |
|---|---|---|---|
| AF-105 | `stocks_bonds_specify` | two fields in one: a description on the left and a `$` dollar amount after the printed `$` | split into `stocks_bonds_specify` (text) + `stocks_bonds_value` (currency) in the tree |
| AF-105 | `expected_payments_explanation` | not a fillable blank — item 6 is an overarching question with sub-questions | remove the field from the schema |
| AF-105 | `dependents_list` (w2) | placement ambiguous ("tough to tell what this goes with") | re-derive against the form; likely a stray widget |
| AF-105 | `insurance_pension_value` | answer blank isn't where the widget is ("doesn't show where the spot is") | re-locate the widget from the source layout |
| AD-008 | `medical_/foster_care_/living_expenses_details` | 2-line continuation: line 1 should start at the underline after "child." / "birth mother." and overflow to line 2; long values reach the blank's right end | model as a multi-widget continuation chain (line1 + line2) |

## Semantic — fill logic, not geometry

| form | field | what the reviewer found | action |
|---|---|---|---|
| AF-102 | `notary_county` | the printed context is "STATE OF … COUNTY OF …" (all caps); the value should be upper-cased on fill, and the right edge may run long for long county names | add an upper-case transform for this field (fill-time), allow overflow |
| DE-403 | `condition_decedent_residence` | should be town/city + state (e.g. "Falmouth, ME"), not a full street address | adjust the field's expected value / sample, not its rect |

## Notes

- The **vertical "too low / merges underline"** trend these decisions also
  mentioned WAS extrapolated programmatically — see `snap_underline.py`
  (lifted ~146 single-line rects corpus-wide). The items above are what
  remained after that and after the targeted horizontal snap-to-underline.
- The horizontal "box ≠ underline segment" pattern was applied only to the
  reviewer-flagged units (not blanket-extrapolated): of 1907 single-line
  fields, 1043 already match, 348 are multi-segment table rows, and the 379
  right-overruns are mostly into empty space — only the ones that cause real
  print-overlap are flagged by the sweep and trimmed there.
