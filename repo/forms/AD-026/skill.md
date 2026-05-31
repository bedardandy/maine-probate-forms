---
form_id: AD-026
form_title: Petition for Adult Adoption
form_revision: "11-03-24"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-A M.R.S.A. § 9-301 (Adult adoption procedure)"
  - "18-A M.R.S.A. § 9-315 (Consent to adoption)"
filing_deadline_days: null
service_required: true
service_recipients: "adoptee_birth_parents_if_known"
n_fields: 51
addendum_supported: true
addendum_target_fields:
  - "adoptee_other_names"
  - "proposed_current_new_name"
  - "birth_parents_inheritance_request"
  - "relationship_to_adoptee"
parties:
  - petitioner
  - co_petitioner
  - adoptee
  - notary
---

> ⚠️ **Statute references are experimental and AI/LLM-generated — not legal advice.** The statute cites in this file identify the form's legal basis and issues to consider; the per-field statute and case-law considerations (`docs/statute-reference/`) are model annotations, not attorney-reviewed, and may be wrong. Verify against current law.

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 4 | deterministic from case_dict |
| party_attr | 15 | deterministic from petitioner + co-petitioner + adoptee + notary records |
| narrative_derived | 27 | LLM (adoptee history, name changes, inheritance preferences) |
| legal_choice | 2 | human — name-change election, inheritance election |
| signature | 3 | wet-ink (petitioner + co-petitioner + notary) |

## Procedural context

Petition to adopt an adult (typically a stepchild who has reached
majority, or an adult who is being formally recognized in the
adopter's family). The adoptee must consent. The form captures:

- Adoptee's birth name, current legal name, and any other names used
- Proposed new name (if changing)
- Whether the adoptee will inherit through birth parents (rare;
  default is that adoption severs birth-parent inheritance)
- Petitioner / co-petitioner contact info and relationship to adoptee

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| `adoptee_birth_name` vs `adoptee_other_names` mixup | LLM puts current legal name in birth name field | hand review (semantic) |
| `proposed_new_birth_name` confusion | "new birth name" means name to appear on amended birth certificate; LLM treats as alt-spelling | hand review |
| `dhhs_certificate_attached` over-claim | LLM checks the box when narrative doesn't confirm | category=legal_choice |
| `birth_parents_inheritance_request` over-confidence | LLM picks "yes" because the adoptee is mentioned by birth parents in narrative | category=legal_choice; default is "no" |
| `relationship_to_adoptee` paraphrase | "stepfather" vs "step-parent of 18 years" | hand review |
| `unlabeled_widget_after_email` | a glitch field on the PDF; should always be blank | flag as external/court-fill |

## High-risk fields (yellow tier: 16 fields)

Most are party-attr fields with composite addresses or name spellings
that can drift.

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | adoptee_name_caption, petitioner_caption, co_petitioner_caption | drift |
| `data_type: person_name` | adoptee_birth_name, proposed_new_birth_name | non-name text |
| `data_type: address` | adoptee_legal_residence, adoptee_mailing_address | malformed |
| `data_type: phone` | adoptee_telephone | invalid |

## Conditional writability (TODO)

```yaml
# Encode in classifications.yaml when verified:
proposed_current_new_name:
  writable_when:
    all_of:
      - field: <name_change_requested_flag>  # exact field TBD
        equals: true
```

## Risk distribution

```
green:  ~33
yellow: 16
orange: 0
red:    0
```
