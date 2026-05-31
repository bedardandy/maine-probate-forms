---
form_id: DE-403
form_title: Bond for Personal Representative
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: personal_representative
statutes:
  - "18-C M.R.S.A. § 3-604 (Personal representative's bond)"
  - "18-C M.R.S.A. § 3-606 (Demand for bond)"
filing_deadline_days: null
filing_deadline_anchor: "letters_issuance_date"
service_required: false
n_fields: 69
addendum_supported: true
addendum_target_fields:
  - "real_property_surety_*_description_*"
parties:
  - personal_representative
  - decedent
  - surety_1 (with property pledge)
  - surety_2 (with property pledge)
  - corporate_surety (alternative path)
  - witness
slot_groups:
  - real_property_surety_1
  - real_property_surety_2
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 16 | deterministic from PR + 2 sureties + corporate surety + 2 witness records |
| narrative_derived | 39 | LLM (pledged real property descriptions, registry citations) |
| signature | 11 | wet-ink (PR + each surety + each witness + each notary jurat) |

## Procedural context

Maine's PR bond form. Three supported paths:

1. **Personal sureties (1 or 2)** with up to 2 pledged real properties each.
2. **Corporate surety** (a bonding company): alternative to personal sureties.
3. **Combination**: one personal surety + one corporate surety.

Each personal surety's pledged real estate is itemized by:
- description
- registry county
- book + page citation
- physical address

Repeated up to 2 times per surety (so up to 4 properties total).

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `*_gross_value` arithmetic | LLM writes a free-form total; no summable per-item value fields exist on this form to validate against | flagged as narrative_derived in classifications.yaml |
| registry book/page swap | LLM writes the page number in the book field or vice versa | hand review; consider per-form book/page validator |
| surety 1 vs surety 2 mixup | LLM uses surety_2's name in the surety_1 affidavit jurat | category=party_attr with explicit surety_N label |
| corporate vs personal surety conflict | LLM populates both blocks when only one path applies | conditional writability (TODO) |
| `penal_sum_numeric` vs `_words` mismatch | "$50,000" vs "Forty-five thousand" | hand review (semantic) |
| `witness_for_co_personal_rep` filled when only one PR | LLM duplicates witness data for a non-existent co-PR | hand review |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `personal_property_gross_value` | 50 | free-form total without summable deps |
| `real_property_gross_value` | 50 | same |

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(real_property_surety_1_description)` | per-surety property descriptions | duplicate pledges |
| `dedupe_within(real_property_surety_2_description)` | per-surety property descriptions | duplicate pledges |
| `populate_from_case_dict` | docket_no, county_probate_court | drift |
| `data_type: currency` | penal_sum_numeric, *_gross_value | non-numeric strings |
| `data_type: date` | bond_date, bond_approval_date | invalid date |

## Conditional writability (TODO)

```yaml
# Encode once verified against the PDF:
real_property_surety_1_*:
  writable_when:
    none_of:
      - field: corporate_surety_name
        exists: true
corporate_surety_*:
  writable_when:
    all_of:
      - field: name_of_surety_1
        exists: false
```

## Risk distribution

```
green:  ~45
yellow: ~22
orange: 2
red:    0
```
