# Underline-fit audit (horizontal anchoring / sizing pass)

Corpus-wide audit of widget-vs-printed-blank fit against the official PDFs
fetched from maineprobate.net (manifest-verified), adjudicated with 4.5x
zoomed rasterization of both the outlined source and baked filled renders.
Vertical placement was already normalized (1,658 on-rule fields, descender
clearance 0.51–0.66pt, 0 rule crossings — re-verified after this pass); the
remaining defects were horizontal position/size, span selection, and checkbox
anchoring.

## Logic-layer changes

- **`tools/snap_to_blank.py --trim`** — new conservative pass. Full snap
  refuses a widget much wider than its blank (by design); trim-only pulls the
  x-edges back onto the row's blank union (leftmost blank start with the
  label-gap inset, rightmost blank end), never grows, never moves y.
  Multi-slot sentences keep their union extent. Applied corpus-wide: 64 trims
  across 17 forms (e.g. N-115 `probate_court_address` ran 178pt past its
  underline across the printed ", Maine."; MISC-102 `produce_additional` ran
  162pt past the sentence's period).
- **`scripts/audit_form_geometry.py`** — three new/hardened checks:
  - `widget_overruns_blank`: horizontal fit measured against underscore-run
    blanks as well as drawn rules (the old rule-only check missed most forms'
    underscore blanks); escalates to high when the overrun crosses printed
    words. Paragraph boxes (h > 20) exempted from the rule-extent check —
    they legitimately span ragged answer rules.
  - `checkbox_off_printed_box`: choice/enabler rects are compared against
    printed checkbox outlines (drawn rects, small stroked paths, and
    Wingdings/ballot box glyphs). Flags only a *nearby-but-offset* box, and
    stays silent when the nearest box is already claimed by another widget or
    no box is detectable — both cases proved to be parser blind spots, not
    layout defects, under zoomed rasterization (GS-008, AD-008, GS-014).
  - Same-field option rects may intentionally share a printed box (PB-007's
    written-report variants tick a shared lead-in box plus their own);
    excluded from `widget_widget_collision`.
- **`tests/geometry_audit_baseline.json`** regenerated and the gate extended
  to the new codes — the corpus now locks at zero
  collision/overrun/native-text/county/checkbox findings (three legacy
  naming findings remain).

## Per-form fixes (visually confirmed at 4–4.5x zoom)

- **GS-014** — `treated_by_counselor/_case_worker/_dentist/_other` enablers
  were full-width text-sized rects overlapping the details rows (8 widget
  collisions); rebuilt as 10x13 boxes on their printed checkboxes, matching
  `treated_by_physician`. Details rows trimmed to their rules.
- **MISC-102** — span-selection fixes: the appear-at-court *time* field sat on
  the "20__" year stub and the *location* field on the time slot (values
  would land in the wrong blanks); time re-anchored onto its real slot, the
  duplicate location suppressed (`suppressed_duplicate_of_...`), year stubs
  left as hand blanks (the date fields carry full dates). Same pattern in the
  appear-before paragraph (`appear_before_final` suppressed —
  no-obvious-question year stub). The permit-inspection paragraph box had
  swallowed the printed "Time and place of inspection:" label row and
  `permit_time_place` floated 20pt below its underline; box stops above the
  label, time/place sits on the label's trailing underline.
- **PB-007.vA** — the date-variant option ticked the "14 days" box and the
  deadline date had a stray checkbox-sized widget over the row-4 checkbox;
  rewired to PB-007's correct dual-box encoding, stray widget dropped.
- **PP-505** — the relationship chain rode between rules and its first row
  duplicated `respondent_name`'s appositive blank; rebuilt on the three
  "as follows" rules, baseline-normalized.
- **AD-008 / NC-001** — `notary_date` spanned the whole "Date: ___  ___"
  row including the signature blank; now covers the date blank only.
- **AD-026** — item-7 paragraph box bottom overlapped item-8's checkbox.
- **County caption fields** (DE-401, DE-403 x2, DE-501, DE-502, N-105, N-115,
  N-117) — right edge inset 3pt from the printed COUNTY label.
- **AF-104** — notary day widget re-seated on its rule.

## Split-date rendering (MISC-102 "on ___, 20__")

The subpoena appearance clause prints its date across two slots — a blank for
the month and day and a separate printed "20__" stub for the two-digit year.
The date field wrote the whole ISO value on the first blank, leaving
"2025-04-02, 20__" with an orphaned, redundant year stub. Added an opt-in
`date_splits` map in `fill_geometry.json` plus `fill_pdf._split_date`: a
declared date field with a parseable value renders "April 2" on the main blank
and "25" on the paired stub, so the clause reads "on April 2, 2025" the way the
form is designed to read. Unparseable values (e.g. an unset narrative field)
fall through untouched — the stub stays blank, exactly as before. Wired for
MISC-102 paragraph 1 only (the unambiguous split); the deposition paragraph's
multi-slot date is left as-is. Locked by `tests/test_split_date.py`.

**Corpus sweep + permanent guard.** Swept every form's source for the
century-stub signature (a printed "20" / "20__" token adjacent to a filled
date field). The corpus holds exactly three bare "20" tokens: MISC-102's
appearance clause (wired above) and the inventory *row numbers* on DE-405 /
PP-406 (`intang_20_*` / `int_prop_20_*` — line item 20, not a year). The
classic execution jurat "___ day of ____, 20___" carries **no** date widget
anywhere in the corpus — those are wet-ink blanks, correctly unmapped. So the
split-date pattern is already fully applied where it belongs. To keep it that
way, `scripts/audit_form_geometry.py` gained a `split_date_stub_unhandled`
check: a filled date field whose blank sits on a printed "20__" stub's row (or
the row directly above) with no `date_splits` entry is flagged. It reads 0
today and is added to the high-value regression gate, so a newly added form
with this layout — or an accidental un-wiring of MISC-102 — fails CI.

Not defects (adjudicated false positives, recorded so they are not
"re-fixed"): GS-008/AD-008/GS-014 checkbox anchoring (boxes render fine —
the parser could not see some printed boxes); AD-011
`name_and_location_of_court` (the printed blank is genuinely short; the fill
layer's shrink-to-fit is the intended behavior); `uncovered_printed_rule`
findings over signature/court/"do not write" areas (intentional blanks — the
suppression conventions `suppressed_wet_ink` / `court_completed` cover the
fields; the rules themselves stay informational-only in the audit).

Outputs are drafts — not legal advice.
