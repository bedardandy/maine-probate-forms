---
form_id: DE-104
form_title: PR Acceptance
form_revision: "07-01-19"
jurisdiction: Maine
court: Probate
filer_role: personal_representative_or_petitioner
statutes:
  - "18-C M.R.S.A. Article 3 (Decedents' Estates)"
filing_deadline_days: null
service_required: true
n_fields: 7
addendum_supported: true
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| narrative_derived | 2 | LLM over narrative + validators |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

None.

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
green     7
```
