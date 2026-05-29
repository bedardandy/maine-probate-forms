---
form_id: APP-1
form_title: Notice of Appeal to Law Court
form_revision: "9-12-19"
jurisdiction: Maine
court: Probate
filer_role: appellant
statutes:
  - "18-C M.R.S.A. § 1-308 (Appeals)"
  - "M.R. Prob. P. 73"
filing_deadline_days: 30
filing_deadline_anchor: "judgment_entry_date"
service_required: true
n_fields: 15
addendum_supported: true
parties:
  - attorney
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 6 | LLM over narrative + validators |
| party_attr | 5 | deterministic from party record |
| case_constant | 3 | deterministic from case_dict |
| signature | 1 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `appellant_name` | green | oc 1/5 |

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
green    15
```
