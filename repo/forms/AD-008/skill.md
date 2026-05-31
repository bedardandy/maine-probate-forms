---
form_id: AD-008
form_title: Report of Disbursements (Adoption)
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-A M.R.S.A. § 9-303 (Adoption — disclosure of expenses)"
filing_deadline_days: null
filing_deadline_anchor: "adoption_finalization_date"
service_required: false
n_fields: 26
addendum_supported: true
addendum_target_fields:
  - "certification_text"
  - "disbursements"
  - "other_disbursements"
parties:
  - petitioner
  - adoptee
  - notary
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 4 | deterministic from petitioner + notary records |
| narrative_derived | 16 | LLM over disbursement narrative |
| signature | 3 | wet-ink (petitioner + notary jurat) |

## Procedural context

Maine requires all adoption-related disbursements to be disclosed —
agency fees, legal fees, medical/hospital costs paid for the birth
parent, travel, gifts. The form is sworn before a notary and filed
with the adoption petition (no separate statutory deadline; tied to
adoption finalization).

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `disbursements` summary instead of itemization | LLM writes "agency and legal fees" instead of itemized amounts | hand review (semantic) |
| `other_disbursements` over-disclosure | LLM includes routine expenses (food, lodging) that aren't reportable | hand review |
| `certification_text` boilerplate substitution | LLM substitutes its own jurat language | hand review; this is a court-published form text |
| notary block confusion | `notary_county` filled with petitioner's county instead of jurat county | category=party_attr with notary party label |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `disbursements` | 35 | free-form narrative; itemization quality varies |
| `other_disbursements` | 35 | over-disclosure risk |
| `certification_text` | 35 | boilerplate-substitution risk |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | probate_docket_no, district_docket_number | drift |
| `data_type: date` | notary_date | invalid date |

## Conditional writability

None: the entire form is unconditionally writable.

## Risk distribution

```
green:  ~15
yellow: ~8
orange: 3
red:    0
```
