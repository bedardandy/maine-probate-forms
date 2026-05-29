---
form_id: DE-407
form_title: Renunciation-Nomination
form_revision: "03-01-25"
jurisdiction: Maine
court: Probate
filer_role: renouncing_person
statutes:
  - "18-C M.R.S.A. § 2-1102 (Disclaimer of property interest)"
  - "12 M.R.S.A. § 2-902"
filing_deadline_days: 270
filing_deadline_anchor: "decedent_death_date"
service_required: true
n_fields: 11
addendum_supported: true
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 6 | LLM over narrative + validators |
| case_constant | 3 | deterministic from case_dict |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `renunciation_nomination_actions` | yellow | wrong 1/5 |
| `declarant_name` | green | oc 1/5 |
| `address` | green | oc 1/5 |

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
green    10
yellow    1
```
