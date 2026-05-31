---
form_id: APP-2
form_title: Transcript Order (Probate Appeal)
form_revision: "6-25-20"
jurisdiction: Maine
court: Probate
filer_role: appellant
statutes:
  - "18-C M.R.S.A. § 1-308 (Appeals)"
  - "M.R. Prob. P. 73"
filing_deadline_days: 30
filing_deadline_anchor: "judgment_entry_date"
service_required: true
service_recipients: "opposing_parties_and_court_reporter"
n_fields: 26
addendum_supported: true
addendum_target_fields:
  - "reason_for_transcript"
  - "*_specification"
parties:
  - appellant
  - hearing (1..4, repeating hearings to transcribe)
slot_groups:
  - hearing
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict (county, docket, case_name) |
| narrative_derived | 21 | LLM over appeal narrative + hearing-list dedupe |
| legal_choice | 1 | human — `payment_method` |
| signature | 1 | wet-ink |

## Known LLM failure modes (May-2026 eval)

The form has 4 `hearing_<N>_(date|proceeding)` rows. Narrative usually
mentions 1-2 hearings to transcribe; LLM padding to 4 slots produces
duplicate entries.

| symptom | example | guard |
|---|---|---|
| late-slot duplication | `hearing_4_date` ↔ `hearing_2_date` repeated | `dedupe_within(hearing_date)` |
| date format drift | `hearing_2_date = "March 15, 2026"` then `hearing_3_date = "3/15/26"` | `data_type: date` accepts both but normalization helps |
| proceeding-name vagueness | LLM writes "hearing" when narrative says "guardianship review" | risk_tier red; flag for human |
| `payment_method` over-confidence | LLM picks "credit card" when narrative is silent | category=legal_choice; never trust LLM |

## High-risk fields (red tier)

| field | score | reasons |
|---|---|---|
| `hearing_4_date` | 76 | wrong 2/5 |
| `hearing_4_proceeding` | 76 | wrong 2/5 |

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(hearing_date)` | hearing_*_date | duplicate hearing dates |
| `dedupe_within(hearing_proceeding)` | hearing_*_proceeding | duplicate proceeding names |
| `nonempty_if_desc` | hearing_*_proceeding | orphan date with no proceeding name |
| `populate_from_case_dict` | docket_no, county_probate_court, case_name | drift |
| `data_type: date` | hearing_*_date | invalid date format |

## Conditional writability

`payment_method == "deposit"` should unlock a deposit-amount cell
if the form has one; not yet encoded.

## Risk distribution

```
green:  ~10
yellow: ~12
orange: 2
red:    2
```
