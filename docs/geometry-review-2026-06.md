# Geometry & AcroForm mapping review — 2026-06

Scope: review the repo (including the `codex/improve-probate-form-mappings`
branch), stand up systematic build/smoke/test infrastructure, visually audit
forms not hand-touched in the last couple of days, and stress the fill path with
plausible-but-wrong inputs. Recently hand-audited geometry (DE-403, 2026-06-17;
the 2026-06-16 geometry-review batch) is treated as authoritative — it is not
overwritten here; disagreements are flagged for a second review instead.

## What landed on this branch

- **Systematic tooling adopted from codex (vetted, no geometry override):**
  `scripts/audit_form_geometry.py` (mapper-failure auditor),
  `scripts/geometry_optimizer.py` + `scripts/optimize_fill_geometry.py`
  (conservative, **provenance-respecting** cleanup — never touches a widget
  marked `locked: true` or a manual `geometry_source`),
  `scripts/build_alignment_stress_packet.py`, and the matching tool-layer
  improvements (`find_forms` two-letter-prefix boost, `route_form` nested-JSON
  extraction, `fill_plan` provenance, `verify_fill_geometry`
  `overlapping_option_errors`, `fill_pdf` county-uppercasing / enabler
  checkboxes / repetition-gap splitting).
- **New infra (this review):** `tools/stress_render.py` (overlay every geometry
  rect on the official PDF for visual inspection without a hand case),
  `tests/test_smoke_examples.py` (fill+verify every example, regression-gated by
  `tests/known_fill_gaps.json`), `tests/test_adversarial_fills.py`, `pytest.ini`,
  and `make test` / `make smoke` / `make audit` targets.
- **Geometry adopted (codex, visually confirmed):** the DE estate family
  DE-101, DE-101(I), DE-104, DE-201, DE-201(I), DE-301, DE-301(I). These fix
  real overlapping-option and sprawling-box bugs (e.g. DE-301
  `petitioner_interest_other_details` no longer sprawls over items 4–5;
  DE-101(I) `creditor`/`other` options no longer overlap, IoU 0.95 → resolved).
- **Example cases corrected:** removed Register-completed fee facts
  (`filing_fee`, `mailing_notices_fee`, `publication_fee`) from the DE-101 and
  DE-201(I) examples to match the (correct) court-use fee suppression.

Gates after the change: `make verify` OK (2 known PB-007 shared-widget warnings),
`make test` = 195 passed, audit total 821 → high-value codes net better
(widget/widget 52→48, overruns 54→44, native-text 67→64) with **no** regression.

### Full-corpus "visual" coverage (all pages, all forms)

A pytest can't literally look at a page, so the corpus is covered two ways:

- **Automated gate** over every page of all 82 forms (201 pages):
  `tests/test_render_all_forms.py` rasterises each page (catches geometry that
  breaks the renderer) and asserts every widget rect is on-page and
  non-degenerate — currently clean (0 off-page, 0 degenerate).
  `tests/test_geometry_audit_baseline.py` runs the mapper-failure auditor across
  the whole corpus and locks each form's high-value finding counts to
  `tests/geometry_audit_baseline.json`, so a new collision/overrun/orphan on any
  page fails CI while cleanups pass.
- **Human/agent review packet:** `make probe-all` (`tools/render_corpus.py`)
  writes overlay PNGs for every page of every form to a directory for actual
  eyeballing — the same overlays the automated gate validates.

## Codex branch verdict (vetted, not wholesale)

| Item | Verdict | Evidence |
|---|---|---|
| Auditor + optimizer + tests + tool layer | **Adopt** | additive; optimizer honors `locked`/manual provenance |
| DE estate-family geometry | **Adopt** | auditor deltas + visual review confirm fixes |
| **AD-011 geometry** | **Reject** | introduced **+34** `widget_native_text_collision`: turned the single-line answer boxes for items 2a–2d into oversized multi-line rects that overprint the printed question labels (`/tmp/probe_codex/AD-011_p1.png`). Main's AD-011 is correct. |
| **DE-403 witness widgets** | **Flag — keep hand audit** | codex sets `witness_for_personal_rep`/`_co_personal_rep`/`_surety_*` to `geometry_source: suppressed`; the 2026-06-17 hand audit kept them fillable. Defensible either way (witness lines are often wet-ink) — see "Flagged for second review". |
| AF-101.vA, DE-201(I) minor native-text upticks | watch | +2 / +1; DE-201(I) visually clean (`/tmp/probe_now/DE-201(I)_p1.png`) |

