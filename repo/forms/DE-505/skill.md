---
form_id: DE-505
form_title: Petition with Respect to Pretermitted or Omitted Child
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 2-302 (Pretermitted children)"
  - "18-C M.R.S.A. § 2-301 (Pretermitted spouse — distinct, but companion law)"
  - "18-C M.R.S.A. § 3-401 (Formal testacy proceeding — vehicle when needed)"
filing_deadline_days: null
filing_deadline_anchor: "will_admission_date"
service_required: true
service_recipients: "all_devisees_and_personal_representative"
n_fields: 13
addendum_supported: true
addendum_target_fields:
  - "omitted_child_info"
  - "circumstances_existed"
  - "basis_facts"
parties:
  - petitioner
  - omitted_child
  - attorney
hand_authored: true
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant     | 3 | deterministic from case_dict |
| party_attr        | 3 | attorney record |
| narrative_derived | 6 | LLM — omitted child identity, circumstances, intent rebuttal, intestate-share prayer |
| signature         | 1 | wet-ink (petitioner) |

## Procedural context

DE-505 is the **pretermitted-child petition** under § 2-302. It is
filed when a child of the decedent was **omitted from the will**
and the petitioner (typically the child or their guardian) wants
to claim a § 2-302 intestate share against the testate estate.

### When does § 2-302 apply?

A child born to or adopted by the testator AFTER the will was
executed who was NOT provided for in the will is presumed to be
pretermitted. The pretermitted child takes an intestate share
UNLESS one of three rebuttals is shown by the will-proponents:

1. **Intent to omit** (§ 2-302(a)(1)): the will indicates the
   omission was intentional.
2. **Provision outside the will** (§ 2-302(a)(2)): the testator
   provided for the child by transfer outside the will and the
   intent that the transfer be in lieu of a testamentary provision
   is shown.
3. **Substantially all to other parent** (§ 2-302(a)(3)): the
   testator devised substantially all of the estate to the other
   parent of the pretermitted child.

The form's `omission_intent` field captures which (if any) of these
rebuttals is being asserted; `circumstances_existed` and
`basis_facts` document the underlying facts.

## Computed formulas

None: the intestate-share calculation is not done on this form
(it depends on the rest of the testate scheme and other heirs;
done downstream).

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| **`omitted_child_info` paraphrase** | LLM writes "decedent's son" instead of the child's full legal name + DOB + relationship | hand review — yellow tier |
| **`omission_intent` posture confusion** | LLM picks "intentional_omission" when narrative supports "no_rebuttal" (i.e., petitioner is asserting the child was genuinely pretermitted, no rebuttal exists) | TODO: value_in(intentional_omission, provided_outside_will, substantially_all_to_other_parent, no_rebuttal_asserted) — but need form-context verification |
| **`circumstances_existed` over-claim** | LLM invents a rebuttal that the case narrative doesn't support | hand review |
| **`intestate_share_prayer` ambiguity** | LLM writes "fair share" instead of "full intestate share" or "share equivalent to siblings" | TODO: value_in candidate; not yet encoded |
| **Wrong vehicle** | DE-505 is filed within an existing probate matter; if the will hasn't yet been admitted, the petitioner needs DE-301 (formal testacy) FIRST and may incorporate DE-505 by reference | pipeline-level gating |

## High-risk fields (1 yellow)

- `omitted_child_info` (yellow, 25): the substantive identification
  of the omitted child. Errors here cascade: wrong name → wrong
  party gets the share → will-construction litigation. Hand review
  mandatory.

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | county, docket_no, estate_of | drift from case dict |
| `data_type: phone/email/bar_number` | attorney_* | malformed contact |
| `data_type: date` | date | invalid date |

## Conditional writability

```yaml
# Recommended TODO additions (need form text verification):
omission_intent:
  validators:
    - "value_in(intentional_omission, provided_outside_will, substantially_all_to_other_parent, no_rebuttal_asserted)"

intestate_share_prayer:
  validators:
    - "value_in(full_intestate_share, equal_with_other_children, other)"
```

## Risk distribution

```
green:  12
yellow:  1
orange:  0
red:     0
```

## Sample case sketch

> Decedent: Robert F. Halliday, will executed 2014-03-22, died
> 2026-01-09. Will devises everything to spouse Helen, then to
> son Marcus.
> Petitioner: Carla Halliday, daughter born 2017-08-14 (after
> the will), not mentioned in the will. Filed by mother
> Eleanor Halliday as Carla's natural guardian.
> `omission_intent` = "no_rebuttal_asserted": petitioner asserts
> Carla was genuinely pretermitted; will is silent on her, no
> outside transfer, decedent did NOT devise substantially all to
> the other parent (Eleanor is not the spouse: Helen is).
> `circumstances_existed` recites the 2017 birth + the 2014 will
> date.
> `intestate_share_prayer` = "full_intestate_share": Carla seeks
> her § 2-301/2-302 intestate share (1/4 share if equal-with-Marcus
> after the spouse's elective portion).

## Why this form is procedurally distinctive

DE-505 is one of a small number of probate forms that **runs against
the will**, not with it. Most petitions seek to administer the
estate per the testator's intent; this one asserts a statutory
override of that intent. Downstream consumers must understand
that filing DE-505:
1. **Triggers § 2-302 service**: every devisee under the will
   must be served (the people whose shares may be reduced).
2. **May be combined with a will contest**: but is procedurally
   distinct from § 3-407 contests.
3. **Is time-sensitive**: though § 2-302 has no fixed deadline
   on its face, the petition must be filed before the estate
   closes; once DE-602 is filed and the year-after creditor
   window closes, the PR's authority ends and § 2-302 relief
   becomes much harder to obtain.
