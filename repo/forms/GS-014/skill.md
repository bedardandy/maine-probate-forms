---
form_id: GS-014
form_title: Annual Report of Guardian of Minor
jurisdiction: Maine
court: Probate
filer_role: guardian
statutes:
  - "18-C M.R.S.A. § 5-211"
filing_deadline_days: 365
filing_deadline_anchor: previous_report_or_appointment
service_required: false
n_fields: 74
addendum_supported: true
addendum_target_fields:
  - "*_desc"
  - "*_achievements"
  - "*_needs"
  - "*_responsibilities"
  - "*_information"
  - "*_recommendations"
  - "funds_received_purpose_*"
parties:
  - guardian
  - minor
  - legal_parent (1..3, repeating; slot pattern <prefix>_<idx>_<role>)
slot_groups:
  - name: funds_received
    pattern: <prefix>_<role>_<idx>
    indices: [1, 2, 3, 4, 5]
    roles: [amount, source, purpose]
  - name: legal_parent
    pattern: <prefix>_<idx>_<role>
    indices: [1, 2, 3]
    roles: [name, address, phone]
section_choices:
  - treated_by_physician: yes/no
  - treated_by_counselor: yes/no
  - treated_by_case_worker: yes/no
  - treated_by_dentist: yes/no
  - treated_by_other: yes/no (with other_treatment_type narrative)
  - needs_being_met: yes/no
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 4 | deterministic from case_dict |
| party_attr | 7 | deterministic from guardian + minor records |
| legal_choice | 7 | human — treatment checkboxes, needs_being_met |
| narrative_derived | 53 | LLM over guardian's annual report |
| signature | 3 | wet-ink (guardian signature + date) |

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `treated_by_other` yes + invented `other_treatment_type` text | LLM checks "other" then fills with generic | `risk_tier=red` for `other_treatment_type`; flag for human |
| funds_received slot duplication | `funds_received_amount_1` repeated at slot 2 | `dedupe_within(funds_received_source)` |
| legal_parent_1 vs _2 swapped | birth order arbitrary in narrative | hand-review when only one parent named in narrative |
| improved-vs-deteriorated cross-fill | LLM puts deterioration content in `*_improved_desc` | semantic check; cannot enforce in regex |
| writable_when scoping | improved_desc filled when status is "deteriorated" | encode `writable_when: mental_health_status == "improved"` |

## High-risk fields (red tier, eval-driven)

| field | risk | reasons |
|---|---|---|
| `treated_by_other` | 100 | wrong 2/5, oc 2/5, miscompr 2/5 |
| `other_treatment_type` | 100 | wrong 2/5, oc 2/5, miscompr 2/5 |
| `legal_parent_1_name` | 60 | wrong 1/5, oc 1/5, miscompr 1/5 |

## Validators

| validator | applies to | what it does |
|---|---|---|
| `dedupe_within(funds_received_amount)` | funds_received_amount_* | rejects duplicate amounts |
| `dedupe_within(funds_received_source)` | funds_received_source_* | rejects duplicate sources |
| `dedupe_within(legal_parent_name)` | legal_parent_*_name | rejects duplicate parent names |
| `nonempty_if_desc` | funds_received_purpose_*, _source_* | requires `_amount_<n>` populated |
| `populate_from_case_dict` | docket_no_probate, docket_no_district, county | drift detection |

## Conditional writability

```
treated_by_other == true       → other_treatment_type writable
mental_health_status == "improved"  → mental_health_improved_desc writable
mental_health_status == "deteriorated" → mental_health_deteriorated_desc writable
physical_health_status == "improved"  → physical_health_improved_desc writable
physical_health_status == "deteriorated" → physical_health_deteriorated_desc writable
```

(Not yet encoded: `mental_health_status` / `physical_health_status`
are themselves classified as `narrative_derived` rather than
`legal_choice`; promote in classifications.yaml when verified.)

## Computed formulas

None encoded. The form does not appear to have totals over funds_received.

## Risk distribution

```
green:  ~17
yellow: ~49
orange: ~6
red:    2
```
