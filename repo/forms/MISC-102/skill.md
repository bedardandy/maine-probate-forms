---
form_id: MISC-102
form_title: Witness Subpoena (Probate)
form_revision: "8-6-21"
jurisdiction: Maine
court: Probate
filer_role: requesting_party
statutes:
  - "M.R. Civ. P. 45 (Subpoenas)"
  - "M.R. Prob. P. 26"
filing_deadline_days: null
filing_deadline_anchor: "hearing_date"
service_required: true
service_recipients: "commanded_party"
n_fields: 38
addendum_supported: true
addendum_target_fields:
  - "produce_designated_things"
  - "produce_additional"
  - "permit_designated_things"
  - "permit_time_place"
parties:
  - commanded_party
  - requesting_party
  - register_attorney
  - objection_recipient
section_headers_exclusive: false
section_headers:
  - appear_probate_court_enabler
  - appear_before_enabler
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 2 | deterministic from case_dict (county, docket) |
| party_attr | 11 | deterministic from commanded + requesting party + register attorney records |
| legal_choice | 2 | section header checkboxes |
| narrative_derived | 22 | LLM over subpoena demand text |
| signature | 1 | wet-ink (clerk signature on issued subpoena) |

## Procedural context

A subpoena commands a witness to either (1) **appear at probate
court** at a stated date/time, OR (2) **appear before a designated
person** (typically the requesting attorney) at a stated location.
The two paths are mutually exclusive checkboxes; the form has been
designed so that each branch has its own conditional fields gated on
the parent enabler checkbox.

In addition, the subpoena can command production of documents/things
("produce") and/or inspection at a designated location ("permit").

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| both appear-paths populated | `appear_probate_court_*` and `appear_before_*` both filled | `writable_when` (already encoded; validator rejects) |
| produce + permit double-fill | LLM treats them as alternatives when they can coexist | both are optional; multiple-true is OK |
| `commanded_to_address` confusion | LLM uses requesting party's address instead of commanded party's | category=party_attr with explicit `commanded_party` label |
| `fees_travel` hallucination | LLM picks a number when narrative is silent | risk_tier flag; hand review |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `commanded_to_*` (2 fields) | 35-50 | bespoke party reference; risk of using wrong party |

## Validators

| validator | applies to | catches |
|---|---|---|
| `writable_when` enforcement | `appear_probate_court_*` (gated by enabler), `appear_before_*` (gated by enabler) | filling wrong-branch fields |
| `populate_from_case_dict` | docket_no, county, county_page2 | drift |
| `data_type: address` | commanded_to_address | malformed |
| `data_type: currency` | fees_travel | non-numeric |

## Conditional writability (encoded)

```
appear_probate_court_enabler == true
  → appear_probate_court_name / _date / _time writable

appear_before_enabler == true
  → appear_before_who / _where / _offices / _additional / _final writable
```

The `produce_*` and `permit_*` blocks are NOT gated: a subpoena can
command appearance, production, and inspection simultaneously.

## Risk distribution

```
green:  ~24
yellow: ~10
orange: 2
red:    0
```
