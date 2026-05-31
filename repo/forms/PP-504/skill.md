---
form_id: PP-504
form_title: Joined Plan
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: guardian_or_conservator
statutes:
  - "18-C M.R.S.A. § 5-314 (Guardian's plan)"
  - "18-C M.R.S.A. § 5-419 (Conservator's plan)"
filing_deadline_days: 60
filing_deadline_anchor: "circumstance_change_date"
service_required: true
n_fields: 21
addendum_supported: true
parties:
  - respondent
legal_choices:
  - plan_version
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 13 | LLM over narrative + validators |
| signature | 4 | wet-ink; never auto-fill |
| case_constant | 2 | deterministic from case_dict |
| party_attr | 1 | deterministic from party record |
| legal_choice | 1 | human decision required |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `judge_signature` | yellow | oc 1/5; miscompr 1/5 |

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
green    19
yellow    2
```
