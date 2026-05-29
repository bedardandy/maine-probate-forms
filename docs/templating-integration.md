# Importing these forms into case-management & templating systems

How to bring a Maine probate form — its fields, types, conditional logic, and
computed values — into Clio, MyCase, PandaDoc, DocuSign, HotDocs, Gavel/Documate,
or any AcroForm-aware system. Pair with [`integrating.md`](integrating.md) (the
per-form data contract) and the reference exporters in
[`tools/export/`](../tools/export/README.md).

> **Not legal advice.** Everything here is a draft mapping to verify against the
> official form and your system's current import format.

## The schema already carries what these systems need

Each `repo/forms/<ID>/schema.json` gives every field a stable `field_id`, a human
`label`, a `type`/`data_type`, choice groups, conditional logic
(`writable_when` / `required_when`), computed `formula`s, and a
`fill_strategy.source` saying where its value comes from. `fill_geometry.json`
adds vision-audited widget rects (page + `[x0,y0,x1,y1]`, top-left origin). That
is enough to drive every paradigm below.

The one move that unlocks clean mapping: **`fill_strategy.source` partitions
every field** into

| binding | source prefix | goes to |
| --- | --- | --- |
| `data` | `case_dict.*`, `*_record.*` | merge variables / form fields |
| `computed` | `recompute_from_dependencies` (+ `formula`) | a calculation/computation |
| `narrative` | `llm_over_narrative` | a free-text (often multiline) variable |
| `signature` | `wet_ink` | a signer tab — never data-merged |
| `manual` | `human_decision`, `left_blank` | left for a person |
| `routing` | `triage` | not a form field |

`*_record.*` sources name the party role (`applicant_record.full_name` →
role `applicant`, key `full_name`), so the data dictionary groups cleanly by
party.

## Three paradigms (pick by what your system actually ingests)

### 1. Standard interchange — vendor-neutral, lowest maintenance

`--target interchange` emits:

- **`template.xfdf`** — standard PDF form-data XML keyed by `field_id`. Because
  `fill_pdf.py` names AcroForm widgets by `field_id`, this populates the fillable
  PDF this repo produces; any AcroForm tool round-trips it.
- **`data_dictionary.csv`** — the flattened contract: every field with canonical
  type, party role, merge token, binding kind, conditional/required/formula
  expressions, risk tier. This is the sheet you hand an integrator.
- **`case_schema.json`** — a JSON Schema for the inbound *case-data object*
  (types, enums from choice groups, `required`, and `if/then` for conditional
  requirements). Validate your data before you fill.

Use this when you have an ETL/integration layer of your own, or a system that
just needs typed fields and data.

### 2. Coordinate field placement — DocuSign, PandaDoc, Adobe Sign

These take a PDF and place fields by page + (x, y); they do **not** run branching
logic. `--target esign` uses `fill_geometry.json` to emit:

- **`docusign_template.json`** — one `Filer` signer with `textTabs` /
  `checkboxTabs` / `dateTabs` / `signHereTabs` placed by `pageNumber` +
  `xPosition`/`yPosition`, `tabLabel = field_id` (so you can prefill by label).
  `wet_ink` fields become `signHereTabs`; `computed` fields are locked.
- **`pandadoc_fields.json`** — a fields payload for the uploaded-PDF field API,
  `merge_field = field_id`, signatures assigned to a `signer` role, data to a
  `filer` role.

Confirm the coordinate origin against your account's API version (we assume
top-left points). Branching logic from the schema does not transfer — enforce it
upstream (or use paradigm 3 to drive the data).

### 3. Merge-field doc assembly — Clio, MyCase, HotDocs, Gavel/Documate

These merge your data into a template you author and **can** enforce conditional
logic and computations — but they generate their own document, not Maine's PDF.
`--target docassembly` (and `--target gavel`) emit:

- **`variables.json`** — typed variables (data + computed + narrative only;
  signatures/manual excluded), each with `show_when`, `required_when`,
  `formula`, and choice `options`.
- **`merge_tokens.csv`** — per-vendor type + token map: Clio custom-field type +
  `{{Matter.Custom.<token>}}`, MyCase `[[<token>]]`, HotDocs variable + type,
  Gavel type. Create the custom fields/variables from this, then drop the tokens
  into your DOCX/template.
- **`logic.md`** — the conditional-visibility, conditional-requirement, and
  computed rules in readable form, ready to re-express as HotDocs `IF`/computation,
  Gavel show-if/calculation, or Clio/MyCase template conditionals.
- **`gavel_variables.json`** — the same, shaped for a Gavel/Documate interview
  (typed variables + `show_if` + `calculation` + `choices`).

For the official PDF *plus* assembled logic, combine: drive data collection and
branching in the doc-assembly tool, then fill the Maine PDF via the
`interchange`/`esign` artifacts (or this repo's `fill_pdf.py`).

## Worked example

```bash
# everything for one form, one folder per paradigm
python3 tools/export/export_form.py --form DE-101 --target all --out out/DE-101

# just the e-sign payloads
python3 tools/export/export_form.py --form N-118 --target esign --out out/N-118
```

`N-118` (a guardian/conservator status report) shows conditional logic surviving
the trip: its `logic.md` lists ~26 fields that show only when their section
checkbox is set (`appointment_of_guardian_court_name ⟸ appointment_of_guardian ==
true`). `DE-405` (inventory) shows computed totals
(`calc_net_inventory = (calc_gross_inventory - calc_gross_real_encumbrances -
calc_gross_personal_encumbrances)`).

## What does and doesn't transfer

| | interchange | e-sign | doc assembly |
| --- | --- | --- | --- |
| field names + types | ✅ | ✅ | ✅ |
| accessible labels | ✅ | ✅ | ✅ |
| coordinate placement | n/a | ✅ (from geometry) | n/a |
| conditional show/require | documented | ✗ (enforce upstream) | ✅ |
| computed fields | documented | locked field | ✅ |
| fills the official Maine PDF | ✅ | ✅ | ✗ (its own doc) |
| signatures | excluded from data | signer tabs | excluded from data |

Pick interchange for neutrality, e-sign to populate + route the official PDF, and
doc assembly when you want the branching interview and don't need Maine's exact
PDF.
