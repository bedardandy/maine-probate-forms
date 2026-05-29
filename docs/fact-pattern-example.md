# Worked example: fact pattern → fill plan

A concrete pass through `docs/agent-workflow.md` for **DE-101** (Application for
Informal Probate / Appointment — Intestate).

## The fact pattern (what a user might say)

> "Sarah Walsh-Bennett's mother, Margaret L. Walsh of Falmouth, died March 18,
> 2026 without a will. Sarah is the sole surviving child and wants to be
> appointed personal representative. Their attorney is Patricia Goff. Probate is
> in Cumberland County."

## 1–2. Route + read

```bash
python3 tools/find_forms.py "informal probate intestate estate"
#   DE-101 ...  (read repo/forms/DE-101/skill.md + metadata.json)
```

## 3. Canonical fact object (`case.json`)

```json
{
  "matter": { "court_county": "Cumberland", "filing_date": "2026-04-02" },
  "parties": {
    "applicant": { "full_name": "Sarah J. Walsh-Bennett", "address": "47 Pine Hill Road",
                   "city": "Falmouth", "state": "ME", "zip": "04105", "legal_interest": "heir" },
    "decedent":  { "full_name": "Margaret L. Walsh", "date_of_death": "2026-03-18",
                   "domicile": "82 Falmouth Foreside Way, Falmouth, ME 04105" },
    "attorney":  { "name": "Patricia M. Goff", "bar_number": "4271",
                   "address": "200 Commercial Street, Portland, ME 04101" }
  },
  "party": { "full_name": "Sarah J. Walsh-Bennett", "address": "47 Pine Hill Road",
             "city": "Falmouth", "state": "ME", "zip": "04105" },
  "facts": { "relationship_to_decedent": "Daughter and sole surviving child." }
}
```
Anything the fact pattern doesn't state is left out; it surfaces as `unresolved`.

## 4. Plan the fill

```bash
python3 tools/fill_plan.py --form DE-101 --case case.json
# DE-101: 83 fields — resolved 15, narrative 52 (agent fills), blank 16, unresolved 0
```
`resolved` already holds `county_probate_court`, the caption, applicant and
attorney blocks, decedent details. The **52 `narrative` fields** are the
worklist *you* compose from the fact pattern (e.g. `applicant_contact_info`,
heir/asset narratives). Write them, fold them back under
`narrative_facts[field_id]`, and re-run to grow `resolved`. The **16 `blank`**
fields are signatures and human elections (bond, demand-for-notice).

## 5. Write the filled PDF

Fetch the flat form from `metadata.json.source_url`, then:
```bash
python3 tools/fill_pdf.py --form DE-101 --case case.json \
    --source "DE-101 (flat).pdf" --out DE-101.filled.pdf
# text_written: 16 | options_checked: 1   (e.g. legal-interest "heir" checked)
```
`repo/forms/DE-101/fill_geometry.json` carries the widget rects, so values land
directly on the flat source, no pipeline needed.

## 6. Report

Report what resolved, the narrative fields you authored, any missing facts, and
the form's risk tiers, all to be reviewed before filing. **Not legal advice.**
