---
form_id: DE-507
form_title: Petition for Adjudication of Testacy and/or Successor PR
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. §§ 3-401, 3-413, 3-416"
filing_deadline_days: null
service_required: true
service_recipients: interested_persons
n_fields: 68
addendum_supported: true
addendum_target_fields: ["petitioner_interest", "newly_discovered_property", "priority_questions", "other_notice_persons"]
parties:
  - petitioner
  - decedent
  - former_pr
  - appointed_pr
  - interested_party (1..N, repeating)
fee_fields: [filing_fee, mailing_notices_fee, abstracts_fee, other_fees]
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 8 | deterministic from party records |
| legal_choice | 2 | human — `demand_for_notice`, `notice_request` |
| narrative_derived | 54 | LLM over narrative + dedupe validators (interested_party slots, fees, dates) |
| signature | 1 | wet-ink; never auto-fill |

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| late interested_party slots duplicated | slots 3+ repeat earlier party data | `dedupe_within(interested_party_name)` (auto-emitted) |
| fee amounts hallucinated | `filing_fee` filled when narrative is silent | `nonempty_if_desc`-style flag; mark as not_applicable |
| date confusion | `termination_date` filled with `appointment_date` value | type-check (both pass as date) — escalate to risk_tier |
| former_pr vs appointed_pr mixup | LLM swaps the two PR names | category split (party_attr with explicit party label) |

## High-risk fields (red tier, eval-driven)

| field | risk | reasons |
|---|---|---|
| `interested_party_3_*` | 100 | wrong 4/5 |
| `interested_party_4_name` | 100 | wrong 3/5 |
| `interested_party_4_address` | 76 | wrong 2/5 |

## Validators

| validator | applies to | what it does |
|---|---|---|
| `dedupe_within(interested_party_name)` | interested_party_*_name | rejects duplicates |
| `dedupe_within(interested_party_address)` | interested_party_*_address | rejects duplicates |
| `nonempty_if_desc` | interested_party_*_address, _relationship | requires `_name` filled first |
| `populate_from_case_dict` | docket_no, county_probate_court | drift detection |

## Computed formulas

None encoded yet: `total_fees` (if it exists on the form) could sum
the four fee fields. Defer until confirmed by inspection.

## Conditional writability

```
demand_for_notice == true  → unlocks notice_request and other_notice_persons
```

(Not yet encoded in writable_when; needs PDF inspection to confirm
exact field-checkbox bindings.)

## Risk distribution

```
green:  12
yellow: 49
orange: 1
red:    6
```