## Auditor triage (non-recently-audited forms)

`make audit` → `catalog/geometry_audit.json`. High-value cross-field worklist
(self-collisions excluded — those are mostly benign multiline/repeating-group
widgets and the documented PB-007 shared widget):

| Form | findings | visual note |
|---|---|---|
| AF-105 | 17 | financial-affidavit amount columns abut; low severity |
| MISC-102 | 16 | mostly county trims + a few checkbox-sized text rects |
| GS-014 | 15 | **confirmed**: provider detail/continuation boxes run tall and overlap adjacent checkbox label lines (`/tmp/probe_now/GS-014_p1.png`) — real, low severity |
| N-118 | 13 | change-in-dwelling row boxes abut |
| AD-028, N-115, PP-405, MISC-101, AF-102/104, N-105/108 | 4–7 | to triage |

The optimizer would fix the safe subset automatically (county over-rule trims
on AF-105/MISC-102/N-118) but does **not** address the GS-014 vertical overlaps
— so it under-reaches rather than over-reaches, which is the desired bias.

## Adversarial / plausible-but-wrong LLM inputs

Pinned in `tests/test_adversarial_fills.py`. Findings:

- **Out-of-enum choice fails safe.** An invalid `applicant_legal_interest`
  value (`executor`) is *not* stamped onto any checkbox; an invalid member of a
  multi-select (`["heir","executor"]`) is *dropped*, not coerced. In both cases
  `verify_filled` surfaces the discrepancy (expected ≠ actual) so an agent/human
  sees the fact went nowhere. This is the system's main guard against confident
  but wrong enum picks.
- **No crash on degenerate text.** Empty / `None` / 4000-char / numeric /
  control-character values all fill without raising; over-long single-line
  values overflow to an addendum.
- **FLAG — no semantic validation of free text/date/currency.** A syntactically
  fine but nonsensical value ("last spring sometime" as a death date, a future
  DOB, a negative amount) lands verbatim. `verify_filled` confirms placement but
  cannot judge correctness. Verify + human review remain the only guards; a
  lightweight type/range validator on date/currency fields is the recommended
  next systematic improvement.

## Flagged for second review (do not auto-apply)

1. **DE-403 witness widgets** — codex suppression vs. 2026-06-17 hand audit.
   Decide whether witness lines on the bond are filer-typed or wet-ink.
2. **Optimizer date suppression** — `optimize_fill_geometry` suppresses *date*
   fields adjacent to signatures (`date_signed`, `notary_date`, `statement_date`)
   alongside the signatures themselves. Signing/notary dates are frequently
   typed, so this should be reviewed before bulk application; only the county
   over-rule trims are unambiguously safe to apply now.

## Open fill-gap worklist (`tests/known_fill_gaps.json`)

Surfaced by the smoke gate; each is a tracked missing/mismatched widget, not a
regression:

- **Candidate missing widgets:** `APP-1:county`, `CN-1:notary_county`,
  `DE-301(I):notary_name`, `AD-026:inheritance_acknowledgment` /
  `parent_child_relationship_acknowledgment` — resolved facts with no widget to
  receive them; confirm against the official form whether the blank exists.
- **`DE-101:decedent_name_caption`** — caption suppressed (filled via
  `decedent_full_legal_name`); likely intentional, confirm.
- **`PB-007` double-stamp** — `appointment_level` renders `expanded; expanded`
  from the intentional shared widget; `gal_roster_status` / `objection_status` /
  `appointment_end_event` are narrative-only with no checkbox. Real bug + design
  limits; the shared widget is the documented `make verify` warning.

Close items by deleting them from `tests/known_fill_gaps.json` as widgets are
added/fixed — the smoke gate then locks them against regression.
