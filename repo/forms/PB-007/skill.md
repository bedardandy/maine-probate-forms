---
form_id: PB-007
form_title: Order Appointing Guardian Ad Litem (Probate)
jurisdiction: Maine
court: Probate
filer_role: court
statutes:
  - "18-C M.R.S.A. § 1-115 (Court appointment of GAL)"
  - "19-A M.R.S.A. § 1507 (GAL in family/probate proceedings)"
filing_deadline_days: null
service_required: true
service_recipients: "parties_and_appointed_gal"
n_fields: 87
addendum_supported: true
addendum_target_fields:
  - "good_cause_findings"
  - "other_qualifications_detail"
  - "appointment_factors"
  - "fee_factors"
  - "*_other_provisions"
  - "expanded_*"
parties:
  - gal
  - minor_children
  - objector
  - other_objector
  - contact_person
  - other_payor
section_choices:
  - appointment_level: limited | standard | expanded
  - fee_structure: hourly_cap | flat_fee | hourly_rate
  - payment_method: lump_sum | percent | periodic | other
section_headers_exclusive: false
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 12 | deterministic from GAL + minor + objector records |
| legal_choice | 4 | human — appointment_level, fee_structure, payment_method, billing_frequency |
| narrative_derived | 64 | LLM over court-finding narrative + duty enumeration |
| signature | 5 | wet-ink (judge order + service certifications) |

## Procedural context

This is a court-issued order: filed by the court, not a litigant.
The order grants three tiers of authority to the appointed GAL:

- **limited**: narrow set of duties; hearing-only appearance
- **standard**: typical GAL duties (interview parties, file report)
- **expanded**: wider set (interview teachers, engage providers,
  arrange counseling, subpoena records, etc.)

Payment structure is independent: the court can elect lump-sum
payments, percentages split between parties, periodic payments, or
hourly billing with caps.

## Known LLM failure modes (May-2026 eval)

The biggest validator-actionable risk is filling fields under
appointment levels or payment methods that weren't elected.

| symptom | example | guard |
|---|---|---|
| out-of-level fields filled | `expanded_arrange_counseling` populated when `appointment_level == "limited"` | `writable_when: {all_of: [{field: appointment_level, equals: "expanded"}]}` (encoded for all `limited_*`, `standard_*`, `expanded_*` fields) |
| out-of-method fields filled | `petitioner_lump_sum` set when `payment_method != "lump_sum"` | `writable_when: {all_of: [{field: payment_method, equals: "lump_sum"}]}` (encoded) |
| fee structure overlap | LLM fills hourly + flat fee simultaneously | category=legal_choice; never trust LLM |
| good_cause_findings paraphrase loss | LLM abstracts specific cited reasons into "abuse and neglect" generic | hand review (semantic) |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `appointment_level` | 35-65 | strategic choice gating ~30 narrative fields |
| `payment_method` | 35-65 | strategic choice gating ~10 fee-detail fields |
| Most narrative duty fields | varies | conditional on appointment_level |

## Validators

| validator | applies to | catches |
|---|---|---|
| `writable_when` enforcement | ~25 limited_/standard_/expanded_* fields | filling wrong-level fields |
| `writable_when` enforcement | ~5 lump_sum/percent/periodic fields | filling wrong-method fields |
| `populate_from_case_dict` | docket_no, county_probate, case_title | drift |
| `data_type: currency` | fee, lump_sum, hourly_rate fields | non-numeric text |
| `data_type: date` | deadline / order_date fields | invalid date |
| `data_type: number` | hours, days, percent | non-integer text |

## Conditional writability

Heavily encoded in `classifications.yaml`:

- `appointment_level == "limited"` → `limited_*` writable
- `appointment_level == "standard"` → `standard_*` writable
- `appointment_level == "expanded"` → `expanded_*` writable (12 fields gated)
- `payment_method == "lump_sum"` → `petitioner_lump_sum`, `respondent_lump_sum`, `lump_sum_deadline`, `other_lump_sum_name` writable

See `repo/forms/PB-007/classifications.yaml` for the full encoding.

## Risk distribution

```
green:  ~30
yellow: ~50
orange: 3
red:    0
```
