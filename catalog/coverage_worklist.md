# Geometry coverage worklist

`catalog/geometry_coverage.json` records **37 detected widget regions that no
schema field is bound to**. This file triages them so the number is not an
undifferentiated unknown: each `candidate_input`/`ambiguous` region below was
rendered from the blank form and classified. The remaining 27
(`spurious`/`likely_static`/`leave_unmapped`) are confirmed intentional skips.

**Important:** every form's schema fields are *already* bound to geometry (0
unbound fields), and all 79 forms fill end-to-end. These are edge regions, not
broken fills. Acting on the "genuine gap" rows below changes fill behaviour
(new schema fields, or re-pointing an existing field's widget), so they are a
**gated follow-up**, not applied here — a wrong re-point would regress a
currently-correct fill.

## Triaged candidates (the 10 `candidate_input` / `ambiguous` regions)

| Form | Page | Region (rect) | Render shows | Verdict | Recommended action |
|---|---|---|---|---|---|
| DE-301 | 1 | `[72,496,543,514]` | "Before me, this day, personally appeared ___" notary line | **Genuine gap** — no schema field | Add `applicant_appearance_name` (text); bind to this rect. (DE-403 has the analogue `affidavit_surety_1_appearance_name`.) |
| DE-504 | 0 | `[72,754,542,774]` | blank "Email Address" line (not the bound `attorney_email`) | **Genuine gap** | Add a petitioner/second email field; bind here. |
| PP-409 | 0 | `[58,747,518,774]` | blank "Email Address" line | **Genuine gap** | Same as DE-504. |
| DE-403 | 3 | `[72,213,542,241]` | "3. Name and Authority of Officer Executing Bond:" fill-in | **Genuine** — schema has `corporate_surety_officer_name_authority`, bound 30pt above at `[72,183,542,196]` | Verify which line is correct; likely a second occurrence — confirm before re-pointing. |
| DE-405 | 2 | `[72,223,542,235]` | "Gross Value of Personal Property Encumbrances $___" | **Genuine / check** — `calc_gross_personal_encumbrances` bound one line up at `[298,207,436,220]`; this line may be `calc_net_inventory` | Map this `$` line to its correct existing field; confirm visually first. |
| DE-506 | 0 | `[334,351,418,363]` | "is $___" currency blank | **Genuine** — `probate_estate_value` bound at `[422,376,...]`, `augmented_estate_value` at `[72,410,...]`; this is a third `$` line | Identify the correct value field for this line (verify against the page's three `$` blanks). |
| PP-405 | 3 | `[103,354,259,374]` | "Dated:" line in the **register-of-probate bond-approval** block | **Court field** — completed by the register at approval, like the adjacent signature | Leave unmapped (court-completed) or bind as an explicit court date; not petitioner-fillable. |
| AD-007 | 0 | `[54,229,222,243]` | header/structural cell at top of the Q&A table | **Spurious** — answer cells (`parent2_q2` …) already bound | Leave unmapped. |
| DE-403 | 1 | `[72,186,521,365]` | one rect spanning **both** "Description of Pledged Personal Property" columns | **Spurious** — a merged over-detection; `personal_property_surety_1_description` and `_2_description` are bound separately | Leave unmapped. |
| DE-403 | 1 | `[72,706,521,774]` | conditional instruction block ("if no property is described …") | **Review** — likely static instructional text, not an input | Confirm static; leave unmapped. |

## Summary
- **Genuine fillable gaps (6):** DE-301 appearance name; DE-504 & PP-409 email;
  DE-403 officer name/authority; DE-405 encumbrances `$` line; DE-506 value `$`
  line. Two (DE-301, the email lines) need a **new schema field**; the other
  three are **existing fields** whose correct line must be confirmed before
  re-pointing.
- **Court-completed (1):** PP-405 register "Dated:" — leave to the court.
- **Spurious / static (3):** AD-007 table header, DE-403 merged description
  rect, DE-403 conditional text.
- **The other 27** unmapped regions are `spurious` / `likely_static` /
  `leave_unmapped` per `geometry_coverage.json` (footnotes, statutory
  references, order headings, notary print-name lines, mirror detections) —
  intentionally skipped.

Regenerate the coverage data with the detection pipeline; see
`docs/maintenance.md`. Outputs are drafts — not legal advice.
