# DE-101(I) Examples

Worked example showing the inputs and expected outputs for filling
DE-101(I) (Application for Informal Probate / Appointment of PR —
Intestate).

## Files

| file | role |
|---|---|
| `case.example.json`   | Synthetic case data: case_dict, party records, attorney record, narrative facts. |
| `filled.example.json` | Expected field-by-field fill output. Field IDs match `../fields.csv`. |

## How a downstream consumer uses this

1. Read `../schema.json` to learn each field's `category`, `data_type`,
   `fill_strategy`, and validators.
2. Read `../skill.md` for filer role, statutes, known failure modes,
   and per-form pipeline guidance.
3. Build a fill pipeline that:
   - For `case_constant` and `party_attr` fields, reads from
     `case.example.json`'s `case_dict` / `<party>_record` blocks
     (see schema's `fill_strategy.source`).
   - For `narrative_derived` fields, prompts an LLM with
     `narrative_facts` plus the field's label and skill.md guidance.
   - For `legal_choice` fields, applies a deterministic rule
     (truthy/falsy boolean → yes/no) when `narrative_facts` provides
     a boolean, or prompts the LLM with the enumerated choice set.
   - Leaves `signature` fields blank for wet-ink and `external`
     fields blank (here: `testamentary_instrument` — intestate).
4. Run `scripts/validate_filled.py` against the output. All
   validators in `schema.json` should pass.

## Why this case is a clean intestate happy-path

- Single heir, no will, no codicils, no out-of-state property, no
  prior PR.
- Triggers exactly one yes/no branch each for: real estate in Maine
  (yes → details required), demand for notice (no), notice service
  requests (both yes).
- `testamentary_instrument` is `null` (intestate) — see
  `classifications.yaml` override marking it `external` so the
  validator doesn't flag the blank.

## What this case does NOT exercise

- Multiple-heir branching (no slot enumeration for `heirs_names_addresses`
  since the form uses a free-text paragraph rather than slot rows).
- The 3-year statute-of-repose exception
  (`died_more_than_3_years_circumstances`).
- A non-registered domestic partner sub-form.
- A prior-personal-representative explanation.
- A foreign-domiciled decedent.

Future examples should target each branch above to make sure the
LLM prompt + validator pipeline handles them.
