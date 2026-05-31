---
form_id: DE-405
form_title: Inventory
form_revision: "5-6-21"
jurisdiction: Maine
court: Probate
filer_role: personal_representative
statutes:
  - "12 M.R.S.A. § 3-706"
  - "M.R. Prob. P. 14"
filing_deadline_days: 90
filing_deadline_anchor: pr_appointment_date
service_required: true
service_recipients: interested_persons
addendum_supported: true
addendum_target_fields: ["*_desc"]
n_fields: 92
sections:
  - name: real_property
    slot_prefix: real_prop
    row_range: [1, 6]
  - name: tangible_personal_property
    slot_prefix: tang
    row_range: [7, 16]
  - name: intangible_personal_property
    slot_prefix: intang
    row_range: [18, 25]
  - name: subtotals_and_totals
    fields: [gross_value_real_property, gross_value_real_encumbrances,
             gross_value_personal_property, gross_value_personal_encumbrances,
             calc_gross_real_property, calc_gross_personal_property,
             calc_gross_inventory, calc_gross_real_encumbrances,
             calc_gross_personal_encumbrances, calc_net_inventory]
  - name: footer
    fields: [appraisers_info, items_appraised_by_pr, pr_dated, pr_signature,
             attorney_name, attorney_address, attorney_phone]
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 3 | deterministic from attorney record |
| computed | 10 | deterministic; recompute from row values |
| narrative_derived (repeating_slot) | 72 | LLM over narrative + dedupe validators |
| narrative_derived (free_text) | 2 | LLM over narrative |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

```
gross_value_real_property         = Σ real_prop_N_val      N∈1..6
gross_value_real_encumbrances     = Σ real_prop_N_enc      N∈1..6
gross_value_personal_property     = Σ tang_N_val + Σ intang_N_val
gross_value_personal_encumbrances = Σ tang_N_enc + Σ intang_N_enc
calc_gross_real_property          = gross_value_real_property
calc_gross_personal_property      = gross_value_personal_property
calc_gross_inventory              = calc_gross_real_property
                                  + calc_gross_personal_property
calc_gross_real_encumbrances      = gross_value_real_encumbrances
calc_gross_personal_encumbrances  = gross_value_personal_encumbrances
calc_net_inventory                = calc_gross_inventory
                                  − calc_gross_real_encumbrances
                                  − calc_gross_personal_encumbrances
```

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| slot duplication within section | Honda CR-V on tang_7 AND tang_14 | `dedupe_within(tang_desc)` |
| cross-section duplication | Vehicle on tang AND intang | `cross_section_dedupe(intang_desc,real_prop_desc)` |
| section mixing | TIAA retirement placed in tang | category schema; reject by content match |
| narrative slot drift | narrative "item 14" → LLM wrote at slot 9 | post-fill: re-key by narrative-stated index when present |
| orphan encumbrance | `*_enc` filled with no `*_desc` | `nonempty_if_desc` |
| arithmetic drift | gross_value off vs Σ rows | `recompute_from_dependencies` |

## Field constants

```
section_capacity:
  real_prop:  6     # rows 1..6
  tang:       10    # rows 7..16
  intang:     8     # rows 18..25
slot_field_suffixes: [desc, val, enc]
slot_field_data_types:
  desc: text
  val:  currency
  enc:  currency
```

## Risk distribution (this form)

```
green:  19   (case_constants, party_attr, computed, low-risk slots)
yellow: 39
orange: 18
red:    16   (high-eval-risk repeating slots: tang_13_*, intang_20-22_*)
```

## Validators (executed by validate_filled.py)

```
- dedupe_within(<group>_desc): rejects duplicate descriptions
- cross_section_dedupe(<other_groups>): rejects desc appearing in 2 sections
- nonempty_if_desc: <prefix>_<n>_val|enc must be empty when <prefix>_<n>_desc empty
- recompute_from_dependencies: computed cells must equal formula above
- populate_from_case_dict: case_constant cells must equal case_dict[field]
```

## Multi-widget / addendum behavior

```
addendum_eligible_fields: <prefix>_<n>_desc       # single-line text widgets
addendum_disallowed_fields: <prefix>_<n>_val      # values must fit a single cell
addendum_disallowed_fields: <prefix>_<n>_enc      # encumbrances must fit a single cell
addendum_disallowed_fields: calc_*, gross_value_* # numeric, no overflow expected
```
