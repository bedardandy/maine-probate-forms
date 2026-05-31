# Upstream worklist (pipeline-gated)

Items found by the consistency/accuracy audits that **cannot be cleanly fixed in
this repo** because they live in the upstream detection/classification pipeline
(`trees/<form>.yaml` + the schema generator's post-build enrichment), which is not
vendored here. `build_form_schema.py` does not reproduce the shipped schemas (they
carry `_skill_metadata_override`, eval-driven risk, harvested `when:` conditions),
so adding/re-sourcing a field is a pipeline task, not a safe local hand-edit.

This file is the turnkey spec for that pass. Outputs are drafts — not legal advice.

## 1. Fill-source mis-classifications (hard facts routed to the LLM bucket)

A scan of all 79 schemas found **126** narrative-bucket (`llm_over_narrative`)
fields whose `field_id` is a hard fact. Categorized:

- **22 clean singular facts** — should be sourced from a record, not the LLM.
- **91 repeating-group** (`heir_N_address`, `interested_party_N_…`) — correctly
  narrative *today*; the case model has no per-row record. Fixing these needs a
  per-party list model upstream (out of scope for a re-source).
- **4 composite phrase** (`residence_and_date_of_death`, `putative_parent_likely_address`,
  `pregnancy_town_state`) — correctly narrative; a composed phrase, not a bare value.

**Runtime mitigation already shipped:** `fill_plan._rescue_from_records` resolves a
narrative field deterministically when the case supplies its *exact field_id* in a
`*_record` (e.g. DE-101 `decedent_date_of_death` ← `decedent_record`). So the
symptom (a present fact deferred to the LLM) is fixed at fill time for the well-named
fields. The upstream fix is still worth doing — it makes the schema's
`fill_strategy.source` honest and helps integrators who read the schema directly.

The 22 to re-source (`fill_strategy.source: llm_over_narrative` → the record key):

| Form | field_id | Note |
|---|---|---|
| DE-101 | `decedent_date_of_death` | ← `decedent_record`; rescued at runtime |
| DE-104 | `personal_representative_address` | ← PR record |
| DE-509 | `attorney_maine_bar_number` | ← `attorney_record.attorney_bar_number` |
| NC-001 | `minor_dob` | ← minor record |
| AD-026 | `adoptee_dob` | ← adoptee record |
| APP-1 | `appellant_address` | ← appellant record |
| PP-207 | `guardian_address` | ← guardian record |
| PP-402 | `conservator_address` | ← conservator record |
| AF-102, DE-301 | `date_of_death` | **unprefixed** — won't rescue (field_id ≠ record key `decedent_date_of_death`); also rename to a role-prefixed id |
| AF-105 | `date_of_birth` | unprefixed — same |
| N-107 | `declarant_address` | ← declarant record |
| PP-407 | `individual_current_address` | ← protected-person record |
| (remainder) | N-118 plan/court addresses, PP-509/510 transfer states | review: some are narrative *events* (a new dwelling address after a change), not stored attrs — confirm before re-sourcing |

## 2. Geometry coverage gaps

See `catalog/coverage_worklist.md` (already triaged + coordinate-verified):
- **New fields (need a tree node):** DE-301 notary appearance name; DE-504 & PP-409
  petitioner email lines.
- **Likely mis-bindings (fix with filled-render verification):** DE-403 officer
  name/authority (ambiguous label layout); DE-506 probate/augmented value (cascading
  `$`-blank shift).

## 3. Statute relevance — authoring refinements (not bugs)

`scripts/audit_statute_relevance.py` adjudicated 224 cited considerations:
**188 relevant, 31 tangential, 5 mis_tied** (see `catalog/vision_audit_findings.md`
sibling note; raw TSV is gitignored). The layer is sound — these are *over-broad*,
not *wrong*, citations worth a tightening pass:

- **Whole-title table-of-contents cites (tighten to a section):** AF-101 / NC-001
  `19-A M.R.S.` — cite the specific competing-jurisdiction section, not the title.
- **Over-broad "related to the form, not this field" sections (31 total):** e.g.
  CN-1 `reasons_for_change` → §1-701 (process, not substance); APP-1/APP-2
  `*_date` → §1-308 (right to appeal, not the date); DE-201 `will_date` → §2-503
  (self-proving formalities, not the date). Each: replace with a section bearing on
  the *specific* field, or drop.
- **5 mis_tied:** 4 are no-cite procedural notes (harness now skips them; no data
  change needed); AF-101.vA §5-105 was an audit misread (the cite is correct);
  DE-101 §2-102 on `non_registered_domestic_partner` is defensible (spousal-share
  context). No sidecar edits required.

## 4. Resolver residual — enum/select fields with a bare-name source

The fleet name-leak scan flags fields like `applicant_legal_interest` whose
`fill_strategy.source` is the bare record name (`applicant_record.applicant`). In a
well-formed case these resolve field_id-first (the case supplies
`applicant_legal_interest`), so the bare-name fallback never fires — the leak only
appears in a degenerate record holding *only* the name. Per the project's prior
decision, the `_NON_NAME_SUFFIXES` denylist is **not** extended open-endedly to
chase these (date suffixes were added because dates are a bounded, universal,
realistically-missing category). The clean fix is upstream: re-source these enum
fields off the bare name key (or mark the source as non-name).
