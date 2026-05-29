---
form_id: PP-405
form_title: Bond for Conservator
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: conservator
statutes:
  - "18-C M.R.S.A. § 5-415 (Bond by conservator)"
  - "18-C M.R.S.A. § 5-416 (Terms and requirements of bond)"
filing_deadline_days: null
filing_deadline_anchor: "appointment_date"
service_required: true
n_fields: 71
addendum_supported: true
addendum_target_fields:
  - "personal_property_value_detail"
  - "real_property_value_detail"
  - "desc_real_property_*"
parties:
  - conservator
  - individual_under_protection
  - surety (1..2, two personal sureties)
  - corporate_surety
  - notary
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 32 | deterministic from conservator + 2 surety + corporate-surety + notary records |
| narrative_derived | 28 | LLM (property descriptions, registry citations, value detail) |
| signature | 9 | wet-ink (conservator, both sureties, both witnesses, both notaries) |

## Procedural context

A conservator must post a bond before letters are issued. PP-405
supports up to two **personal sureties** (with property pledged as
collateral) OR a **corporate surety**. Each personal surety's pledge
is broken out into:

- Up to two **personal property** items (`desc_personal_property_1..2`)
- Up to four **real property** items (`desc_real_property_1..4`) with
  county/registry/book/page citations and physical addresses

The form has two **affidavit blocks** at the end where each personal
surety swears to the pledged property values; corporate sureties skip
these.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `gross_value_*` arithmetic | LLM writes a free-form total that doesn't match individual property values | NOT a computed cell on this form — the value_detail is narrative; flag for hand review |
| registry-page/book confusion | LLM swaps `book_page_1_book` and `book_page_1_page` | hand review; consider per-county format validator |
| surety 1 vs surety 2 mixup | LLM uses surety_2's name in surety_1's affidavit block | category=party_attr with explicit surety_N label |
| corporate vs personal surety conflict | LLM populates BOTH corporate and personal surety blocks | conditional writability — only one path applies (TODO: encode) |
| `penal_sum_numeric` vs `_words` mismatch | "$50,000" vs "Fifty thousand and 00/100" | hand review (semantic) |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `corporate_surety_*` (3 fields) | 50 | conditional on corporate-surety path |
| `personal_property_value_detail` | 50 | free-form total without summable deps |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | docket_no, county_probate_court | drift |
| `data_type: currency` | gross_value_*, penal_sum_numeric | non-numeric text |
| `data_type: address` | surety addresses, corporate_surety_address | malformed |
| `data_type: phone` | surety phone fields | invalid |

## Conditional writability (TODO)

```yaml
# Encode in classifications.yaml:
# If corporate_surety_name is populated, all personal-surety fields
# should be blank, and vice versa.
corporate_surety_name:
  required_when:
    none_of:
      - field: name_of_surety_1
        exists: true
desc_personal_property_*:
  writable_when:
    none_of:
      - field: corporate_surety_name
        exists: true
```

## Risk distribution

```
green:  ~50
yellow: ~17
orange: 4
red:    0
```
