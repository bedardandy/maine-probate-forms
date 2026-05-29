---
form_id: DE-501
form_title: Petition with Respect to Supervised Administration
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: personal_representative_or_petitioner
statutes:
  - "18-C M.R.S.A. § 3-502 (Supervised administration — petition)"
  - "18-C M.R.S.A. § 3-501 (Supervised administration — nature)"
  - "18-C M.R.S.A. § 3-505 (Supervised administration — interim orders)"
filing_deadline_days: null
filing_deadline_anchor: "case_open"
service_required: true
service_recipients: "interested_persons"
n_fields: 20
addendum_supported: true
addendum_target_fields:
  - "circumstances"
  - "special_restrictions_details"
parties:
  - petitioner
  - attorney
legal_choices:
  - will_supervised_provisions
  - supervision_request
hand_authored: true
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant     | 3 | deterministic from case_dict |
| party_attr        | 6 | deterministic from petitioner + attorney records |
| narrative_derived | 7 | LLM over narrative + validators |
| legal_choice      | 1 | human decision (`will_supervised_provisions`) |
| signature         | 2 | wet-ink (petitioner + judge) |
| external          | 1 | left blank for the court (`judge_name`) |

## Procedural context

Filed either to **open** supervised administration of a decedent's
estate or to **convert** an existing unsupervised administration to
supervised under § 3-502. Two posture variants the LLM must
distinguish from the narrative:

1. **Open**: filed at start of probate. `testacy_status` reflects
   intestate / testate / unknown; `circumstances` justifies why
   supervision is warranted (small heir, contested heirs, real
   estate disposition, out-of-state PR, etc.).
2. **Convert**: filed after PR is appointed. `circumstances`
   recites what changed (PR misconduct allegation, beneficiary
   dispute, complex asset valuation). § 3-502 lists the grounds.

The same form is used by both posture variants: only the narrative
distinguishes them. **A wrong posture call cascades** into the wrong
boilerplate for `circumstances` and `supervision_request`.

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| Posture confusion (Open vs Convert) | LLM cites § 3-502 grounds on an Open filing | hand review of `circumstances` |
| `will_supervised_provisions` mis-checked | LLM checks "yes" when the will is silent on supervision | category=legal_choice (human decision) |
| `testacy_status` paraphrase | LLM writes "decedent left a will" instead of the form's three enumerated choices | value_in candidate (not yet encoded) |
| `supervision_request` boilerplate drift | LLM rewrites the prayer-for-relief language | hand review |
| `special_restrictions_details` over-claim | LLM invents restrictions not in the narrative | hand review |
| `court_order` confusion | LLM fills the judge's section | category=narrative_derived but flagged in audit; should be `external` — TODO |
| `judge_name` not blank | LLM writes a placeholder | category=external + `fill_source=left_blank` |

## High-risk fields (yellow tier: 2 fields)

- `will_supervised_provisions` (yellow, 20): legal_choice that
  drives downstream supervision scope. Misclick changes the
  estate's entire administration regime.
- `circumstances` (yellow, 25): narrative explaining why
  supervision is needed. LLM tends to write generic
  boilerplate; a human should verify it cites specific
  facts from the case narrative.

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county_probate_court, docket_no, decedent_name | drift from case dict |
| `data_type: phone/email/bar_number` | attorney_* | malformed contact |
| `data_type: person_name` | petitioner_name, attorney_name | non-name text |
| `data_type: date` | signature_date, order_date | invalid date |

## Conditional writability

```yaml
# court_order, order_date, judge_name should be left blank by the
# petitioner — they're filled by the court after the hearing.
# court_order is currently category=narrative_derived but should be
# external. Flagged for follow-up in classifications.yaml.
```

## Risk distribution

```
green:  18
yellow:  2
orange:  0
red:     0
```

## Sample case sketch

> A § 3-502 posture-Convert filing: PR named in informal probate has
> been removed for malfeasance; remaining heir files DE-501 to
> convert the estate to supervised administration so the successor
> PR's actions require court approval. `circumstances` should recite
> the prior PR's removal docket entry and the specific concerns
> (e.g., commingling, refusal to inventory).
