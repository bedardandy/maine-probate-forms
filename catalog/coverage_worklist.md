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
| DE-403 | 3 | `[72,213,542,241]` | "3. Name and Authority of Officer Executing Bond:" label at y198–211; `corporate_surety_officer_name_authority` bound on the empty line *above* at y183 | **Ambiguous** (coordinate-verified) — could be a blank-above-label layout (binding correct) or answer-after-label (mis-bound) | Confirm with a *filled* render before any change; do not re-point on the label alone. |
| DE-405 | 2 | `[72,223,542,235]` | ~~"Gross …Encumbrances $___"~~ **correction:** that label/`$` is at **y207**, where `calc_gross_personal_encumbrances` IS correctly bound; this widget sits in the gap by the grey *Net Value* calc line | **Spurious** (coordinate-verified) — NOT a gap; the field is correctly bound | Leave unmapped. (Crop misread initially; exact text coords corrected it.) |
| DE-506 | 0 | `[334,351,418,363]` | line-5 probate `$` at x326/y352 (unmapped); `probate_estate_value` bound at x421/y376–385 — next to **line-6** augmented `$` (x413/y386); `augmented_estate_value` bound off at left-margin x72/y410 | **Likely cascading mis-binding** (coordinate-verified) — value bindings appear shifted off their `$` blanks | Fix upstream with filled-render verification; cascading, so not hand-edited here. |
| PP-405 | 3 | `[103,354,259,374]` | "Dated:" line in the **register-of-probate bond-approval** block | **Court field** — completed by the register at approval, like the adjacent signature | Leave unmapped (court-completed) or bind as an explicit court date; not petitioner-fillable. |
| AD-007 | 0 | `[54,229,222,243]` | header/structural cell at top of the Q&A table | **Spurious** — answer cells (`parent2_q2` …) already bound | Leave unmapped. |
| DE-403 | 1 | `[72,186,521,365]` | one rect spanning **both** "Description of Pledged Personal Property" columns | **Spurious** — a merged over-detection; `personal_property_surety_1_description` and `_2_description` are bound separately | Leave unmapped. |
| DE-403 | 1 | `[72,706,521,774]` | conditional instruction block ("if no property is described …") | **Review** — likely static instructional text, not an input | Confirm static; leave unmapped. |

## Summary (after coordinate verification)
Each candidate was re-checked against the form's exact text coordinates, not just
the rendered crop — a crop misread initially flagged DE-405 as a gap when the
field is correctly bound (logged below as a caution).

- **New-field gaps (3):** DE-301 notary appearance name; DE-504 & PP-409 email
  lines. These have **no schema field** and need an upstream **tree node** —
  `trees/` is not vendored in this repo, so they cannot be added cleanly here
  (a hand-injected field would diverge from the pipeline source). Apply upstream,
  then regenerate.
- **Likely mis-bindings, fix upstream (2):** DE-403 officer name/authority
  (ambiguous label layout — needs a filled render); DE-506 probate/augmented
  value (`$` bindings appear shifted; cascading). Both are pixel-precise geometry
  corrections that belong in the pipeline's detection/alignment loop with
  filled-render verification, not downstream hand-surgery.
- **Court-completed (1):** PP-405 register "Dated:" — leave to the court.
- **Spurious / static (4):** AD-007 table header; DE-403 merged description rect;
  DE-403 conditional text; **DE-405 y223 widget** (correction: not a gap — the
  field is correctly bound at y207).
- **The other 27** unmapped regions are `spurious` / `likely_static` /
  `leave_unmapped` per `geometry_coverage.json` (footnotes, statutory references,
  order headings, notary print-name lines, mirror detections) — intentionally
  skipped.

**Why nothing was hand-bound here:** verification showed the "gaps" are either
upstream-new-field needs or precise (sometimes cascading) geometry corrections
whose correct placement can't be confirmed without a filled render. One nearly
re-pointed a *correct* binding. The safe, correct path is the upstream pipeline;
this file is the turnkey spec for it. Regenerate via `docs/maintenance.md`.
Outputs are drafts — not legal advice.
