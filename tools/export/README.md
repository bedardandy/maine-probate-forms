# Export forms into templating / case-management systems

Turn a form package (`repo/forms/<ID>/schema.json` + `fill_geometry.json`) into
the import artifacts that popular case-management and document-templating systems
ingest. The full conceptual guide — which paradigm each vendor uses and what maps
where — is in [`docs/templating-integration.md`](../../docs/templating-integration.md).
This README is the tool reference.

```bash
python3 tools/export/export_form.py --form DE-101 --target all --out out/DE-101
```

## What it emits, per paradigm (`--target`)

| target | files | for |
| --- | --- | --- |
| `interchange` | `template.xfdf`, `data_dictionary.csv`, `case_schema.json` | any AcroForm-aware system, ETL, validation — **vendor-neutral** |
| `esign` | `docusign_template.json`, `pandadoc_fields.json` | DocuSign, PandaDoc, Adobe Sign — **coordinate field placement** |
| `docassembly` | `variables.json`, `merge_tokens.csv`, `logic.md` | Clio, MyCase, HotDocs — **merge-field doc assembly** |
| `gavel` | `gavel_variables.json` | Gavel / Documate — **legal no-code interview** |
| `all` | all of the above (one subdir each) | |

## How it works

`model.py` normalizes the raw schema into vendor-neutral fields with:

- a **canonical type** (`string`/`date`/`currency`/`boolean`/`choice`/`signature`/…),
- a **data binding** derived from `fill_strategy.source` — every field lands in
  one of: `data` (`case_dict.*` / `*_record.*`), `computed`
  (`recompute_from_dependencies`, carries a formula), `narrative`
  (`llm_over_narrative`), `signature` (`wet_ink`), `manual`
  (`human_decision`/`left_blank`), or `routing` (`triage`),
- readable **conditional logic** from `writable_when`/`required_when`
  (`{all_of|any_of: [{field, equals}]}`) and **formula** expressions from the
  `add`/`sub`/`field`/`sum_slot` op-tree.

`exporters.py` renders that model. Per-vendor specifics are **type tables**, not
logic — adding a system is a table edit. The data binding is what lets each
paradigm take the right slice: e-sign systems get every placed widget (incl.
signatures as signer tabs); doc-assembly and schema exports get only the
mergeable `data`/`computed`/`narrative` variables and leave `signature`/`manual`
fields to a person.

## Key on `field_id`, not detected widget names

Every artifact keys on the curated `field_id` (and `fill_pdf.py` names AcroForm
widgets by `field_id`), so an XFDF or a DocuSign `tabLabel` lines up with the
fillable PDF this repo produces. See [`docs/integrating.md`](../../docs/integrating.md).

## Validating the export

Two validators ship with the layer — one needs no account, one needs a free
developer sandbox.

```bash
# 1. interchange round-trip — no account, offline. Checks template.xfdf is
#    well-formed, the field set is consistent across XFDF / CSV / case_schema,
#    then builds an AcroForm named by field_id, imports a populated XFDF into it,
#    and confirms every value lands on the right field.
python3 -m tools.export.validate_interchange            # all 79 forms + examples
python3 -m tools.export.validate_interchange --form DE-101 --fetch   # vs the real blank

# 2. DocuSign coordinate placement — needs a free sandbox
#    (https://developers.docusign.com). --dry-run builds + validates the envelope
#    offline; with a token it creates a DRAFT (nothing emailed) you open in the
#    sandbox console to eyeball tab placement.
python3 -m tools.export.docusign_sandbox_test --form DE-101 --dry-run
export DOCUSIGN_ACCOUNT_ID=... DOCUSIGN_ACCESS_TOKEN=...
python3 -m tools.export.docusign_sandbox_test --form DE-101
```

The interchange validator is the cheapest high-value check — it exercises the
`field_id` keying every other paradigm reuses. The DocuSign sandbox is the only
way to confirm the geometry → tab-coordinate mapping renders correctly; do it
once before relying on the `esign` artifacts.

## Caveats

- **Coordinate origin.** DocuSign/PandaDoc payloads place fields by page + (x, y)
  in points from the **top-left** (the schema's convention). Confirm against your
  account's current API version before production — origin conventions and field
  keys drift between API releases.
- **Merge-token syntax is a suggestion.** `merge_tokens.csv` proposes Clio
  (`{{Matter.Custom.<token>}}`) and MyCase (`[[<token>]]`) tokens; the exact
  namespace depends on how you create the custom fields. The tool gives you the
  variable name, type, and the conditional/computed logic — you wire them to your
  matter's field namespace.
- **Doc-assembly systems build their own document.** Clio/MyCase/HotDocs/Gavel
  merge your data into a template you author; they do not fill Maine's official
  PDF. Use the `esign`/`interchange` artifacts if you need the official PDF
  populated.

> **Not legal advice.** Output is a draft mapping to verify against the official
> form and your system's import format.
