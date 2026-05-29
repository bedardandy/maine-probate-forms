---
form_id: PP-401
form_title: Petition for Appointment of Conservator
form_revision: "07-01-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. Article 5 (Protective Proceedings)"
filing_deadline_days: null
service_required: true
n_fields: 35
addendum_supported: true
parties:
  - attorney
  - respondent
legal_choices:
  - petition_type
  - conservatorship_type
  - interpreter_required
  - nominee_bankruptcy
  - nominee_conviction
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 18 | LLM over narrative + validators |
| party_attr | 6 | deterministic from party record |
| legal_choice | 5 | human decision required |
| signature | 4 | wet-ink; never auto-fill |
| case_constant | 2 | deterministic from case_dict |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `persons_to_notify` | green | oc 1/5 |
| `nominee_conviction` | yellow | oc 1/5 |

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
green    30
yellow    5
```
