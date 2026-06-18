---
form_id: AD-011
form_title: Pet to Recognize Foreign Adoption
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S. § 9-312 (Foreign adoptions)"
filing_deadline_days: null
service_required: true
n_fields: 28
addendum_supported: true
parties:
  - adoptee
  - attorney
  - name_of_other
  - notary
  - petitioner
legal_choices:
  - change_of_name_requested
slot_groups:
  - petitioner
  - petitioner_signature
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| party_attr | 14 | deterministic from party record |
| narrative_derived | 10 | LLM over narrative + validators |
| case_constant | 2 | deterministic from case_dict |
| legal_choice | 1 | human decision required |
| signature | 1 | wet-ink; never auto-fill |

## Computed formulas

None.

## Repeating slot groups

| prefix | indices | suffixes |
|---|---|---|
| `petitioner` | 1..2 | signature |
| `petitioner_signature` | 1..2 | date |

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|

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
green    23
yellow    5
```
