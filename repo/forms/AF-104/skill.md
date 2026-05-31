---
form_id: AF-104
form_title: Affidavit of Diligent Search
form_revision: "8-6-21"
jurisdiction: Maine
court: Probate
filer_role: affiant
statutes: []
filing_deadline_days: null
service_required: false
n_fields: 27
addendum_supported: true
parties:
  - affiant
  - interested_party
  - notary
  - person_being_sought
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 11 | LLM over narrative + validators |
| party_attr | 10 | deterministic from party record |
| case_constant | 3 | deterministic from case_dict |
| signature | 3 | wet-ink; never auto-fill |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `date_signed` | orange | wrong 1/5; oc 1/5; miscompr 1/5 |
| `in_re` | green | oc 1/5 |
| `person_unknown_name` | green | oc 1/5 |
| `family_friends_date` | yellow | oc 1/5; miscompr 1/5 |
| `family_friends_contacted` | yellow | oc 2/5; miscompr 1/5 |
| `name_not_contacted` | green | oc 1/5 |

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
green    24
orange    1
yellow    2
```
