---
form_id: DE-509
form_title: Petition for Removal of Personal Representative
form_revision: "7-1-19"
jurisdiction: Maine
court: Probate
filer_role: petitioner
statutes:
  - "18-C M.R.S.A. § 3-611 (Termination/removal of PR)"
filing_deadline_days: null
service_required: true
service_recipients: "personal_representative_and_interested_persons"
n_fields: 34
addendum_supported: true
addendum_target_fields:
  - "grounds_for_removal"
  - "interested_parties_address_*"
parties:
  - petitioner
  - pr_to_remove
  - attorney
  - interested_parties (1..7, repeating)
slot_groups:
  - interested_parties
---

## Pipeline routing

| category | n | path |
|---|---|---|
| case_constant | 3 | deterministic from case_dict |
| party_attr | 7 | deterministic from petitioner + PR + attorney records |
| narrative_derived | 22 | LLM (incl. interested-parties slots + grounds narrative) |
| signature | 2 | wet-ink |

## Known LLM failure modes (May-2026 eval)

The `interested_parties_*_<N>` table extends to 7 slots: slots 6-7 are
where Qwen duplication shows up.

| symptom | example | guard |
|---|---|---|
| late-slot duplication | `interested_parties_name_7` ↔ earlier names | `dedupe_within(interested_parties_name)` |
| `grounds_for_removal` paraphrase loss | Qwen abstracts specific facts ("misconduct") instead of preserving them ("commingled estate funds with personal account in March 2026") | hand review (semantic, no automated guard) |
| PR-to-remove name vs petitioner name swap | rare but happens when both share a surname | category=party_attr with explicit party labels reduces but doesn't eliminate |

## High-risk fields (red tier)

| field | score | reasons |
|---|---|---|
| `interested_parties_name_7` | 100 | wrong 2/5 |
| `interested_parties_address_7` | 100 | wrong 2/5 |

## Validators

| validator | applies to | catches |
|---|---|---|
| `dedupe_within(interested_parties_name)` | _name_1..7 | duplicate party names |
| `dedupe_within(interested_parties_address)` | _address_1..7 | duplicate addresses |
| `nonempty_if_desc` | _address_*, _relationship_* | orphan rows |
| `populate_from_case_dict` | docket_no, county | drift |

## Conditional writability

None encoded. `grounds_for_removal` is required by statute but the
form doesn't have a checkbox gating it.

## Risk distribution

```
green:  ~12
yellow: ~17
orange: ~3
red:    2
```
