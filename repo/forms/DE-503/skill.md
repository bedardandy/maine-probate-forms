---
form_id: DE-503
form_title: Notice of Disallowance of Claim Against Estate
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: personal_representative
statutes:
  - "18-C M.R.S.A. § 3-806 (Allowance of claims)"
  - "18-C M.R.S.A. § 3-805 (Claims; classification)"
filing_deadline_days: 60
filing_deadline_anchor: "claim_filing_date"
service_required: true
service_recipients: "claimant_and_claimant_attorney"
n_fields: 26
addendum_supported: true
addendum_target_fields:
  - "basis_for_claim"
  - "decision_by_personal_representative"
  - "residence_and_date_of_death"
parties:
  - personal_representative
  - decedent
  - claimant
  - attorney_for_claimant
  - attorney_for_personal_representative
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 16 | deterministic from PR + decedent + claimant + 2 attorney records |
| narrative_derived | 6 | LLM over claim narrative |
| legal_choice | 1 | human — `decision_by_personal_representative` |

## Procedural context

A PR uses this form to formally disallow a creditor's claim against
the estate. The form must be filed within 60 days of the claim being
filed, and triggers the claimant's right to petition the court for
allowance under § 3-806.

## Known LLM failure modes (May-2026 eval)

Compact form, mostly party-attribute fields. Failures cluster around
the two attorney sub-records (claimant vs PR) and the claim narrative.

| symptom | example | guard |
|---|---|---|
| attorney role swap | `claimant_attorney_*` populated with PR's attorney info | category=party_attr with explicit party label; manual verify |
| `basis_for_claim` paraphrase loss | LLM summarizes the claim ("medical bills") instead of preserving specifics ("Mercy Hospital invoice dated 2026-02-14, $4,287.50") | hand review (semantic, no automated guard) |
| `date_claim_due` confusion | LLM writes today's date instead of the statutory 60-day deadline | computed from claim_filing_date; flag in skill_metadata.filing_deadline_anchor |
| `decision_by_personal_representative` over-confidence | LLM picks "disallowed" when narrative supports "allowed in part" | category=legal_choice; never trust LLM |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `attorney_for_claimant_*` | 36-50 | composite contact info; risk of incorrect record |
| `decision_by_personal_representative` | 50 | legal_choice with multiple valid options |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | docket_no, county, estate_name | drift |
| `data_type: currency` | amount_claimed | non-numeric text |
| `data_type: date` | date_claim_due | invalid date |
| `data_type: bar_number` | both attorney bar numbers | non-Maine format |
| `data_type: email` | both attorney emails | invalid format |

## Conditional writability

None encoded: the form's two attorney blocks are both unconditionally
writable (a claimant may be self-represented, in which case the
claimant_attorney block is left blank; that's `nullable`, not gated).

## Risk distribution

```
green:  21
yellow: 0
orange: 5
red:    0
```
