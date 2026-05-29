---
form_id: DE-602
form_title: Sworn Statement (PR Closing of Decedent Estate)
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: personal_representative
statutes:
  - "18-C M.R.S.A. § 3-1003 (Closing of estate by sworn statement)"
  - "18-C M.R.S.A. § 3-1004 (Liability of distributees to claimants)"
filing_deadline_days: null
filing_deadline_anchor: "final_distribution_date"
service_required: true
service_recipients: "all_known_distributees_and_unpaid_claimants"
n_fields: 12
addendum_supported: true
addendum_target_fields:
  - "further_verify_actions"
  - "provisions_details"
parties:
  - personal_representative
  - notary
hand_authored: true
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant     | 3 | deterministic from case_dict |
| party_attr        | 2 | notary record (officer name + county) |
| narrative_derived | 5 | LLM: PR name, further-verify actions, provisions, notary appearer name, signature date |
| signature         | 2 | wet-ink (PR + notary jurat) |

## Procedural context

DE-602 is the **PR's closing affidavit** in an unsupervised
administration. Under § 3-1003, the PR can close the estate by
filing a sworn statement (no formal court order needed) **once
all of the following are true**:

1. The estate has been fully administered.
2. All known claims have been satisfied or otherwise resolved.
3. All assets have been distributed to entitled persons.
4. A copy of this statement has been mailed to every distributee
   and every unpaid claimant (the `service_recipients` set).

**Filing this form is what closes the estate**: once filed AND
the year-after-filing creditor window expires, the PR's authority
ends automatically under § 3-1003. No further court action needed
(unlike DE-501's supervised path which requires DE-505 court
order).

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| **`further_verify_actions` paraphrase** | LLM writes generic "ensured all assets distributed" instead of citing the specific steps (e.g., obtained tax clearance letter, secured release-of-claim from creditor X, filed final inventory amendment) | hand review — yellow tier |
| **PR signs before distribution complete** | LLM writes a `pr_signature_date` that precedes the actual final distribution date | category=narrative_derived; consider adding date_order check against an external `final_distribution_date` field |
| **`notary_appearance_name` vs `personal_representative_name` drift** | LLM writes the PR's full legal name in one field and an informal version in the other | TODO: add `equals_field(personal_representative_name, relaxed)` |
| **`provisions_details` over-claim** | LLM lists provisions not actually in the will (e.g., asserts a specific bequest when narrative is silent) | hand review |
| **§ 3-1003(b) service omission** | The form RECITES that the PR served the statement on distributees and unpaid creditors — but the LLM might fill the form before service is complete | service_required=true; flagged in skill metadata for pipeline gating |

## High-risk fields (1 yellow)

- `further_verify_actions` (yellow, 33): the affidavit's substantive
  content. The PR is swearing under oath that they took these
  steps; over-claim here is perjury risk. Hand review mandatory.

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county_name, docket_no, estate_of_decedent | drift from case dict |
| `data_type: person_name` | personal_representative_name, notary_officer_name | non-name text |
| `data_type: date` | pr_signature_date, notary_date | invalid date |

## Conditional writability

```yaml
# Recommended classifications.yaml additions (not yet encoded):
notary_appearance_name:
  validators:
    - "equals_field(personal_representative_name, relaxed)"
# Rationale: the PR is the affiant who appears before the notary,
# so notary_appearance_name == personal_representative_name. Catches
# LLM drift where the two fields disagree.
```

## Risk distribution

```
green:  11
yellow:  1
orange:  0
red:     0
```

## Sample case sketch

> Estate of Helen R. Larrabee. PR Daniel Larrabee has:
> - Filed the final inventory (DE-405) on 2026-01-12.
> - Resolved a $14,200 disputed claim by Bangor Savings via
>   DE-504 (allowed in full, paid 2026-02-20).
> - Distributed remaining assets per intestate shares to three
>   children on 2026-04-30.
> - Obtained tax clearance from Maine Revenue Services and
>   IRS Form 706 closing letter.
> - Mailed copies of the sworn statement to all three distributees
>   and to Bangor Savings (now-satisfied creditor) on 2026-05-08.
>
> The DE-602 `further_verify_actions` should specifically cite
> these five steps. Generic "all duties completed" prose is
> insufficient.

## Why this form matters for the LLM pipeline

DE-602 is **the last document in the standard PR pipeline**: every
estate that's opened informally (DE-101 or DE-201) and closes
unsupervised ends here. So the pipeline must coordinate with
upstream filings: the case_dict carried through DE-101/201 →
DE-405 → DE-602 must remain consistent. The `populate_from_case_dict`
validator catches drift; a future cross-form check could verify
that DE-602's `personal_representative_name` matches the PR appointed
in the case's DE-101/201.
