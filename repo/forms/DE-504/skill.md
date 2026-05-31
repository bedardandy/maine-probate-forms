---
form_id: DE-504
form_title: Petition to Resolve Disputed Claim and Allowance
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: personal_representative_or_petitioner
statutes:
  - "18-C M.R.S.A. § 3-806 (Allowance of claims)"
  - "18-C M.R.S.A. § 3-807 (Payment of claims)"
filing_deadline_days: 60
filing_deadline_anchor: "claim_disallowance_notice_date"
service_required: true
service_recipients: "claimant_and_personal_representative"
n_fields: 17
addendum_supported: true
addendum_target_fields:
  - "factual_legal_issues"
  - "proof_and_evidence"
parties:
  - petitioner
  - attorney
legal_choices:
  - order_disposition
hand_authored: true
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant     | 3 | deterministic from case_dict |
| party_attr        | 5 | attorney record (petitioner attestation is narrative) |
| narrative_derived | 6 | LLM over narrative — claim disposition + amounts |
| signature         | 3 | wet-ink (petitioner + judge) |

## Procedural context

Filed when a creditor's claim against an estate has been disallowed
(or partially disallowed) by the PR and the creditor wants the
probate court to adjudicate. Under § 3-806, the claimant has **60
days from the mailing of disallowance notice** to file this
petition or the claim is barred.

Either the claimant OR the PR may petition (the form's
`filer_role` is permissive). Most commonly filed by the claimant.

The form has a dual structure:
1. **Petitioner section** (top): identifies the dispute, recites
   facts, requests relief.
2. **Order section** (bottom, fields `order_disposition`,
   `allowed_part_amount`, `order_date`, `judge_signature`):
   completed by the **court** after hearing. These should be left
   blank when the petitioner files.

## Computed formulas

None: `allowed_part_amount` is a court-ordered figure, not a
deterministic computation from the petition's facts.

## Known LLM failure modes (anticipated; no eval evidence yet)

| symptom | example | guard |
|---|---|---|
| LLM fills the order section | LLM writes "claim allowed in full" in `order_disposition` | category should be `external`; current schema flags it as `narrative_derived` — TODO classifications.yaml |
| LLM invents `allowed_part_amount` | LLM splits the claimed amount as a guess | category should be `external`; flagged for follow-up |
| `factual_legal_issues` boilerplate | LLM writes a generic dispute summary instead of citing specific notice-of-disallowance grounds | hand review |
| `proof_and_evidence` over-claim | LLM lists evidence the case narrative doesn't actually have (e.g., "promissory note" when there's only an oral agreement) | hand review |
| Petitioner identity confusion | LLM lists the PR as petitioner when the claimant is the actual filer | hand review against narrative |

## High-risk fields (none yellow/orange/red)

This form is all-green by static risk score, but the order-side
fields (`order_disposition`, `allowed_part_amount`, `order_date`,
`judge_signature`) are *latent* risks because the auto-classifier
treats them as petitioner-fillable. A future classifications.yaml
should mark them `external` so the validator catches LLM
encroachment on the court's section.

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county_probate_court, docket_number, decedent_name | drift from case dict |
| `data_type: phone/email/bar_number` | attorney_* | malformed contact |
| `data_type: currency` | allowed_part_amount | malformed dollar value (validator runs but value is judge-set) |
| `data_type: date` | date_signed, order_date | invalid date |

## Conditional writability

```yaml
# Recommended classifications.yaml override (not yet encoded):
order_disposition:
  category: external
  fill_source: left_blank
allowed_part_amount:
  category: external
  fill_source: left_blank
order_date:
  category: external
  fill_source: left_blank
```

## Risk distribution

```
green:  17
yellow:  0
orange:  0
red:     0
```

## Sample case sketch

> Creditor A submits a $14,200 claim to the estate based on a
> handwritten IOU. PR rejects the claim via § 3-806 notice on
> 2026-03-01. Creditor A files DE-504 on 2026-04-12 (within the
> 60-day window). `factual_legal_issues` cites the IOU and the
> rejection notice; `proof_and_evidence` lists the IOU itself, a
> bank deposit matching the loan amount, and an email
> acknowledging the debt.
