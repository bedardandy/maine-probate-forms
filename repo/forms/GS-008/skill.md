---
form_id: GS-008
form_title: Acceptance of Appointment by Guardian (Minor)
jurisdiction: Maine
court: Probate
filer_role: guardian
statutes:
  - "18-C M.R.S.A. § 5-209 (Acceptance of appointment by guardian of minor)"
filing_deadline_days: null
filing_deadline_anchor: "appointment_order_date"
service_required: false
n_fields: 17
addendum_supported: false
parties:
  - guardian
  - corporate_guardian (alternative path)
  - notary
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 8 | deterministic from guardian + notary records |
| narrative_derived | 1 | LLM (rare) |
| legal_choice | 2 | individual vs corporate path |
| signature | 3 | wet-ink (guardian + notary jurat) |

## Procedural context

Compact 17-field form. The appointed guardian (or an officer of a
corporate guardian) signs to accept the appointment of a minor. Letters
of guardianship issue only after this is on file. Two filer paths:

1. **Individual guardian**: name + contact + signature + notary jurat
2. **Corporate guardian**: entity name + authorized officer's name and
   title; same notary jurat

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| individual vs corporate path conflict | LLM fills both `guardian_name` and corporate-officer fields | conditional writability (TODO) |
| `guardian_full_name` vs `guardian_name` confusion | LLM puts initials in one, full name in other | hand review |
| jurat name mismatch | `notary_appearer_name` doesn't match `guardian_name` | data-cross-check (TODO validator) |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `guardian_name` vs `guardian_full_name` | 35-50 | likely-duplicate fields; risk of mismatch |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county_probate, docket_no | drift |
| `data_type: address` | guardian_address | malformed |
| `data_type: phone` | guardian_phone | invalid |
| `data_type: email` | guardian_email | invalid |

## Conditional writability (TODO)

The PDF has a corporate-guardian branch that should mutually exclude
the individual-guardian branch. Once verified:

```yaml
guardian_name:
  writable_when:
    none_of:
      - field: corporate_guardian_name
        exists: true
```

## Risk distribution

```
green:  ~13
yellow: ~2
orange: 2
red:    0
```
