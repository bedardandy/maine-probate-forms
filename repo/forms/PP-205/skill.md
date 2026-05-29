---
form_id: PP-205
form_title: Petition for Appointment of Guardian and/or Conservator (Adult)
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. §§ 5-301, 5-401"
  - "M.R. Prob. P. 17"
filing_deadline_days: null
service_required: true
service_recipients: respondent_and_interested_persons
n_fields: 105
addendum_supported: true
addendum_target_fields: ["*_address", "notify_person_*_address", "*_justification", "nominee_justification", "conservatorship_necessity_reason_*"]
parties:
  - petitioner
  - respondent
  - nominee
  - respondent_attorney
  - notify_person (1..6, repeating)
section_choices:
  - emergency_guardian_requested: yes/no
  - emergency_conservator_requested: yes/no
  - guardianship_scope: full | limited
  - conservatorship_scope: full | limited
  - interpreter_required: yes/no
  - nominee_bankruptcy: yes/no (with conditional explanation)
  - nominee_conviction: yes/no (with conditional explanation)
allowed_values:
  guardianship_scope: [full, limited]
  conservatorship_scope: [full, limited]
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 7 | deterministic from party records |
| legal_choice | 8 | human decision — scope, emergency, interpreter, nominee character |
| narrative_derived | 84 | LLM over narrative (mostly `notify_person_*` slots) |
| signature | 4 | wet-ink; never auto-fill |

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| repeating-slot duplication | `notify_person_4_5_6_*` filled with copies of `notify_person_3` | `dedupe_within(notify_person_name)` (auto-emitted) |
| slot drift past entity count | narrative names 3 notice persons → LLM fills 5 slots | repeating-slot prompt rule (already shipped); validator catches via dedupe |
| legal_choice over-confidence | `guardianship_scope=full` when narrative doesn't specify | flag as `human_required`; never trust LLM here |
| address overflow | long facility addresses truncated | addendum-eligible (auto-overflows to addendum page) |

## High-risk fields (red tier, eval-driven)

| field | risk | reasons |
|---|---|---|
| `notify_person_4_*` | 100 | wrong 4/5, oc 4/5, miscompr 4/5 |
| `notify_person_5_*` | 100 | wrong 4/5, oc 4/5, miscompr 2/5 |
| `notify_person_6_*` | ~80 | wrong 3/5 |

## Validators

| validator | applies to | what it does |
|---|---|---|
| `dedupe_within(notify_person_name)` | notify_person_*_name | rejects duplicate names |
| `dedupe_within(notify_person_address)` | notify_person_*_address | rejects duplicate addresses |
| `nonempty_if_desc` | notify_person_*_address, _relationship | requires accompanying _name |
| `populate_from_case_dict` | docket_no, case_caption | drift detection vs case dictionary |

## Conditional writability

```
emergency_guardian_requested == "yes"   → unlocks emergency-related narrative fields
emergency_conservator_requested == "yes" → ditto
nominee_bankruptcy == "yes"             → unlocks nominee_justification narrative
nominee_conviction == "yes"             → unlocks nominee_justification narrative
```

## Computed formulas

None.

## Risk distribution

```
green:  28
yellow: 42
orange: 18
red:    17
```
