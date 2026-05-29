---
form_id: PP-108
form_title: Acceptance of Appt by Conservator - Minor
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. Article 5 (Protective Proceedings)"
filing_deadline_days: null
service_required: true
n_fields: 7
addendum_supported: true
parties:
  - minor
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 3 | LLM over narrative + validators |
| case_constant | 2 | deterministic from case_dict |
| party_attr | 1 | deterministic from party record |
| signature | 1 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `conservator_signature` | orange | wrong 1/5; oc 2/5; miscompr 1/5 |

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
green     6
orange    1
```
