---
form_id: AF-105
form_title: Financial Affidavit
jurisdiction: Maine
court: Probate
filer_role: form_subject
statutes:
  - "M.R. Prob. P. 14"
filing_deadline_days: null
service_required: false
n_fields: 91
addendum_supported: true
addendum_target_fields: ["additional_statement", "*_specify", "dependents_list", "other_personal_property"]
parties:
  - form_subject
  - spouse
  - form_subject_employer
  - notary
required_witness: notary
section_groups:
  income: [salary_wages_*, unemployment_*, social_security_*, tanf_*, alimony_child_support_*, other_income_*]
  assets: [cash_on_hand, checking_*, savings_*, retirement_*, investment_*, real_estate_*, vehicle_*, insurance_pension_value, other_personal_property]
  expenses: [food_expenses, housing_expenses, utilities_expenses, other_living_expenses, medical_support_payments]
  computed: [medical_support_total]
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 7 | deterministic from form_subject + spouse records |
| legal_choice | 5 | human — `spouse_employed`, `own_real_estate`, `request_type`, `anticipate_other_employment` |
| narrative_derived | 69 | LLM over financial-declaration narrative |
| computed | 3 | recompute from row values (formulas to encode) |
| signature | 4 | wet-ink + notary |

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `other_income_*` triple-filled when not in narrative | LLM invents an "other" income line | repeating-slot rule (already); flag `_specify` as risky |
| period vs amount mismatch | `salary_wages_period: monthly` but `_amount: $52000` (annual figure) | type contract checks numeric; unit confusion needs human review |
| spouse fields filled when subject is unmarried | `spouse_name` hallucinated | `writable_when: spouse_employed in (yes, no)` (parent gate) |
| expense category confusion | utilities written under `food_expenses` | type-correct but semantically wrong; flag for review |
| medical_support_total ≠ Σ payments | computed cell hand-filled wrong | `recompute_from_dependencies` (needs formulas.yaml) |

## High-risk fields (red tier, eval-driven)

| field | risk | reasons |
|---|---|---|
| `other_income_amount` | 73 | wrong 2/5, oc 2/5, miscompr 1/5 |
| `other_income_period` | 73 | wrong 2/5, oc 2/5, miscompr 1/5 |
| `other_income_specify` | 73 | wrong 2/5, oc 2/5, miscompr 1/5 |
| `medical_support_payments` | 70 | wrong 1/5, oc 1/5, miscompr 1/5 |
| `medical_support_total` | 70 | computed but currently filled by LLM |

## Validators

| validator | applies to | what it does |
|---|---|---|
| data_type: currency | all `*_value`, `*_amount`, `*_expenses`, fees | rejects text/non-numeric |
| data_type: phone | `phone`, `*_phone` | enforces US phone format |
| data_type: address | `address`, `*_address` | (currently structural only) |
| `recompute_from_dependencies` | `medical_support_total` | requires formulas.yaml encoding |
| `populate_from_case_dict` | docket_no, county | drift detection |

## Conditional writability

```
spouse_employed != null    → spouse_* fields writable
own_real_estate == "yes"   → real_estate_market_value, mortgage_holder writable
```

(Encode in classifications.yaml writable_when when willing to verify
on the PDF.)

## Computed formulas

`formulas.yaml` is not yet authored. Candidate:

```yaml
medical_support_total:
  op: field
  id: medical_support_payments
# (or sum if multiple medical_support_* rows exist — verify on PDF)
```

## Risk distribution

```
green:  ~30
yellow: ~51
orange: ~5
red:    5
```
