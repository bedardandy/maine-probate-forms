---
form_id: DE-401
form_title: DE-401(A) Certificate of Value Resident and Non Resident (Rev. 7-1-19)
jurisdiction: Maine
court: Probate
filer_role: applicant
statutes:
  - "18-C M.R.S.A. § 3-401 (Adjudication of testacy)"
  - "18-C M.R.S.A. § 3-108 (Three-year statute of repose)"
  - "36 M.R.S.A. § 4063 (Maine estate tax — 9-month deadline)"
filing_deadline_days: 1095
filing_deadline_anchor: "decedent_death_date"
service_required: true
n_fields: 21
addendum_supported: true
parties:
  - decedent
legal_choices:
  - decedent_interest_maine_real_estate
  - federal_estate_tax_return
  - maine_estate_tax_return
  - estate_tax_action
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 9 | LLM over narrative + validators |
| legal_choice | 4 | human decision required |
| case_constant | 2 | deterministic from case_dict |
| computed | 2 | deterministic; recompute from formula |
| signature | 2 | wet-ink; never auto-fill |
| party_attr | 1 | deterministic from party record |
| external | 1 | left blank (filled by court/clerk) |

## Computed formulas

See `formulas.yaml` for JSON-DSL expressions interpreted by `validate_filled.py`.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `applicant_acknowledgment_name` | green | oc 1/5 |

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
green    17
yellow    4
```
