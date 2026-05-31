---
form_id: PP-410
form_title: Petition for Interim Order
form_revision: "09-12-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 5-310 (Interim orders)"
filing_deadline_days: null
service_required: true
service_recipients: "interested_persons_and_respondent"
n_fields: 54
addendum_supported: true
addendum_target_fields:
  - "notified_person_*_address"
  - "petitioner_interest"
  - "interim_order_relief"
  - "appointment_explanation_or_funds"
parties:
  - petitioner
  - respondent
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
| party_attr | 7 | deterministic from petitioner + attorney + respondent records |
| narrative_derived | 40 | LLM over narrative + dedupe validators |
| legal_choice | 1 | human — `appointment_type_needed` |
| signature | 4 | wet-ink |

## Known LLM failure modes (May-2026 eval)

Same repeating-slot family as DE-405/PP-205. The `notified_person_*`
table degrades quickly past slot 2: narrative typically supplies 1-3
parties but the form has up to 5 slots; Qwen duplicates earlier entries
to fill empty slots.

| symptom | example | guard |
|---|---|---|
| slot duplication | `notified_person_3` ↔ `notified_person_4` filled with same person | `dedupe_within(notified_person_name)` |
| orphan address | `notified_person_5_address` filled, name empty | `nonempty_if_desc` (treats `_name` as the head field) |
| relationship hallucination | "sister" assigned when narrative is silent | risk_tier high; flag for human |
| `appointment_type_needed` over-confidence | LLM picks "emergency guardian" when narrative supports "temporary conservator" | category=legal_choice; never trust LLM |

## High-risk fields (red tier)

| field | score | reasons |
|---|---|---|
| `notified_person_3_relationship` | 100 | wrong 3/5 |
| `notified_person_4_name` | 100 | wrong 3/5 |
| `notified_person_4_address` | 100 | wrong 3/5 |
| `notified_person_4_relationship` | 100 | wrong 3/5 |

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(notified_person_name)` | notified_person_*_name | repeated names across slots |
| `dedupe_within(notified_person_address)` | notified_person_*_address | repeated addresses |
| `nonempty_if_desc` | notified_person_*_address, *_relationship | orphan rows |
| `populate_from_case_dict` | docket_no, county | drift |

## Conditional writability

None encoded. `appointment_type_needed` is a one-of choice that
gates the relief narrative: could be expressed as `writable_when`
on `interim_order_relief` and `appointment_explanation_or_funds`
once the PDF is inspected.

## Risk distribution

```
green:  ~22
yellow: ~25
orange: ~3
red:    4
```
