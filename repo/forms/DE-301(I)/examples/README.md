# DE-301 Examples

Worked example for DE-301 (Petition for Formal Probate of Will and
Appointment of PR). Demonstrates the **multi-party formal petition**
pipeline pattern: 5 distinct parties with separate *_record blocks
in the case data, plus cross-field consistency validation via
`equals_field` rules.

## Files

| file | role |
|---|---|
| `case.example.json`   | Common case: applicant **=** appointee (petitioner self-nominates as PR). Will found, applicant nominated in will. |
| `filled.example.json` | Expected fill (applicant=appointee). |
| `case.split.json`     | Split case: applicant **≠** appointee (petitioner asks court to appoint a corporate trustee). Will's named PR predeceased. |
| `filled.split.json`   | Expected fill (split). Demonstrates the validators still trace signature/notary to the applicant, NOT the appointee. |

## Pipeline pattern: multi-party formal petition

DE-301 is the **canonical multi-party form**. It surfaces five party
roles that downstream consumers must map distinctly:

1. **applicant** — the person filing the petition (typically the
   would-be PR)
2. **appointee** (`person_whose_appointment_is_sought`) — the
   person being proposed as PR. In ~90% of cases, same as applicant.
   But the form has separate fields and the pipeline must fill
   both.
3. **decedent** — the deceased; their record drives the case caption
   and several narrative fields.
4. **attorney** — the lawyer representing the applicant. Contact
   fields (phone, email, bar_number) have data_type validators.
5. **notary** — the notary public who takes the applicant's
   acknowledgement. Notary identity isn't pre-populated; it's
   captured at the notarial appearance event.

## What this case exercises

| dimension | mechanism |
|---|---|
| **Five-party fill** | Each party has a distinct `<party>_record` block in case data; the schema's `fill_strategy.source` routes each field to the right block |
| **applicant=appointee duplication** | `applicant_full_legal_name` and `person_whose_appointment_is_sought` both contain the same name — the pipeline writes the same value to both fields, not a paraphrase |
| **Cross-field consistency** | `equals_field(applicant_full_legal_name, relaxed)` rules on `applicant_signature_name` AND `notary_appearer_name` — both must match the applicant's full legal name |
| **Temporal consistency** | `date_order(date_of_death, >)` on `applicant_signature_date` — applicant signs AFTER the decedent dies (obvious but worth encoding) |
| **Yes/no narrative chain** | Three sequential legal_choice booleans: `domiciled_in_county` → `will_presented_for_probate` → `nominated_as_pr_in_will`. Each `=yes` opens the standard testate-PR path |
| **Conditional sub-form** | `special_admin_acceptance_*` fields are LEFT NULL when special_admin_requested=false (no § 3-614 special administrator) |
| **Notary acknowledgement** | `notary_appearer_name` must equal the applicant's name — the applicant is the affiant appearing before the notary |
| **Composite field** | `applicant_address_email_phone` is a single text field containing three pieces of info joined; format as "address / phone / email" |

## Why this is the testate counterpart to DE-101

| DE-101 (intestate) | DE-301 (formal testate) |
|---|---|
| Single applicant, single PR | Same parties but split into separate fields |
| `testamentary_instrument` = `external` (blank) | `will_details` is filled with will metadata |
| Heirs in free-text paragraph | Heirs implicit (named in will) |
| Informal probate (§ 3-301) | Formal probate (§ 3-401) |
| No will_* fields | `will_presented_for_probate`, `will_details`, `nominated_as_pr_in_will` |

Use DE-101's example for the intestate path; use DE-301's example
when a will is present.

## What case.split.json adds vs case.example.json

The split case exercises the central distinction the pipeline can
easily miss: **applicant and appointee are different people**.

| dimension | example (common) | split |
|---|---|---|
| applicant_full_legal_name | Margaret L. Crawford-Hines | Rebecca M. Tilton |
| person_whose_appointment_is_sought | Margaret L. Crawford-Hines | Bath Savings Trust Company, by James L. Hartwell, Trust Officer |
| applicant=appointee? | yes (~90% of cases) | no |
| applicant_signature_name | matches applicant | **still** matches applicant (Rebecca) — applicant signs even when appointee is different |
| notary_appearer_name | matches applicant | **still** matches applicant — applicant appears before notary |
| nominated_as_pr_in_will | yes | no |

The validators are designed to trace **who signs** (applicant) and
**who serves** (appointee) as DIFFERENT axes. The split case
demonstrates this distinction. If a pipeline naively copied
`applicant_full_legal_name` into `person_whose_appointment_is_sought`
on every fill, the split case would have the sister appointed as
PR instead of the corporate trustee — a serious error the
validator does not catch (because there's no rule against it).
Hand review must flag applicant=appointee vs split BEFORE fill.

## What still isn't exercised

- **applicant ≠ appointee NATURAL person** — case.split.json uses
  a corporate trustee. A second split case could use an attorney
  appointee (still a natural person, different from the petitioner).
- **Out-of-county venue** — `domiciled_in_county=no` triggers
  `venue_basis_if_not_domiciled` (which has its own narrative).
- **No will found** — `will_presented_for_probate=no` is a posture
  inconsistent with the form's purpose; the case should be re-routed
  to DE-101 (intestate) or DE-201 (informal probate of will found
  later).
- **Will exists but applicant not named PR** — `nominated_as_pr_in_will=no`
  triggers priority disputes that may need a competing § 3-203
  priority statement.
- **Special administrator path** — `special_admin_acceptance_*`
  fields are null here. A separate example should exercise that
  branch (where an emergency PR is appointed before formal probate
  resolves).
- **Co-petitioners** — two heirs jointly petitioning (e.g. siblings
  splitting PR duties) isn't shown.

## See also

- `../schema.json` — full field schema including `equals_field` and
  `date_order` validators
- `../skill.md` — hand-curated narrative with party-mapping guidance
- `../classifications.yaml` — `equals_field` and `date_order` rule
  declarations
- `../../DE-101/examples/` — intestate counterpart (flat form, no will)
- `../../PP-406/examples/` — slot-table + formulas pattern
- `../../PB-007/examples/` — conditional writability pattern
