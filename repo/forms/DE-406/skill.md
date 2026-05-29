---
form_id: DE-406
form_title: Probate Account
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: personal_representative_or_petitioner
statutes:
  - "18-C M.R.S.A. Article 3 (Decedents' Estates)"
filing_deadline_days: null
service_required: true
n_fields: 31
addendum_supported: true
parties:
  - attorney
  - personal_representative
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 18 | LLM over narrative + validators |
| party_attr | 6 | deterministic from party record |
| case_constant | 3 | deterministic from case_dict |
| computed | 2 | deterministic; recompute from formula |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

See `formulas.yaml` for JSON-DSL expressions interpreted by `validate_filled.py`.

## Known LLM failure modes (May-2026 eval)

_No eval evidence on file for this form. Run `scripts/run_fact_eval.sh <form_id> 5` to generate._

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
green    31
```
