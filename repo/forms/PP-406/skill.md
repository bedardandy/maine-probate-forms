---
form_id: PP-406
form_title: Inventory (Conservatorship)
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: conservator
statutes:
  - "18-C M.R.S.A. § 5-417 (Conservator's inventory)"
filing_deadline_days: 90
filing_deadline_anchor: "appointment_date"
service_required: true
service_recipients: "individual_under_protection_and_interested_persons"
n_fields: 96
addendum_supported: true
addendum_target_fields: ["*_desc"]
parties:
  - conservator
  - individual_under_protection
  - attorney
sections:
  - name: real_property
    slot_prefix: real_prop
    row_range: [1, 6]
  - name: tangible_personal_property
    slot_prefix: tang_prop
    row_range: [7, 17]
  - name: intangible_personal_property
    slot_prefix: int_prop
    row_range: [18, 25]
slot_groups:
  - real_prop
  - tang_prop
  - int_prop
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 5 | deterministic from conservator + attorney records |
| computed | 10 | deterministic; recompute from row values |
| narrative_derived | 77 | LLM over narrative + dedupe validators |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

Encoded in `formulas.yaml`. The 10 totals mirror DE-405's structure
with PP-406-specific naming:

```
gross_value_real_property         = Σ real_prop_N_value      N∈1..6
gross_value_real_encumbrances     = Σ real_prop_N_enc        N∈1..6
gross_value_personal_property     = Σ tang_prop_N_value  N∈7..17
                                  + Σ int_prop_N_value   N∈18..25
gross_value_personal_encumbrances = (same shape for `_enc`)
calc_gross_real                   = gross_value_real_property
calc_gross_personal               = gross_value_personal_property
calc_gross_inventory              = calc_gross_real + calc_gross_personal
calc_gross_real_enc               = gross_value_real_encumbrances
calc_gross_personal_enc           = gross_value_personal_encumbrances
calc_net_inventory                = calc_gross_inventory
                                  − calc_gross_real_enc
                                  − calc_gross_personal_enc
```

## Known LLM failure modes (May-2026 eval)

Structural cousin of DE-405. Same Qwen failure patterns:

| symptom | example | guard |
|---|---|---|
| slot duplication | `tang_prop_8_desc` ↔ `tang_prop_13_desc` filled with same item | `dedupe_within(tang_prop_desc)` |
| section mixing | financial account placed in `tang_prop_*` (should be `int_prop_*`) | `cross_section_dedupe(int_prop_desc, real_prop_desc)` |
| narrative position drift | LLM places "item 14" at slot 9 | re-key from narrative if explicit; otherwise hand review |
| orphan encumbrance | `tang_prop_12_enc` filled while `tang_prop_12_desc` empty | `nonempty_if_desc` |
| arithmetic drift | `gross_value_personal_property = $597,620.88` when row sum = $790,123.76 | `recompute_from_dependencies` (caught $192K error in eval) |
| literal `"None"` in currency cell | `real_prop_2_enc = "None"` | `data_type: currency` rejects |

## High-risk fields (red tier)

| field | score | reasons |
|---|---|---|
| `tang_prop_13_*` | 100 | wrong 3/5; slot duplication |
| `tang_prop_14_*` | 100 | wrong 3/5; slot duplication |
| `tang_prop_15_*` | 88-100 | wrong 2-3/5 |
| `tang_prop_16_desc` | ~80 | wrong 2/5 |

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(real_prop_desc)` | real_prop_1..6_desc | duplicate real property |
| `dedupe_within(tang_prop_desc)` | tang_prop_7..17_desc | duplicate tangible items (most frequent Qwen bug) |
| `dedupe_within(int_prop_desc)` | int_prop_18..25_desc | duplicate financial accounts |
| `cross_section_dedupe` | each section's `_desc` | same asset across sections |
| `nonempty_if_desc` | each `_value`, `_enc` | orphan value/encumbrance |
| `recompute_from_dependencies` | all `gross_value_*`, `calc_*` | bad arithmetic |
| `data_type: currency` | all `_value`, `_enc`, totals | non-numeric text, units, narrative |
| `populate_from_case_dict` | `county_probate_court`, `docket_no` | drift |

## Conditional writability

None: flat inventory form.

## Risk distribution

```
green:  19
yellow: 61
orange: 7
red:    9
```
