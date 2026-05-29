---
form_id: PP-210
form_title: Registration of Guardianship or Conservatorship
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. Article 5 (Protective Proceedings)"
filing_deadline_days: null
service_required: true
n_fields: 15
addendum_supported: true
parties:
  - form_subject
legal_choices:
  - registration_type
  - order_attached_marker
  - petition_not_pending_marker
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 8 | LLM over narrative + validators |
| case_constant | 3 | deterministic from case_dict |
| legal_choice | 3 | human decision required |
| party_attr | 1 | deterministic from party record |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `order_attached_marker` | yellow | oc 1/5 |
| `notice_of_intent_date_filed` | yellow | miscompr 1/5 |

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
green    11
yellow    4
```
