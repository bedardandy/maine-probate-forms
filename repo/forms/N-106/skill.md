---
form_id: N-106
form_title: Notice of Removal
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: personal_representative
statutes:
  - "18-C M.R.S.A. § 3-801 (Notice to creditors)"
filing_deadline_days: 30
filing_deadline_anchor: "pr_appointment_date"
service_required: true
n_fields: 12
addendum_supported: true
parties:
  - name_of_other
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 8 | LLM over narrative + validators |
| case_constant | 3 | deterministic from case_dict |
| party_attr | 1 | deterministic from party record |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `forwarded_date` | yellow | oc 1/5; miscompr 1/5 |

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
green    11
yellow    1
```
