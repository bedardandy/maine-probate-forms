---
form_id: AF-102
form_title: Small Estate Affidavit for Collection of Personal Property
form_revision: "4-8-20"
jurisdiction: Maine
court: Probate
filer_role: affiant
statutes: []
filing_deadline_days: null
service_required: false
n_fields: 12
addendum_supported: true
parties:
  - affiant
  - notary
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| party_attr | 4 | deterministic from party record |
| narrative_derived | 4 | LLM over narrative + validators |
| signature | 3 | wet-ink; never auto-fill |
| case_constant | 1 | deterministic from case_dict |

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
green    12
```
