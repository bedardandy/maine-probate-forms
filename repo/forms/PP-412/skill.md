---
form_id: PP-412
form_title: Conservators Report
form_revision: "8-6-21"
jurisdiction: Maine
court: Probate
filer_role: conservator
statutes:
  - "18-C M.R.S.A. § 5-418 (Conservator's reports)"
filing_deadline_days: 365
filing_deadline_anchor: "appointment_anniversary"
service_required: true
n_fields: 22
addendum_supported: true
parties:
  - attorney
  - conservator
  - individual_under_protection
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 13 | LLM over narrative + validators |
| party_attr | 6 | deterministic from party record |
| case_constant | 2 | deterministic from case_dict |
| signature | 1 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `individual_name` | orange | wrong 1/5; oc 1/5; miscompr 1/5 |

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
green    21
orange    1
```
