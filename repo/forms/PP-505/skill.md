---
form_id: PP-505
form_title: Physician's or Psychologist's Report (Protective Proceedings)
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: evaluator
statutes:
  - "18-C M.R.S.A. § 5-204 (Guardian appointment — physician's report)"
  - "18-C M.R.S.A. § 5-304 (Adult guardianship — physician's report)"
  - "18-C M.R.S.A. § 5-405 (Conservator appointment — physician's report)"
filing_deadline_days: null
filing_deadline_anchor: "appointment_hearing_date"
service_required: true
service_recipients: "court_petitioner_respondent_attorney"
n_fields: 17
addendum_supported: true
addendum_target_fields:
  - "cognitive_functional_abilities"
  - "mental_physical_condition_evaluation"
  - "guardian_tasks_sufficient_capacity"
  - "conservator_tasks_sufficient_capacity"
  - "recommendations"
parties:
  - evaluator
  - respondent
legal_choices:
  - professional_type
hand_authored: true
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant     | 2 | deterministic from case_dict |
| party_attr        | 2 | evaluator + respondent records |
| narrative_derived | 10 | LLM — cognitive assessment, capacity findings, recommendations |
| legal_choice      | 1 | human decision: `professional_type` (physician/psychologist/PA/NP) |
| signature         | 2 | wet-ink (evaluator + notary if required) |

## Procedural context

PP-505 is the **medical/psychological capacity evaluation** that
must accompany a petition for adult guardianship (§ 5-304),
conservatorship (§ 5-405), or both. It is **not filed by the
petitioner**: the petitioner requests the evaluation; the form is
completed by a licensed evaluator (physician, psychologist,
physician assistant, or nurse practitioner: `professional_type`).

The form has dual purpose:

1. **Capacity assessment**: documents the respondent's cognitive
   and functional abilities, mental and physical condition, and
   the evaluator's specific findings about decision-making
   capacity.
2. **Tailored recommendation**: provides separate capacity
   findings for **guardian tasks** vs **conservator tasks** —
   recognizing that someone might lack capacity for personal-care
   decisions (guardian's domain) but retain capacity for financial
   decisions (conservator's domain), or vice versa.

### Why dual capacity findings matter

Maine's protective-proceedings statute treats guardianship and
conservatorship as **distinct authorities**. PP-505 supports the
court's § 5-304(b) requirement to use the **least restrictive
alternative**: a tailored evaluation may justify a conservator-only
order (financial protection) without a guardian (personal-care
protection), or guardianship limited to specific decision domains.

A vague evaluation that conflates the two creates downstream
problems: the court may default to a fuller appointment than the
record supports.

## Computed formulas

None.

## Known LLM failure modes (May-2026 eval: high-stakes form)

| symptom | example | guard |
|---|---|---|
| **`professional_type` orange-tier confusion** | LLM picks "physician" when evaluator is a psychologist (Maine recognizes both for capacity opinions) | category=legal_choice; TODO add `value_in(physician, psychologist, physician_assistant, nurse_practitioner)` |
| **`cognitive_functional_abilities` paraphrase loss** | LLM writes generic "diminished cognition" instead of citing specific findings (e.g., MMSE score, ADL/IADL specifics, executive function impairment) | hand review — yellow tier |
| **Capacity-task conflation** | LLM gives identical answers for `guardian_tasks_sufficient_capacity` and `conservator_tasks_sufficient_capacity` when the underlying clinical picture supports a distinction | hand review — both yellow |
| **Over-generalization to incapacity** | LLM concludes total incapacity from limited evidence; evaluator should support graduated/limited findings where the clinical record supports them | hand review |
| **Identity drift** | `respondent_name` or `evaluator_name` doesn't match the case caption or the evaluator's license | TODO: equals_field(case_dict.respondent_name) — but respondent's name isn't currently a case_constant; encode via classifications.yaml |
| **`recommendations` over-claim** | LLM recommends specific guardian/conservator candidates; this is the petitioner's job, not the evaluator's | hand review |

## High-risk fields

- `professional_type` (orange, 48): most-failed field per eval.
  Three reasons: (1) Maine recognizes 4 licensure types as
  qualified evaluators, (2) the form lists them as radio options,
  (3) narrative often omits the credential and the LLM guesses
  from context.
- `cognitive_functional_abilities` (yellow, 25): substantive
  cognitive findings. Errors cascade through to the court's
  capacity determination.
- `mental_physical_condition_evaluation` (yellow, 25): the
  clinical narrative the court relies on. Hand review mandatory.
- `guardian_tasks_sufficient_capacity` (yellow, 25): the
  guardian-specific finding.
- `conservator_tasks_sufficient_capacity` (yellow, 25): the
  conservator-specific finding.

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county_probate_court, docket_no | drift from case dict |
| `data_type: date` | evaluation_date, signature_date | invalid date |

## Conditional writability

```yaml
# Recommended TODO additions:
professional_type:
  validators:
    - "value_in(physician, psychologist, physician_assistant, nurse_practitioner)"

# Date-order: evaluation_date must be recent (within 6 months of
# hearing) but the form doesn't carry the hearing date.
# Cross-form check: PP-505.evaluation_date must be within 180
# days of the appointment_hearing_date carried in the case.
# Not yet expressible in the current validator DSL.
```

## Risk distribution

```
green:   12
yellow:   4
orange:   1
red:      0
```

## Sample case sketch

> Respondent: William F. Owens, 78, post-stroke (CVA 2025-11-12).
> Evaluator: Dr. Sarah Lin, psychologist, Maine license PSY-3104.
> Findings: MMSE 18/30 (moderate impairment), retained
> understanding of financial accounts at routine levels but
> impaired executive function for complex decisions. ADL
> independence preserved; IADL impairment (medication
> management, transportation).
>
> Expected fill pattern:
> - `professional_type` = "psychologist"
> - `cognitive_functional_abilities` cites the MMSE score, the
>   ADL/IADL distinction, executive function impairment
> - `guardian_tasks_sufficient_capacity` = "limited — retained
>   capacity for daily personal-care decisions; impaired for
>   complex medical and residential decisions"
> - `conservator_tasks_sufficient_capacity` = "limited — capable
>   of routine banking with supervision; impaired for asset
>   management, contracts, real estate"
> - `recommendations` recites the clinical basis WITHOUT proposing
>   specific guardian/conservator candidates

## Why this form is procedurally distinctive

PP-505 is one of the few probate forms **filed by a third party**
(the evaluator) rather than by a petitioner, respondent, or PR.
This affects the pipeline in three ways:

1. **The "filer" isn't a party in the standard sense**: the
   evaluator submits the form but isn't a litigant. Party-attr
   fields trace to the evaluator's professional record, not a
   case-party record.
2. **Service obligations are reverse**: the petitioner serves the
   evaluation request; the evaluator serves the COMPLETED form back
   on the court, petitioner, respondent's attorney, and respondent
   (or respondent's GAL).
3. **The form attaches to the petition** rather than initiating a
   proceeding. Its case_dict comes from the underlying PP-201
   (guardian) or PP-401 (conservator) petition.

A pipeline that processes PP-505 must coordinate with the upstream
petition: the case_dict must already exist; the form's evaluator
name must come from a different source (e.g., a roster lookup)
than the standard party-record pattern.
