---
form_id: PP-413
form_title: Petition for Termination, Removal, or Resignation (Guardianship/Conservatorship)
form_revision: "9-12-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 5-318 (Termination of guardianship)"
  - "18-C M.R.S.A. § 5-432 (Termination of conservatorship)"
filing_deadline_days: null
service_required: true
service_recipients: "individual_under_protection_and_interested_persons"
n_fields: 40
addendum_supported: true
addendum_target_fields:
  - "*_reasons_detail"
  - "requested_orders"
parties:
  - petitioner
  - attorney
  - notify_person (1..N, repeating)
section_choices:
  - belief_reason_termination: yes/no
  - belief_reason_removal: yes/no
  - belief_reason_resignation: yes/no
section_headers_exclusive: false  # multiple grounds can be invoked
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 6 | deterministic from petitioner + attorney records |
| legal_choice | 5 | human — which grounds (termination/removal/resignation) + scope |
| narrative_derived | 24 | LLM over grounds narrative + notify-person slots |
| signature | 3 | wet-ink |

## Known LLM failure modes (May-2026 eval)

The form supports THREE non-exclusive grounds (termination, removal,
resignation). Each has its own `belief_reason_<X>` checkbox plus a
matching `<X>_reasons_detail` narrative. Qwen tends to fill all three
reason narratives even when only one ground was invoked.

| symptom | example | guard |
|---|---|---|
| reason narrative for un-checked ground | `removal_reasons_detail` filled when `belief_reason_removal == false` | `writable_when` (encode in classifications.yaml) |
| notify_person duplication | `notify_person_3` ↔ `notify_person_4` duplicate | `dedupe_within(notify_person_name)` |
| `requested_orders` over-broad | LLM enumerates orders narrative doesn't request | hand review |

## High-risk fields (red tier)

| field | score | reasons |
|---|---|---|
| `notify_person_3` | 100 | wrong 4/5 |
| `notify_person_4` | 100 | wrong 4/5 |

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(notify_person_*)` | notify_person slots | duplicate notice recipients |
| `populate_from_case_dict` | docket_no, county | drift |

## Conditional writability

```yaml
# To encode in classifications.yaml:
termination_reasons_detail:
  writable_when: {all_of: [{field: belief_reason_termination, equals: true}]}
removal_reasons_detail:
  writable_when: {all_of: [{field: belief_reason_removal, equals: true}]}
resignation_reasons_detail:
  writable_when: {all_of: [{field: belief_reason_resignation, equals: true}]}
```

## Risk distribution

```
green:  ~18
yellow: ~16
orange: ~4
red:    2
```
