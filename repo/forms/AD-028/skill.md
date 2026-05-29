---
form_id: AD-028
form_title: Affidavit of Parentage
form_revision: "5-6-21"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-A M.R.S.A. §§ 9-301 to 9-315 (Adoption)"
filing_deadline_days: null
service_required: true
n_fields: 24
addendum_supported: true
parties:
  - birth_mother
  - form_subject
  - name_of_other
  - notary
  - putative_parent
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 10 | LLM over narrative + validators |
| party_attr | 9 | deterministic from party record |
| signature | 3 | wet-ink; never auto-fill |
| case_constant | 2 | deterministic from case_dict |

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `putative_parent_statements` | green | oc 1/5 |
| `newspaper_name_address` | green | miscompr 1/5 |
| `mother_signature` | green | oc 1/5 |
| `notary_county` | green | oc 1/5 |
| `name_of_appearer` | green | oc 1/5 |

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
```
