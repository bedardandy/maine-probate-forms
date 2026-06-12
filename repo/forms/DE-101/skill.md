---
form_id: DE-101
form_title: Petition for Formal Adjudication of Intestacy and Appointment of PR (or Adjudication Only)
form_revision: "07-01-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 3-401 (Formal testacy proceedings; nature)"
  - "18-C M.R.S.A. § 3-402 (Petition contents)"
  - "18-C M.R.S.A. § 3-403 (Notice of hearing)"
  - "18-C M.R.S.A. § 3-414 (Formal proceedings concerning appointment of PR)"
  - "18-C M.R.S.A. § 3-108 (Three-year statute of repose)"
filing_deadline_days: null
service_required: true
n_fields: 115
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
  - demand_for_notice
  - demand_for_notice_details
slot_groups:
  - died_more_than
  - heirs
  - notice_other
  - renunciation
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 88 | LLM over narrative + validators |
| party_attr | 10 | deterministic from party record |
| other | 7 | TRIAGE — unclassified |
| legal_choice | 5 | human decision required |
| case_constant | 3 | deterministic from case_dict |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

None.

## Repeating slot groups

| prefix | indices | suffixes |
|---|---|---|
| `died_more_than` | 3..3 | years |
| `heirs` | 1..12 | addr, dob, name, rel |
| `notice_other` | 1..4 | addr, name |
| `renunciation` | 1..8 | signature |

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `decedent_full_name` | green | miscompr 1/5 |
| `decedent_domicile` | green | oc 1/5 |

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
green    38
yellow   77
```
