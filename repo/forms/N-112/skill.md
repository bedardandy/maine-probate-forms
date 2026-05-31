---
form_id: N-112
form_title: Notice of Appointment (Guardian or Conservator)
jurisdiction: Maine
court: Probate
filer_role: guardian_or_conservator
statutes:
  - "18-C M.R.S.A. § 5-309 (Notice of appointment — guardian)"
  - "18-C M.R.S.A. § 5-411 (Notice of appointment — conservator)"
filing_deadline_days: 30
filing_deadline_anchor: "appointment_order_date"
service_required: true
service_recipients: "interested_persons"
n_fields: 18
addendum_supported: true
parties:
  - filer (guardian or conservator)
  - subject (individual under protection)
  - appointing_court
section_choices:
  - subject_to_guardianship: yes/no
  - subject_to_conservatorship: yes/no
  - appointed_as_guardian: yes/no
  - appointed_as_conservator: yes/no
section_headers_exclusive: false  # both guardian and conservator can be true
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 5 | deterministic from case_dict |
| party_attr | 5 | deterministic from filer + subject + appointing-court records |
| narrative_derived | 4 | LLM (appointed-court details when not in case_dict) |
| legal_choice | 4 | human — guardian/conservator role yes/no |
| signature | 0 | (no signature on this form; informational notice only) |

## Procedural context

A notice sent to interested persons confirming who has been appointed
guardian or conservator (or both) over a specific individual. Must be
served within 30 days of the appointment order. Captures:

- Subject's name and capacity (guardianship and/or conservatorship)
- Filer's appointed role (guardian and/or conservator)
- Appointing court name + address
- Filing county

The 4 yes/no fields are NOT mutually exclusive: a single fiduciary
can be appointed as both guardian and conservator.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `subject_to_*` vs `appointed_as_*` confusion | LLM swaps "subject was under guardianship" vs "filer was appointed as guardian" | category=legal_choice; manual verify |
| `appointing_court_name` confusion | LLM uses the current-filing court instead of the original appointing court (relevant for transfers) | category=case_constant when transfer is involved |
| `filing_county` vs `appointing_court_county` mismatch | LLM uses the wrong county when filer is serving notice in a different county | hand review |
| `i_we_name` plural form | "I" vs "We" for single vs co-fiduciaries | hand review |

## High-risk fields (yellow tier: 5 fields)

Most yellow risk concentrated around the role yes/no checkboxes.

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | docket_number, county_name, in_re | drift |
| `data_type: address` | appointing_court_address | malformed |
| `data_type: person_name` | i_we_name, subject_name | non-name text |

## Conditional writability

None encoded. Note: the 4 yes/no role checkboxes are independent but
at least one of `appointed_as_guardian` or `appointed_as_conservator`
must be true (the notice is meaningless otherwise): could add as a
form-level invariant.

## Risk distribution

```
green:  ~12
yellow: 5
orange: 1
red:    0
```
