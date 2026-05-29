---
form_id: NC-001
form_title: Petition for Name Change of Minor
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 1-701 (Name change — minor)"
  - "M.R. Prob. P. 17"
filing_deadline_days: null
service_required: true
service_recipients: "parents_or_legal_guardians_and_minor_if_14_plus"
n_fields: 35
addendum_supported: true
addendum_target_fields:
  - "reason_for_change"
  - "provided_documents"
parties:
  - petitioner
  - copetitioner
  - minor
  - attorney
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 21 | deterministic from petitioner + copetitioner + minor + attorney records |
| narrative_derived | 8 | LLM over reason narrative + documentation list |
| signature | 3 | wet-ink (petitioner + copetitioner + notary) |

## Procedural context

A parent or guardian petitions the court to change the legal name of a
minor. The form requires the minor's current legal name, the desired
new name (first/middle/last as separate fields), and a narrative
explaining the reason. If both legal parents/guardians are filing,
copetitioner is completed; otherwise the single-parent path applies
with documentation explaining why only one is petitioning.

## Known LLM failure modes (May-2026 eval)

| symptom | example | guard |
|---|---|---|
| current vs new name swap | LLM populates `current_legal_name` with the desired new name | hand review (semantic) |
| `desired_*_name` part confusion | LLM splits "Sarah Marie" as desired_first="Sarah Marie" instead of first="Sarah" middle="Marie" | hand review |
| copetitioner false-fill | LLM fills copetitioner block when narrative supports single-petitioner | conditional writability (TODO) |
| `reason_for_change` paraphrase loss | LLM abstracts the specific reason ("matches stepfather's surname after step-parent adoption") into generic ("family unity") | hand review |
| `provided_documents` over-claim | LLM checks documents that weren't actually attached | hand review |

## High-risk fields (orange tier)

| field | score | reasons |
|---|---|---|
| `copetitioner_*` (5 fields) | 35-50 | conditional fill risk |
| `reason_for_change` | 35 | semantic preservation risk |
| `current_legal_name` vs `desired_*_name` | 35 | swap risk |

## Validators

| validator | applies to | catches |
|---|---|---|
| `populate_from_case_dict` | probate_county, probate_docket_number, district_docket_number | drift |
| `data_type: person_name` | minor_name, new_minor_name, *_first_name, etc. | non-name text |
| `data_type: address` | petitioner_address, copetitioner_address | malformed |
| `data_type: phone` | copetitioner_phone | invalid |
| `data_type: email` | copetitioner_email | invalid |

## Conditional writability (TODO)

```yaml
# Encode in classifications.yaml when verified against the PDF layout:
copetitioner_*:
  writable_when:
    all_of:
      - field: <copetitioner_present_flag>  # may not exist as a separate field
        equals: true
```

The form likely has a "single petitioner" vs "joint petition" implicit
choice driven by whether the copetitioner block is filled at all.

## Risk distribution

```
green:  ~22
yellow: ~10
orange: 3
red:    0
```
