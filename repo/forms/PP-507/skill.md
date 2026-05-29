---
form_id: PP-507
form_title: Affidavit for Emergency Guardian and/or Conservator
form_revision: "07-01-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 5-312 (Emergency guardian)"
  - "18-C M.R.S.A. § 5-403 (Emergency conservator)"
filing_deadline_days: null
service_required: true
service_recipients: "respondent_and_interested_persons"
n_fields: 99
addendum_supported: true
addendum_target_fields:
  - "circumstances_of_harm"
  - "requested_powers"
  - "notice_address_*"
parties:
  - petitioner
  - respondent
  - notary
  - notice (1..13, repeating notice recipients)
slot_groups:
  - notice
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 8 | deterministic from petitioner + notary + respondent records |
| narrative_derived | 87 | LLM over emergency-circumstance narrative + dedupe validators |
| legal_choice | 2 | human — emergency type election |

## Known LLM failure modes (May-2026 eval)

PP-507's `notice_*_<N>` table has 13 slots: by far the longest in the
repo. Narrative typically supplies 4-7 notice recipients; Qwen padded
past that point produces duplicate entries.

| symptom | example | guard |
|---|---|---|
| late-slot duplication | `notice_name_8` ↔ `notice_name_13` repeat earlier names | `dedupe_within(notice_name)` |
| relationship inversion | "daughter" vs "granddaughter" swap when narrative is ambiguous | risk_tier red; flag for human |
| `circumstances_of_harm` paraphrase drift | LLM summarizes instead of quoting narrative facts | hand review (semantic, no automated guard) |
| `requested_powers` over-broad selection | LLM picks all-powers when narrative supports limited | category=legal_choice; never trust LLM |
| affidavit jurat under-confidence | `notary_*` block left blank | jurat is wet-ink by definition |

## High-risk fields (red tier)

| field | score | reasons |
|---|---|---|
| `notice_name_13` | 72 | wrong 1/5; slot duplication |
| `notice_address_13` | 72 | wrong 1/5 |
| `notice_relationship_13` | 72 | wrong 1/5 |

(Lower red count than DE-405/PP-205 because the v2 repeating-slot
prompt fix landed well on this form during the pilot.)

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(notice_name)` | notice_name_1..13 | duplicate names across slots |
| `dedupe_within(notice_address)` | notice_address_1..13 | duplicate addresses |
| `nonempty_if_desc` | notice_address_*, notice_relationship_* | orphan rows |
| `populate_from_case_dict` | docket_no, county | drift |

## Conditional writability

`emergency_guardian_requested` / `emergency_conservator_requested` are
the two top-level affirmation checkboxes. The `requested_powers` and
`circumstances_of_harm` narratives are required when either is true.

```yaml
requested_powers:
  required_when:
    any_of:
      - field: emergency_guardian_requested
        equals: true
      - field: emergency_conservator_requested
        equals: true
```

(Not yet encoded: TODO in classifications.yaml.)

## Risk distribution

```
green:  ~50
yellow: ~38
orange: ~8
red:    3
```
