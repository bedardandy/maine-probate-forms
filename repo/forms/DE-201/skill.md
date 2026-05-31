---
form_id: DE-201
form_title: Application for Informal Probate of Will and/or Appointment of PR
form_revision: "09-12-19"
jurisdiction: Maine
court: Probate
filer_role: applicant
statutes:
  - "18-C M.R.S.A. § 3-301 (Informal probate of will)"
  - "18-C M.R.S.A. § 3-308 (Informal appointment of PR)"
  - "18-C M.R.S.A. § 3-108 (Three-year statute of repose)"
filing_deadline_days: 1095
filing_deadline_anchor: "decedent_death_date"
service_required: true
service_recipients: "interested_persons"
n_fields: 96
addendum_supported: true
addendum_target_fields:
  - "applicant_interest_other_explain"
  - "decedent_domicile"
  - "heirs_page1_addr_*"
  - "heirs_page2_addr_*"
  - "pr_name_address"
parties:
  - applicant
  - decedent
  - personal_representative
  - heir (1..3 on page 1, 1..6 on page 2 — total slots: 9)
slot_groups:
  - heirs_page1
  - heirs_page2
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 6 | deterministic from applicant + PR + decedent records |
| narrative_derived | 81 | LLM over heir list + applicant narrative + legal-choice yes/nos |
| legal_choice | 4 | human — bond requirement, demand-for-notice, etc. |
| signature | 2 | wet-ink (applicant + notary jurat) |

## Procedural context

The most common form for opening a Maine probate estate. Used for both
informal probate (with will) and informal appointment (intestate). The
heirs section spans 2 pages with different slot structures:

- **Page 1:** 3 heir rows (`heirs_page1_name_1..3`, `_addr_1..3`,
  `_dob_1..3`, `_rel_1..3`)
- **Page 2:** 6 heir rows (`heirs_page2_*_1..6`)

The form's repeating-slot table is split across pages so dedupe must
operate **across both prefixes**: same heir should not appear on both
pages.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| cross-page heir duplication | same heir listed at `heirs_page1_name_2` and `heirs_page2_name_1` | `cross_section_dedupe(heirs_page1_name, heirs_page2_name)` |
| `applicant_interest` paraphrase loss | LLM abstracts the specific basis ("nominated as PR in will") into generic ("interested party") | hand review (semantic) |
| `applicant_interest_other_explain` filled when interest is standard | LLM uses the "other" explanation slot when one of the standard interest categories applies | conditional writability (TODO) |
| bond_requirement / demand_for_notice over-confidence | LLM picks a value when narrative is silent | category=legal_choice; never trust LLM |
| `non_registered_partner` confusion | "domestic partner" claim when narrative says spouse | hand review |
| `pr_name_address` composite confusion | LLM packs name + address + phone into the address field | data_type composite text (handled) |

## High-risk fields (yellow tier: 68 fields)

The heir rows are the highest concentration of yellow-tier risk
(slot duplication potential × 9 slots × 4 attributes = 36 yellow fields).

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(heirs_page1_name)` | page-1 heir names | duplicate heirs on page 1 |
| `dedupe_within(heirs_page2_name)` | page-2 heir names | duplicate heirs on page 2 |
| `cross_section_dedupe(heirs_page1_name)` | page-2 → page-1 names | same heir across pages (most common error) |
| `dedupe_within(heirs_page1_addr)`, `_page2_addr` | per-page addresses | duplicate addresses |
| `nonempty_if_desc` | heirs_*_addr, _dob, _rel | orphan rows |
| `populate_from_case_dict` | docket_no, county_probate_court, estate_of_decedent | drift |

## Conditional writability

```yaml
# Encode in classifications.yaml when verified against PDF:
applicant_interest_other_explain:
  writable_when:
    all_of:
      - field: applicant_interest
        equals: "other"
register_serve_notices:
  writable_when:
    all_of:
      - field: demand_for_notice
        equals: true
publish_notice_creditors:
  writable_when:
    all_of:
      - field: demand_for_notice
        equals: true
```

## Risk distribution

```
green:  ~27
yellow: 68
orange: 1
red:    0
```
