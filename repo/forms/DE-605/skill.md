---
form_id: DE-605
form_title: Verified Application for Certificate of Discharge
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: claimant_or_pr
statutes:
  - "18-C M.R.S.A. § 3-1201 (Collection of personal property by affidavit)"
filing_deadline_days: null
filing_deadline_anchor: "decedent_death_date"
service_required: false
n_fields: 20
addendum_supported: true
parties:
  - applicant
  - notary
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 8 | LLM over narrative + validators |
| party_attr | 5 | deterministic from party record |
| signature | 4 | wet-ink; never auto-fill |
| case_constant | 3 | deterministic from case_dict |

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
green    20
```
