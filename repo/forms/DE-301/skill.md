---
form_id: DE-301
form_title: Application for Informal Appointment of Special Administrator
form_revision: "09-12-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 3-401 (Formal testacy proceedings)"
  - "18-C M.R.S.A. § 3-108 (Three-year statute of repose)"
filing_deadline_days: 1095
filing_deadline_anchor: "decedent_death_date"
service_required: true
n_fields: 29
addendum_supported: true
parties:
  - applicant
  - appointee
  - attorney
  - decedent
  - notary
legal_choices:
  - domiciled_in_county
  - will_presented_for_probate
  - will_details
  - nominated_as_pr_in_will
---

## Pipeline routing

| category | n | path |
|---|---|---|
| party_attr | 14 | deterministic from party record |
| narrative_derived | 5 | LLM over narrative + validators |
| legal_choice | 4 | human decision required |
| case_constant | 3 | deterministic from case_dict |
| signature | 3 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `county_probate_court` | green | oc 1/5 |
| `domiciled_in_county` | yellow | oc 1/5 |
| `will_presented_for_probate` | yellow | oc 1/5 |
| `notary_title` | green | miscompr 1/5 |

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
green    25
yellow    4
```
