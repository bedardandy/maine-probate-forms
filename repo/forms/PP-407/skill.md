---
form_id: PP-407
form_title: Conservator Account
form_revision: "09-12-2019"
jurisdiction: Maine
court: Probate
filer_role: conservator
statutes:
  - "18-C M.R.S.A. § 5-419 (Conservator's accountings)"
filing_deadline_days: 365
filing_deadline_anchor: "appointment_anniversary"
service_required: true
n_fields: 30
addendum_supported: true
parties:
  - attorney
legal_choices:
  - accounting_type
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 18 | LLM over narrative + validators |
| party_attr | 5 | deterministic from party record |
| case_constant | 2 | deterministic from case_dict |
| computed | 2 | deterministic; recompute from formula |
| signature | 2 | wet-ink; never auto-fill |
| legal_choice | 1 | human decision required |

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
green    29
yellow    1
```
