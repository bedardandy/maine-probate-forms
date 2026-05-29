---
form_id: N-115
form_title: Notice re Appointment of PR to Heirs_ Devisees
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: interested_person
statutes:
  - "18-C M.R.S.A. § 3-204 (Demand for notice)"
filing_deadline_days: null
service_required: true
n_fields: 17
addendum_supported: true
parties:
  - attorney
  - personal_representative
legal_choices:
  - bond_type
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 6 | LLM over narrative + validators |
| case_constant | 5 | deterministic from case_dict |
| party_attr | 3 | deterministic from party record |
| signature | 2 | wet-ink; never auto-fill |
| legal_choice | 1 | human decision required |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `estate_name` | green | oc 1/5 |
| `notice_recipient` | yellow | miscompr 1/5 |
| `pr_address` | green | oc 1/5 |

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
green    15
yellow    2
```
