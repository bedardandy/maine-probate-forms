---
form_id: PP-408
form_title: Claim Against Estate
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: conservator
statutes:
  - "18-C M.R.S.A. § 5-428 (Conservatorship claims)"
filing_deadline_days: 60
filing_deadline_anchor: "claim_filing_date"
service_required: true
n_fields: 25
addendum_supported: true
parties:
  - attorney_for_claimant
  - attorney_for_conservator
  - claimant
  - individual_under_protection
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| party_attr | 13 | deterministic from party record |
| narrative_derived | 8 | LLM over narrative + validators |
| case_constant | 2 | deterministic from case_dict |
| signature | 2 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `claimant_address` | green | oc 1/5 |

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
green    25
```
