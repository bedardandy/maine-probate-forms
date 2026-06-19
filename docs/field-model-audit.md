# Field-model audit & value guides

Beyond "is the rectangle in the right place" (`scripts/audit_form_geometry.py`),
this layer asks whether each form's **field model** is sound — every printed
question has a field, the right *kind* of field, named so answers don't collide,
with a sidecar that says exactly what value belongs there.

## Tools

| Command | What it does |
|---|---|
| `make questions` | `audit_form_questions.py` → `catalog/question_audit.json` |
| `make value-guides` | `build_value_guide.py` → `repo/forms/<ID>/value_guide.json` |
| `make value-guides-check` | `verify_value_guide.py` (freshness + calc integrity + advisories) |

Regression gates: `tests/test_question_audit_baseline.py` (actionable findings),
`tests/test_value_guides.py` (guide freshness + every formula resolves).

## Question / field-quality audit

`audit_form_questions.py` flags, per form:

- **uncovered_question** — a numbered printed prompt whose answer band has a
  blank rule but no fill widget (a question with no field). Corpus: 1.
- **text_on_signature_line** — a text/date widget sitting on a printed
  "Signature" rule (should be wet-ink / left blank). *Advisory* — a date or fee
  line legitimately sits above a signature, so these are flagged, not auto-edited.
- **symbol_splits_underline** — one widget spanning an interior printed `$` or
  `,`, which means two values share a blank that the form visually separates
  (e.g. `$____.__` dollars/cents, or `____ , 20__` day/month/year). Splitting
  these is a schema change → flagged for authoring.
- **duplicate_widget_name** — two widgets that would receive the *same* AcroForm
  name and overwrite each other. Corpus: **0** — the `field_id` / `field_id__N`
  naming guarantees distinct names for every widget answering the same question.
  The gate keeps it that way.
- **alignment_suggestion** — declared justification vs. a layout recommendation:
  currency → right (already correct everywhere), a *short mid-sentence blank*
  (printed text on both sides, < 130pt) → center, else left. The 88 left→center
  suggestions are the "short underline centered for symmetry" aesthetic — these
  are an opt-in worklist, not auto-applied.
- **underline_buffer** — a widget edge bleeding into an adjacent printed word
  with no gap (`____County`). 13 right-into-word bleeds were given a 2.5pt
  buffer; the comma case (`____, Maine`) is left tight to the comma by design.

## Value guides (`repo/forms/<ID>/value_guide.json`)

A consumable, per-field projection of `schema.json` enriched with the value
specifics the schema leaves implicit. Each field gets `data_type`, `alignment`,
`required`, `expects`, and where applicable:

- **`format`** — `decimal`, `YYYY-MM-DD or MM/DD/YYYY`, `YYYY` (calendar year),
  `ZIP5 or ZIP+4`, `NNN-NNN-NNNN`.
- **`address_components`** — `[street, city, state, zip]` (or `[street]` for
  street-only labels), plus `state_format`: *2-letter USPS (ME), not "Maine"*.
- **`currency`** — `{min, decimals}`; value carries digits only, no `$`.
- **`calculated`** — the formula, mode, and `validate: recompute from inputs and
  compare; do not accept a free-typed total`.
- **`avoid`** — adversarial, plausible-but-wrong values to reject: a future date
  for a DOB/death, `$` inside a currency box, a value spanning a printed comma/
  `20__` stub, the state spelled out, a missing ZIP, the trailing word "County".

`verify_value_guide.py` is adversarial about under-specification:

- **calculation integrity** (hard gate): every `{op:field,id}` in a formula must
  reference a real field — corpus: **0 broken**, so all derived totals are
  recomputable.
- **under-typed text advisory** (`catalog/value_guide_advisories.json`): text
  fields whose label implies a concrete type but are typed generic `text` —
  62 corpus-wide (e.g. `date_of_death`, `date_of_birth`, docket/phone/email/year
  fields). These are an authoring worklist: promoting them to `date`/`currency`/
  etc. lets the guide be specific and unlocks format/range checking. Left as
  advisories here rather than bulk-rewriting schemas.

## Worklists this surfaced

- **Promote under-typed fields** (62): the date-of-death / date-of-birth / docket
  fields typed as `text` are the highest value — they should be `date` /
  `docket_number` so the guide and any validator can enforce format and "no
  future date".
- **Split symbol-broken blanks** (2): AF-105 `medical_support_total` over a `$`,
  MISC-102 `produce_time` over a `,`.
- **Center short mid-sentence blanks** (88, opt-in aesthetic).
- **Review** the 2 text-on-signature and 1 uncovered-question flags against the
  official form.
