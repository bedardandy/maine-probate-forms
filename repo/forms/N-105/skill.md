---
form_id: N-105
form_title: Demand for Notice
form_revision: "6-25-23"
jurisdiction: Maine
court: Probate
filer_role: personal_representative
statutes:
  - "18-C M.R.S.A. § 3-310 (Informal probate — notice)"
  - "18-C M.R.S.A. § 3-705 (Duty of PR)"
filing_deadline_days: 30
filing_deadline_anchor: "pr_appointment_date"
service_required: true
n_fields: 13
addendum_supported: true
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 8 | LLM over narrative + validators |
| case_constant | 3 | deterministic from case_dict |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `date_of_death_or_appointment` | green | oc 1/5 |

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
green    13
```
