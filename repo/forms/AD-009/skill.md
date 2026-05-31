---
form_id: AD-009
form_title: Certificate of Counseling
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-A M.R.S.A. §§ 9-301 to 9-315 (Adoption)"
filing_deadline_days: null
service_required: true
n_fields: 19
addendum_supported: true
parties:
  - adoptee
  - agency
  - counselee
  - counselor
  - notary
  - refusal_parent
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| party_attr | 9 | deterministic from party record |
| signature | 4 | wet-ink; never auto-fill |
| case_constant | 3 | deterministic from case_dict |
| narrative_derived | 3 | LLM over narrative + validators |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `counselee_name` | green | oc 1/5 |
| `date_signed` | yellow | oc 1/5; miscompr 1/5 |

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
green    18
yellow    1
```
