---
form_id: N-118
form_title: Notice of Change (Guardianship / Conservatorship)
jurisdiction: Maine
court: Probate
filer_role: guardian_or_conservator
statutes:
  - "18-C M.R.S.A. §§ 5-321, 5-417"
filing_deadline_days: 30
filing_deadline_anchor: change_event_date
service_required: true
service_recipients: interested_persons
n_fields: 61
addendum_supported: true
addendum_target_fields:
  - "*_address"
  - "*_reason_for_change"
  - "*_individual_name"
  - "*_dwelling_*"
  - "persons_notified"
parties:
  - filer
  - guardian
  - conservator
  - individual_under_protection
section_headers_exclusive: true   # only one section is checked per filing
section_headers:
  - appointment_of_guardian
  - appointment_of_conservator
  - change_in_permanent_dwelling
  - change_in_dwelling
  - revised_guardianship_plan_approved
  - interim_annual_report
  - revised_conservatorship_plan_approved
  - other_notice
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 1 | deterministic — `role` |
| legal_choice | 15 | human — exclusive section selection |
| narrative_derived | 40 | LLM (conditional on parent section being checked) |
| signature | 2 | wet-ink |

## Critical structural rule

**Exactly one section header should be `true`.** All child fields are
`writable_when: {field: <parent>, equals: true}`. Filling child fields
across multiple sections is the most common LLM error on this form
(34 errors on Qwen pattern-1 fill: see CHANGES below).

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| children of un-elected sections filled | both `appointment_of_guardian_*` AND `change_in_permanent_dwelling_*` populated | `writable_when` validation (already firing — 34 errors on Qwen p1) |
| `other_notice` checked but `other_notice_description` empty | yes/no without follow-up | conditional `required_when` |
| `persons_notified` confuses persons with addresses | LLM combines fields | risk_tier red; flag for human |
| docket format drift | "2025-GA-0412" instead of "YYYY-NNNN-XX" | data_type docket_number with ME format |

## High-risk fields (red tier, eval-driven)

| field | risk | reasons |
|---|---|---|
| `other_notice` | 100 | wrong 2/5, oc 2/5, miscompr 3/5 |
| `other_notice_description` | 97 | wrong 2/5, oc 2/5, miscompr 3/5 |
| `interim_annual_report` | 70 | wrong 1/5 |
| `revised_conservatorship_plan_approved` | 70 | wrong 1/5 |
| `revised_conservatorship_plan_approved_approval_date` | 70 | wrong 1/5 |

## Validators

| validator | applies to | what it does |
|---|---|---|
| `writable_when` enforcement | all section-child fields | rejects writes when parent section is not `true` |
| data_type: date | `*_order_date`, `*_approval_date`, `*_change_date` | enforces format |
| data_type: docket_number | docket_number | enforces Maine YYYY-NNNN-XX |
| `populate_from_case_dict` | docket_number, county_name | drift detection |

## Conditional writability

Every section header gates its children. Examples:

```
appointment_of_guardian == true →
  appointment_of_guardian_court_name, _order_date, _guardian_name,
  _individual_name are writable.

change_in_permanent_dwelling == true →
  change_in_permanent_dwelling_individual_name, _new_address,
  _nature_of_dwelling, _reason_for_change are writable.

revised_guardianship_plan_approved == true →
  revised_guardianship_plan_approved_approval_date is writable.

other_notice == true →
  other_notice_description, persons_notified are writable.
```

## Computed formulas

None.

## Risk distribution

```
green:  32
yellow: 15
orange: 9
red:    5
```
