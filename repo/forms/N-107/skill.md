---
form_id: N-107
form_title: Waiver of Notice
form_revision: "03-01-25"
jurisdiction: Maine
court: Probate
filer_role: declarant
statutes:
  - "18-C M.R.S.A. § 1-401 (Waiver of notice)"
filing_deadline_days: null
service_required: false
n_fields: 9
addendum_supported: true
legal_choices:
  - waiver_type
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 6 | LLM over narrative + validators |
| case_constant | 2 | deterministic from case_dict |
| legal_choice | 1 | human decision required |

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
green     8
yellow    1
```
