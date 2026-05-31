---
form_id: N-108
form_title: Waiver of Notice on Behalf of Minor or Individual Subject to G-C
form_revision: "6-25-23"
jurisdiction: Maine
court: Probate
filer_role: personal_representative
statutes:
  - "18-C M.R.S.A. § 3-1001 (Notice of final account)"
filing_deadline_days: null
filing_deadline_anchor: "final_account_filing_date"
service_required: true
n_fields: 12
addendum_supported: true
parties:
  - petitioner
  - ward
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 5 | LLM over narrative + validators |
| case_constant | 3 | deterministic from case_dict |
| party_attr | 3 | deterministic from party record |
| signature | 1 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `date` | orange | wrong 1/5; miscompr 1/5 |
| `fiduciary_role` | yellow | oc 2/5; miscompr 1/5 |
| `ward_status` | yellow | oc 3/5 |

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
green     9
yellow    2
orange    1
```
