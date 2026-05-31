---
form_id: PP-510
form_title: Petition to Transfer Guardianship/Conservatorship + Provisional Order
form_revision: "2-3-21"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 5-431 (Transfer of conservatorship)"
  - "18-C M.R.S.A. § 5-321 (Transfer of guardianship)"
filing_deadline_days: null
service_required: true
service_recipients: "individual_under_protection_and_interested_persons_in_both_states"
n_fields: 50
addendum_supported: true
addendum_target_fields:
  - "individual_connection_to_new_state"
  - "objection_to_transfer"
  - "notified_person_*_address"
parties:
  - petitioner
  - individual_under_protection
  - attorney
  - notified_person (1..N, repeating)
slot_groups:
  - notified_person
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 8 | deterministic from petitioner + individual + attorney records |
| legal_choice | 5 | human — return-to-maine yes/no, move-permanently yes/no, etc. |
| narrative_derived | 31 | LLM over transfer narrative + notified-person slots |
| signature | 4 | wet-ink |

## Known LLM failure modes (May-2026 eval)

Notified-person slot table: same family as PP-205, PP-410, PP-413.
Narrative typically names 1-2 people to notify in each state; LLM
padding past slot 2 produces duplicates.

| symptom | example | guard |
|---|---|---|
| early slot duplication | `notified_person_2_address` ↔ slot 1 address | `dedupe_within(notified_person_address)` |
| `transfer_destination_state` confusion | LLM writes the current state (Maine) instead of destination | semantic; no automated guard |
| `individual_connection_to_new_state` paraphrase loss | Qwen abstracts specific facts ("family", "doctor") into generic statements | hand review |
| `individual_return_to_maine` over-confidence | LLM picks "yes" when narrative is silent | category=legal_choice |

## High-risk fields (red tier)

| field | score | reasons |
|---|---|---|
| `notified_person_2_address` | 100 | wrong 3/5 |
| `notified_person_2_relationship` | 100 | wrong 3/5 |

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(notified_person_name)` | _name slots | duplicate names |
| `dedupe_within(notified_person_address)` | _address slots | duplicate addresses |
| `nonempty_if_desc` | _address, _relationship | orphan rows |
| `populate_from_case_dict` | docket_no, county | drift |

## Conditional writability

```yaml
# Encode in classifications.yaml:
individual_return_to_maine:
  required_when:
    any_of:
      - field: individual_move_permanently
        equals: false
objection_to_transfer:
  writable_when:
    any_of:
      - field: individual_move_permanently
        equals: true
```

## Risk distribution

```
green:  ~24
yellow: ~22
orange: ~2
red:    2
```
