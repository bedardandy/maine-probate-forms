---
form_id: DE-506
form_title: Petition for Elective Share
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: surviving_spouse_or_attorney
statutes:
  - "18-C M.R.S.A. § 2-202 (Elective share — amount)"
  - "18-C M.R.S.A. § 2-211 (Procedure for elective share)"
  - "18-C M.R.S.A. § 2-212 (Time for filing)"
filing_deadline_days: 270
filing_deadline_anchor: "decedent_death_date"
service_required: true
service_recipients: "personal_representative_and_interested_persons"
n_fields: 46
addendum_supported: true
addendum_target_fields:
  - "exceptions"
  - "elective_share_determined"
  - "transferees_*_property_*"
parties:
  - petitioner (surviving spouse)
  - decedent
  - transferees (1..6, persons who received non-probate transfers)
slot_groups:
  - transferees
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 4 | deterministic from case_dict |
| narrative_derived | 41 | LLM over elective-share computation + transferees |
| signature | 1 | wet-ink |

## Procedural context

The surviving spouse can elect to take a statutory share of the
"augmented estate" instead of their share under the will. The elective
share calculation pulls in non-probate transfers (joint accounts,
beneficiary designations, gifts within 2 years) which must be listed
as transferees. The deadline is 9 months after death or 6 months after
probate is opened, whichever is later: the encoded `270` days uses
the conservative single-deadline.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `transferees_*_N` slot duplication | same transferee listed at slots 3 and 5 | `dedupe_within(transferees_name)` (auto-emitted) |
| `augmented_estate_value` over-confidence | LLM picks a number when the calculation should be done by the court | category=legal_choice (kind of); risk_tier flag |
| `elective_share_determined` premature | LLM fills the elective-share amount before the court determines it | hand review; this field is typically filled post-order |
| `petitioner_entitled` over-claiming | LLM asserts entitlement when narrative is silent | legal_choice; never trust LLM |
| `exceptions` paraphrase loss | LLM abstracts specific objections | hand review |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `augmented_estate_value` | 35-65 | strategic legal_choice |
| `transferees_*` late slots | 35 | repeating-slot risk |
| `elective_share_determined` | 50 | court-determined, not petitioner-filled |
| `exceptions` | 35 | semantic, no automated guard |

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(transferees_name)` | transferees_name_1..6 | duplicate transferees |
| `dedupe_within(transferees_address)` | transferees_address_* | duplicate addresses |
| `nonempty_if_desc` | transferees_address_*, _property_* | orphan rows |
| `populate_from_case_dict` | docket, county, estate caption | drift |

## Conditional writability

None encoded. `elective_share_determined` should be `writable_when:
court_order_entered == true`, but this is a court-side field that
isn't filled at petition time anyway: flag as `external`/court-fill.

## Risk distribution

```
green:  ~22
yellow: ~20
orange: 4
red:    0
```
