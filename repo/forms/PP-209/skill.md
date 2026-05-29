---
form_id: PP-209
form_title: Interim and Annual Report of Guardian
form_revision: "07-01-19"
jurisdiction: Maine
court: Probate
filer_role: guardian
statutes:
  - "18-C M.R.S.A. § 5-317 (Annual report of guardian)"
filing_deadline_days: 365
filing_deadline_anchor: "appointment_anniversary"
service_required: true
n_fields: 25
addendum_supported: true
parties:
  - individual_under_protection
legal_choices:
  - report_type
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 19 | LLM over narrative + validators |
| case_constant | 2 | deterministic from case_dict |
| signature | 2 | wet-ink; never auto-fill |
| party_attr | 1 | deterministic from party record |
| legal_choice | 1 | human decision required |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `current_mental_physical_social_condition` | green | oc 1/5 |

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
green    24
yellow    1
```
