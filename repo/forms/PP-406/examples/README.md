# PP-406 Examples

Worked example showing the inputs and expected outputs for PP-406
(Inventory for Conservatorship). Demonstrates the **slot-table +
formulas** pipeline pattern: the LLM fills repeating slot rows from
narrative facts, then a deterministic formula step computes the
section subtotals.

## Files

| file | role |
|---|---|
| `case.example.json`    | Synthetic conservatorship case (happy path — 2 real-property rows fit in 6 slots). |
| `filled.example.json`  | Expected fill. Subtotals pre-computed for verification. |
| `case.overflow.json`   | Synthetic case where narrative has **8 real-property entities** but form has only 6 slots — exercises the addendum-overflow path. |
| `filled.overflow.json` | Expected fill (overflow path). Highest-value entries pack the 6 slots; lower-value remainders go on an addendum. |

## What this case exercises

This case is the canonical **slot-table happy path**:

- **3 asset sections** map to 3 slot groups
  (`real_prop`, `tang_prop`, `int_prop`).
- **Subtotal arithmetic** is verified end-to-end:
  ```
  gross_value_real_property      = 287500 + 28000          = 315500
  gross_value_personal_property  = sum(tang) + sum(int)     = 32450 + 225813.25 = 258263.25
  calc_gross_inventory           = gross_real + gross_pers  = 573763.25
  ```
  All six computed totals (gross_value_*, calc_gross_*) come from
  `../formulas.yaml`.
- **Encumbrance handling**: real property has a mortgage (92400),
  personal property has none. Tests that the
  `gross_value_*_encumbrances` formulas pull from the right slots.

## Pipeline walkthrough

A downstream consumer using this case should:

1. **Map narrative slots → form slots.**
   `narrative_facts.real_property[0]` → `real_prop_1_{desc,value,enc}`,
   `narrative_facts.tangible_personal_property[0]` →
   `tang_prop_7_{desc,value,enc}`, etc. The LLM is responsible for
   this mapping plus paraphrasing description text.

2. **Leave unused slots blank.**
   The form has 6 real-property rows but the case has 2; rows 3-6
   must be `null`, not padded with `"None"` or `"$0"`. (The
   classifier emits `nonempty_if_desc` on value/enc fields — they
   must be empty if `desc` is empty.)

3. **Skip subtotal fields during LLM fill.**
   `gross_value_*` and `calc_gross_*` are `category: computed`.
   Their `fill_strategy.source = recompute_from_dependencies`. The
   pipeline must compute them via `../formulas.yaml` *after* the
   slot rows are filled, then write them into the PDF.

4. **Validate.**
   `scripts/validate_filled.py` runs:
   - `dedupe_within(real_prop_desc)` — each row's description is unique
   - `cross_section_dedupe(int_prop_desc, tang_prop_desc)` — descriptions
     don't repeat across sections (catches LLM copying a row across
     groups)
   - `nonempty_if_desc` — every value/enc cell is empty iff its
     desc cell is empty
   - `recompute_from_dependencies` — every computed cell equals
     the formula output, with a 0.5% tolerance for rounding

## What case.overflow.json adds

The overflow case demonstrates the **slot-saturation** pipeline pattern:
when narrative has more entities than the form has slots, pack the
highest-value entries into the slots and route the rest to an addendum
sheet. Key takeaways encoded there:

- **Packing strategy**: 6 slots = 6 highest-value entries; the two
  lowest-value entries (1/4 cottage interest, marina slip) go on the
  addendum.
- **Subtotal tension**: the validator's `recompute_from_dependencies`
  computes the subtotal from in-form slots only ($1,105,000), but the
  legally-correct gross value includes the addendum rows ($1,155,500).
  This is a **known limitation** of the current formula DSL — flagged
  in `case.overflow.json`'s `_open_question` and surfaced in
  `KNOWN_GAPS.md`.
- **Addendum reference**: the last filled slot's description tags
  "SEE ADDENDUM" so downstream consumers know to look beyond row 6.

## What this case does NOT exercise

- **Negative-equity asset**: an encumbrance value greater than the
  asset value (legally permissible, e.g. underwater mortgage on a
  property the conservator is choosing to retain).
- **Joint title**: real estate held jointly with right of
  survivorship (only the protected person's fractional interest
  belongs in the inventory).
- **Out-of-state asset**: a brokerage account at an out-of-state
  custodian where the per-account valuation date might differ.
- **The "annual update" path**: PP-406 is filed annually; a
  follow-up year's inventory references the prior year's totals.

Future examples should cover each branch above. The validator
itself does not yet catch the negative-equity or asset-overflow
cases — those are open follow-ups in `KNOWN_GAPS.md`.

## See also

- `../schema.json` — full field-level schema including validators
  and `fill_strategy` for each field.
- `../formulas.yaml` — JSON-DSL definitions of all 10 computed
  subtotals.
- `../skill.md` — known LLM failure modes for slot-table fills,
  including the surname-paraphrase loss pattern PP-406 shares with
  other inventory forms.
- `../../DE-101/examples/` — the contrasting **flat-form** example
  (no slots, no formulas — pure case_constant + party_attr +
  narrative_derived).
