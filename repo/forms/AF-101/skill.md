---
form_id: AF-101
form_title: Jurisdictional Affidavit
form_revision: "03-01-2025"
jurisdiction: Maine
court: Probate
filer_role: affiant
statutes:
  - "18-C M.R.S.A. § 1-303 (Probate Court jurisdiction)"
filing_deadline_days: null
service_required: false
n_fields: 21
addendum_supported: true
parties:
  - affiant
  - petitioner
  - minor_child
  - notary
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 6 | deterministic from case_dict |
| party_attr | 4 | deterministic from affiant + notary + petitioner records |
| narrative_derived | 8 | LLM over residency / jurisdiction facts |
| signature | 3 | wet-ink (affidavit jurat) |

## Known LLM failure modes (May-2026 eval)

Compact form: most risks are around the notary jurat block where
Qwen sometimes confuses the affiant's name with the notary's name.

| symptom | example | guard |
|---|---|---|
| jurat name confusion | `notary_petitioner_name` (the affiant who appeared) filled with the notary's name | category=party_attr (notary vs affiant); structural validator can't fully prevent — verify against witness signature |
| `oath_no_pending_*` over-confidence | LLM checks "no pending case in district court" when narrative is silent | category=legal_choice |
| `minor_child_name` for non-minor cases | LLM fills child name when the form is for adult guardianship | hand review |

## High-risk fields (red tier)

| field | score | reasons |
|---|---|---|
| `notary_petitioner_name` | 100 | wrong 3/5 — name confusion in jurat |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | docket variants, county, court_name | drift |
| `data_type: person_name` | notary_*_name fields | non-name text |

## Conditional writability

```yaml
# Encode in classifications.yaml:
minor_child_name:
  writable_when:
    all_of:
      - field: jurisdictional_inquiries
        # Inquiries field implies minor-guardianship context;
        # exact PDF widget binding TBD
```

## Risk distribution

```
green:  ~12
yellow: ~7
orange: 1
red:    1
```
