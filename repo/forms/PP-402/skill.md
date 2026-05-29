---
form_id: PP-402
form_title: Acceptance of Appointment by Conservator
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: conservator
statutes:
  - "18-C M.R.S.A. § 5-403 (Acceptance of appointment)"
filing_deadline_days: null
filing_deadline_anchor: "appointment_order_date"
service_required: false
n_fields: 8
addendum_supported: false
parties:
  - conservator
  - corporate_conservator
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 2 | deterministic from conservator record |
| narrative_derived | 1 | LLM (rare; only if corporate conservator + special authority) |
| signature | 2 | wet-ink |

## Procedural context

Compact 8-field form. The appointed conservator (or an officer of a
corporate conservator) signs to accept the appointment. Letters of
conservatorship issue only after this is on file.

The form supports two filer paths:
1. **Individual conservator**: name + signature.
2. **Corporate conservator**: entity name + authorized officer's
   name and title ("its").

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `conservator_name` vs `its_title` confusion | LLM puts an officer's title in the entity-name field | category=party_attr with explicit party labels |
| filling both individual + corporate paths | LLM populates both blocks | conditional writability (TODO — one path only) |
| `conservator_address` vs corporate address mismatch | LLM uses the individual conservator's home address for a corporate entity | hand review |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `conservator_address` | 35-50 | composite contact info |
| `conservator_name` | 35-50 | path-confusion risk |
| `its_title` | 35-50 | only meaningful for corporate path |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | docket_no, county_probate_court, in_re | drift |
| `data_type: address` | conservator_address | malformed |

## Conditional writability (TODO)

```yaml
# Encode in classifications.yaml once verified:
its_title:
  writable_when:
    all_of:
      - field: guardian_by  # corporate-officer name field
        exists: true
```

## Risk distribution

```
green:  ~5
yellow: 0
orange: 3
red:    0
```
