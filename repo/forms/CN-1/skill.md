---
form_id: CN-1
form_title: Name Change Petition
form_revision: "11-03-24"
jurisdiction: Maine
court: Probate
filer_role: consentor
statutes: []
filing_deadline_days: null
service_required: false
n_fields: 31
addendum_supported: true
parties:
  - attorney
  - notary
  - petitioner
legal_choices:
  - request_confidential_order
  - request_no_notification
---

## Pipeline routing

| category | n | path |
|---|---|---|
| party_attr | 15 | deterministic from party record |
| narrative_derived | 9 | LLM over narrative + validators |
| signature | 3 | wet-ink; never auto-fill |
| case_constant | 2 | deterministic from case_dict |
| legal_choice | 2 | human decision required |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `petitioner_prior_names` | yellow | oc 1/5; miscompr 1/5 |
| `notary_petitioner_name` | green | oc 1/5 |

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
green    28
yellow    3
```
