---
form_id: AD-007
form_title: Confidential Statement (Adoption)
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-A M.R.S.A. §§ 9-301 to 9-315 (Adoption — Maine pre-2019)"
  - "22 M.R.S.A. § 4137 (Information sharing)"
filing_deadline_days: null
filing_deadline_anchor: "petition_filing_date"
service_required: true
service_recipients: "department_of_health_human_services"
n_fields: 58
addendum_supported: true
addendum_target_fields:
  - "additional_explanation"
parties:
  - adoptee
  - parent_1
  - parent_2
slot_groups:
  - parent_questionnaire   # parent1_q1..q25, parent2_q1..q25
hand_authored: true
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant     | 4  | deterministic from case_dict (county, location, two docket numbers) |
| party_attr        | 1  | adoptee name from party record |
| narrative_derived | 49 | LLM — 50 parent questionnaire answers (25 × 2 parents) + additional_explanation |
| signature         | 4  | wet-ink (parent 1 + parent 2, name + date each) |

## Procedural context

AD-007 is a **Confidential Statement** filed alongside the adoption
petition. It captures background information about the two birth
parents (parent_1, parent_2) via a 25-question questionnaire about
each: physical / medical / educational / occupational history.
Maine Probate uses this to populate the adoption record and to
satisfy § 4137 information-sharing requirements when the adoptee
later seeks identifying information.

The form is **structurally symmetric**: every `parent1_qN` has a
corresponding `parent2_qN`. The narrative fact bank should be
shaped the same way: `narrative_facts.parent_1.q1..q25` and
`narrative_facts.parent_2.q1..q25`.

**Two probate vs. district dockets.** Adoption matters straddle
the two court systems in Maine: the petition starts in probate
but related orders (e.g. termination of parental rights) may
originate in district court. The form has BOTH
`probate_docket_no` and `district_docket_no`, and the case_dict
must supply both keys (or null if not applicable).

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| **`location` paraphrase** | LLM writes "Cumberland County" when the form wants `Portland, ME` | hand review — `location` is orange-tier |
| **Question/answer alignment slip** | LLM answers parent2_q9's question content into parent1_q9 (off-by-one due to question text in narrative) | dedupe_within wouldn't catch — needs hand review |
| **Missing parent_2 path** | LLM leaves all parent2_* fields blank when narrative is silent (single-parent surrender) | acceptable for genuine single-parent cases; flagged if narrative names two parents |
| **Date inconsistency** | parent1_date and parent2_date should be the same (both parents sign on the same day) | not currently encoded — TODO equals_field |
| **`additional_explanation` boilerplate** | LLM pads with generic disclaimers | hand review |

## High-risk fields

- `location` (orange, 48): the form's "Location" field is a
  case-constant the case_dict supplies, but eval flagged 4/5
  fills as drifting from the case_dict source. Catch via
  `populate_from_case_dict` validator (already encoded).
- `parent1_q9` (green, 13): slight elevation; ratings on the
  question about reason for surrender/relinquishment are highly
  variable across cases. Hand-review when present.

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county, location, probate_docket_no, district_docket_no | drift from case dict |
| `data_type: person_name` | adoptee_name | non-name text |
| `data_type: date` | parent1_date, parent2_date | invalid date |

## Conditional writability

```yaml
# Recommended TODO classifications.yaml:
# 1. Encode an equals_field for parent2_date == parent1_date
#    (relaxed, since both parents typically sign at the same notarial
#    appearance).
# 2. Encode parent1_qN ↔ parent2_qN pairing as a "missing if other
#    missing" rule (not yet implemented as a validator type).
```

## Risk distribution

```
green:  57
yellow:  0
orange:  1   (location)
red:     0
```

## Privacy note

This form is **filed under seal**. The adoptee can request
identifying information only after age 18 per § 4137, and only
when the form's information_sharing_intent fields support
disclosure. Downstream consumers must treat AD-007 fills as
restricted-access: they cannot be batched with public-record
filings.

## Sample case sketch

> Petitioner A is adopting their stepchild (single-parent adoption
> path: biological mother retains rights, biological father
> surrenders). `parent_1` is the biological mother (still parental),
> `parent_2` is the surrendering biological father.
> `parent2_q1..q25` are answered from the father's surrender
> affidavit; `parent1_q1..q25` reflect the mother's continuing role.
> `additional_explanation` notes the stepparent context.
