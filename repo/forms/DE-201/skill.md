---
form_id: DE-201
form_title: Petition for Formal Probate of Will or Appointment of Personal Representative or Both
form_revision: "8-6-21"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 3-401 (Formal testacy proceedings; nature)"
  - "18-C M.R.S.A. § 3-402 (Petition contents)"
  - "18-C M.R.S.A. § 3-407 (Burdens in contested cases)"
  - "18-C M.R.S.A. § 3-414 (Formal proceedings concerning appointment of PR)"
  - "18-C M.R.S.A. § 3-108 (Three-year statute of repose)"
filing_deadline_days: null
service_required: true
n_fields: 100
addendum_supported: true
parties:
  - attorney
  - decedent
  - form_subject
  - petitioner
legal_choices:
  - petition_type
  - real_estate_in_maine
  - domiciled_outside_maine
  - will_probated_informally
  - demand_for_notice
  - demand_for_notice_details
  - will_date
slot_groups:
  - devisees
  - died_more_than
  - heirs_page1
  - heirs_page2
  - notice_other
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 69 | LLM over narrative + validators |
| other | 10 | TRIAGE — unclassified |
| party_attr | 9 | deterministic from party record |
| legal_choice | 7 | human decision required |
| case_constant | 3 | deterministic from case_dict |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

None.

## Repeating slot groups

| prefix | indices | suffixes |
|---|---|---|
| `devisees` | 1..7 | addr, name |
| `died_more_than` | 3..3 | years |
| `heirs_page1` | 1..3 | addr, dob, name, rel |
| `heirs_page2` | 1..4 | addr, dob, name, rel |
| `notice_other` | 1..4 | addr, name |

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `heirs_page1_dob_3` | orange | wrong 1/5; oc 1/5 |
| `heirs_page2_addr_4` | yellow | oc 1/5 |

For each, the validator-level guard is encoded in `schema.json` `fields[].validators[]`.

## Validators

Declarative tags emitted in `schema.json` `fields[].validators[]`;
interpreted by `scripts/validate_filled.py`.

- `dedupe_within(<group>_<role>)` — rejects duplicates within a slot group
- `cross_section_dedupe(...)` — rejects desc appearing across sections
- `nonempty_if_desc` — value/encumbrance must be empty when desc is empty
- `recompute_from_dependencies` — computed cells equal formula
- `populate_from_case_dict` — case_constant cells equal case_dict[field]

## Risk distribution

```
green    32
yellow   67
orange    1
```
