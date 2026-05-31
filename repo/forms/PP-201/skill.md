---
form_id: PP-201
form_title: Petition for Appointment of Guardian (Adult)
form_revision: "07-01-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 5-301 (Petition for guardian — adult)"
  - "18-C M.R.S.A. § 5-302 (Procedure)"
filing_deadline_days: null
service_required: true
service_recipients: "respondent_and_interested_persons"
n_fields: 33
addendum_supported: true
addendum_target_fields:
  - "persons_to_notify"
  - "limit_contact_persons"
  - "respondent_property"
parties:
  - petitioner
  - respondent
  - respondent_attorney
  - attorney
  - notify_person (1..N, repeating)
slot_groups:
  - notify_person
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 6 | deterministic from petitioner + respondent + attorney records |
| narrative_derived | 18 | LLM (respondent info + notify_person slots + property narrative) |
| legal_choice | 4 | human — scope (full/limited), interpreter, emergency, etc. |
| signature | 3 | wet-ink |

## Procedural context

Petition to appoint a guardian for an adult who cannot make decisions
for themselves (e.g., due to dementia, severe disability). The
petitioner must establish jurisdiction, identify the respondent and
proposed guardian, and serve notice on interested persons. The form
includes optional sections for contact-restriction requests and
respondent property listings.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `respondent_age` hallucination | LLM picks a plausible age when narrative is silent | risk_tier flag; hand review |
| `notify_person_*` slot duplication | same person listed at slots 3 and 5 | `dedupe_within(notify_person_name)` (auto-emitted on Pattern A slots if names follow that convention) |
| `respondent_attorney` confusion | LLM fills petitioner's attorney info | category=party_attr with explicit party label |
| `respondent_property` over-detail | LLM enumerates property when narrative is general | hand review |
| `limit_contact_persons` mis-scope | LLM lists everyone narrative mentions vs only those to restrict | hand review |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `respondent_age` | 35 | confabulation risk |
| `respondent_attorney` | 35 | role-confusion risk |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county_probate_court, docket_no | drift |
| `data_type: number` | respondent_age | non-integer |
| `data_type: address` | persons_to_notify | malformed |
| `data_type: date` | petition_date | invalid date |

## Conditional writability

None encoded. The form's optional sections (contact restrictions,
property listing) are unconditionally writable but should be blank if
the narrative doesn't support them.

## Risk distribution

```
green:  ~22
yellow: ~9
orange: 2
red:    0
```
