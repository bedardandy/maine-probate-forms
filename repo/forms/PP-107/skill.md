---
form_id: PP-107
form_title: Petition for Appointment of Conservator of Minor
form_revision: "07-01-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 5-401 (Petition for conservator — minor)"
  - "18-C M.R.S.A. § 5-403 (Emergency appointment)"
filing_deadline_days: null
service_required: true
service_recipients: "minor_legal_parents_and_interested_persons"
n_fields: 35
addendum_supported: true
addendum_target_fields:
  - "notify_persons_address"
  - "minor_assets_asset"
  - "minor_residence"
parties:
  - petitioner
  - minor
  - minor_attorney
  - notify_person (1..N, repeating)
slot_groups:
  - notify_persons
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict |
| party_attr | 11 | deterministic from petitioner + minor + attorney records |
| narrative_derived | 14 | LLM (minor info + notice list + asset narrative) |
| legal_choice | 4 | human — emergency, interpreter, scope, etc. |
| signature | 4 | wet-ink |

## Procedural context

Petition to appoint a conservator over a minor's assets. Used when a
minor has received money or property that needs adult management (e.g.,
inheritance, settlement, gift). The form requires identifying:

- The minor's residence and current legal parents
- A description of the assets requiring conservatorship
- Persons to be notified of the petition

This form is the minor-conservatorship analog of PP-201 (which is for
adults). It's distinct from PP-101 (guardian of minor's PERSON).

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `notify_persons_*` slot duplication | same person at multiple slots | `dedupe_within(notify_persons_name)` |
| `minor_assets_asset` paraphrase loss | LLM abstracts "$48,000 settlement from car accident" into "personal injury proceeds" | hand review (semantic) |
| `emergency_conservator` over-confidence | LLM picks "yes" when narrative is silent | category=legal_choice |
| `interpreter_needed` over-claim | minors typically don't need interpreters; LLM may default to "yes" inappropriately | category=legal_choice |
| `minor_attorney` confusion | optional field; LLM may fill with the petitioner's attorney instead of the minor's separately-appointed counsel | hand review |

## High-risk fields (yellow tier: 7 fields)

Most are narrative_derived around the minor's situation and asset list.

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(notify_persons_name)` | notify_persons_*_name | duplicates |
| `dedupe_within(notify_persons_address)` | notify_persons_*_address | duplicates |
| `populate_from_case_dict` | docket_no, county_probate_court | drift |
| `data_type: currency` | minor_assets_asset (often appears with $ amounts) | non-numeric (note: this is text in current schema) |
| `data_type: address` | minor_residence, notify_persons_address | malformed |

## Conditional writability

None encoded. `emergency_conservator == true` should unlock additional
emergency-specific narrative fields if they exist on the PDF.

## Risk distribution

```
green:  ~26
yellow: 7
orange: 0
red:    0
```
