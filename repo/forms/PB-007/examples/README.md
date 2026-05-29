# PB-007 Examples

Worked example for PB-007 (Order Appointing Guardian Ad Litem —
Probate). Demonstrates the **conditional-writability** pipeline
pattern: a top-level radio choice (`appointment_level`) gates
which downstream fields are valid to fill.

## Files

| file | role |
|---|---|
| `case.example.json`     | Synthetic GAL appointment case — appointment_level=**limited** path. |
| `filled.example.json`   | Expected fill (limited path). standard_* and expanded_* fields left blank. |
| `case.expanded.json`    | Second case — appointment_level=**expanded** path. Subpoena power, multi-record review, third-party engagement, mediation appearance. |
| `filled.expanded.json`  | Expected fill (expanded path). limited_* and standard_* fields left blank. |

## Pipeline pattern: conditional writability

PB-007 is the canonical example of the **writable_when** pattern.
The form has three appointment-level radio buttons at the top:

- **limited** — gates the 3-field `limited_*` section
- **standard** — gates a single `standard_other_provisions` field
- **expanded** — gates 26+ `expanded_*` fields (subpoena power, expert
  engagement, third-party interviews, evaluation orders, etc.)

Selecting one of the three should **disable** the other two
sections. Encoded in `schema.json` as:

```json
"writable_when": {
  "all_of": [
    {"field": "appointment_level", "equals": "limited"}
  ]
}
```

The validator interprets this as: "if `appointment_level !=
'limited'` and this field has a value, that's a `not_writable`
violation."

## What this case exercises

| dimension | how it's tested |
|---|---|
| **Conditional writability** | `appointment_level=limited` → `limited_*` fields filled; `standard_*` and `expanded_*` fields all null |
| **value_in enumeration** | `appointment_level` value matches `{limited, standard, expanded}` |
| **Multi-party fills** | gal, minor, objector, payor records all wired into separate party_attr fields |
| **Dual docket numbers** | case_dict carries `docket_no_probate` AND `docket_no_district`; the latter is null when only probate is involved |
| **Conditional follow-up text** | `objection_status=no_objection_to_appointment` → `objecting_parties_appointment` and `other_objector_name_appointment` stay blank |

## Pipeline walkthrough

1. **Determine the gating value.** LLM or human resolves
   `appointment_level` from the narrative. If unclear, default to
   limited (the most conservative scope).
2. **Fill only the gated section.** For `appointment_level=limited`,
   write `limited_duties`, `limited_hearing_appearance`, and
   `limited_other_provisions` (if applicable). For `=standard`,
   write `standard_other_provisions`. For `=expanded`, fill the
   full expanded_* set.
3. **Leave non-gated sections null.** Do NOT pad with placeholders
   like "N/A" or "n/a" — the validator interprets a non-empty
   string as a `not_writable` violation on a gated field.
4. **Validate.** `scripts/validate_filled.py` will:
   - Check `value_in(limited, standard, expanded)` on the gate.
   - For every field with a `writable_when` rule, check that the
     value is empty when the rule evaluates to false.
   - Catch the cascade pattern: if `appointment_level=standard`,
     then `expanded_duties`, `expanded_interview_teachers`, etc.
     must all be empty.

## What case.expanded.json adds vs case.example.json

The expanded case complements the limited case by exercising the
opposite gate of writability:

| dimension | limited case | expanded case |
|---|---|---|
| `appointment_level` | `limited` | `expanded` |
| Writable section | `limited_*` (3 fields) | `expanded_*` (26+ fields) |
| Subpoena | not applicable | `yes` — court grants § 1-115 subpoena power |
| Record review | school records only (within limited_duties prose) | THREE separately named subjects in `expanded_person_1..3_name` + `expanded_record_types_1..3` |
| Provider engagement | none | `expanded_engage_provider=yes`, psychologist evaluation with cost cap |
| Counseling | none | `expanded_arrange_counseling=yes` with deadline + scope |
| Mediation | not appearing | `expanded_mediation_appearance=yes` |
| Dual docket | probate only | probate AND district court |
| Objection at appointment | `no_objection_to_appointment` | `objecting` → triggers `objecting_parties_appointment` + `appointment_factors` |
| Fee objection | none | objection overruled, court reallocates 75/25 |
| Fee cap | 20 hours | 60 hours |

Both cases share: writable_when gate enforcement, value_in on
appointment_level / gal_roster_status / objection_status /
appointment_end_event, and the `not_writable` cascade catch on
non-gated sections.

## What still isn't exercised

- **`appointment_level=standard`** — the middle path; gates a
  single follow-up field. A third case could cover this.
- **Fee-arrangement variants** — both cases use `hourly_cap`; the
  form also supports `flat_fee`, `hourly_rate`, and
  `hourly_cap_with_additional`.
- **`gal_roster_status=not_rostered` or `waived`** — both cases
  use `rostered`.
- **`appointment_end_event` non-`report_filed` values** — neither
  case exercises `case_dismissed`, `date_certain`, etc.

## See also

- `../schema.json` — full field-level schema with writable_when JSON trees
- `../skill.md` — hand-curated narrative documenting the
  appointment-level cascade and the GAL-roster constraint
- `../../DE-101/examples/` — flat-form contrast (no conditional logic)
- `../../PP-406/examples/` — slot-table + formulas contrast
