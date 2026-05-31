---
form_id: MISC-101
form_title: Motion (General Probate)
form_revision: "9-12-19"
jurisdiction: Maine
court: Probate
filer_role: movant
statutes:
  - "M.R. Prob. P. 7 (Motions)"
filing_deadline_days: null
filing_deadline_anchor: "hearing_date"
service_required: true
service_recipients: "all_interested_persons"
n_fields: 22
addendum_supported: true
addendum_target_fields:
  - "motion_for"
  - "order_decision"
  - "service_recipients"
parties:
  - movant
  - attorney
  - service_recipient (varying number)
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 8 | deterministic from movant + attorney records |
| narrative_derived | 10 | LLM over motion text + certificate of service |
| signature | 1 | wet-ink (movant signature) |

## Procedural context

A general-purpose motion form usable in any probate proceeding. Movant
states what relief is requested (`motion_for`), includes a certificate
of service listing who was served (`service_recipients`, `service_date`),
and the court enters an order at the bottom (`order_decision`,
`order_date`).

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `motion_for` paraphrase loss | LLM abstracts specific relief ("more time to file inventory") into generic ("continuance") | hand review (semantic) |
| `service_recipients` over-claim | LLM lists recipients the narrative doesn't confirm were served | hand review |
| `order_decision` premature | LLM fills the order disposition before the court has ruled | category=external (court fills this section, not movant) |
| `footnote_reference` boilerplate | LLM substitutes its own footnote text | hand review; this is court-published form text |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `order_decision` | 35 | court-side field; movant should leave blank |
| `motion_for` | 35 | semantic preservation risk |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county_probate_court, docket_no | drift |
| `data_type: date` | motion_date, service_date, order_date | invalid date |
| `data_type: person_name` | movant_printed_name, certificate_of_service_name | non-name text |

## Conditional writability

`order_decision` and `order_date` should be left blank by the movant
(the court fills them). Encode as `external`:

```yaml
# TODO classifications.yaml:
order_decision:
  category: external
  subcategory: court_order
order_date:
  category: external
  subcategory: court_order_date
```

## Risk distribution

```
green:  ~16
yellow: ~4
orange: 2
red:    0
```
