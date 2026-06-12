---
form_id: DE-301
form_title: Petition for Formal Appointment of Special Administrator
form_revision: "07-01-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 3-614 (Appointment of special administrator)"
  - "18-C M.R.S.A. § 3-615 (Who may be special administrator)"
  - "18-C M.R.S.A. § 3-617 (Powers and duties of special administrator appointed by court)"
filing_deadline_days: null
service_required: true
n_fields: 41
addendum_supported: true
parties:
  - attorney
  - decedent
  - petitioner
legal_choices:
  - will_presented_for_probate
  - will_details
slot_groups:
  - interested_party
---

## Pipeline routing

| category | n | path |
|---|---|---|
| narrative_derived | 17 | LLM over narrative + validators |
| party_attr | 7 | deterministic from party record |
| other | 6 | TRIAGE — unclassified |
| signature | 5 | wet-ink; never auto-fill |
| case_constant | 3 | deterministic from case_dict |
| legal_choice | 2 | human decision required |
| external | 1 | left blank (filled by court/clerk) |

## Computed formulas

None.

## Repeating slot groups

| prefix | indices | suffixes |
|---|---|---|
| `interested_party` | 1..4 | addr, name |

## Known LLM failure modes (May-2026 eval)

| field | tier | eval signals |
|---|---|---|
| `county_probate_court` | green | oc 1/5 |
| `domiciled_in_county` | green | oc 1/5 |
| `will_presented_for_probate` | yellow | oc 1/5 |

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
yellow   16
```
