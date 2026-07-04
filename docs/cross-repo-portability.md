# Cross-repo portability of the fill/geometry audit work

This note catalogs the improvements made during the probate underline-fit /
fill audit and says, for each, whether it transfers to the sibling Maine forms
libraries and how to adopt it. It exists because those libraries
([maine-court-forms](https://github.com/bedardandy/maine-court-forms),
[transactional-tax-forms](https://github.com/bedardandy/transactional-tax-forms),
[maine-corporation-forms](https://github.com/bedardandy/maine-corporation-forms))
are separate repos with their own maintainers — this repo can't push to them —
and because they fill forms by a **different mechanism**, so not everything
here is portable.

## The mechanism split (why some of this doesn't transfer)

- **This repo (probate)** fills by *geometry*: `repo/forms/<ID>/fill_geometry.json`
  carries a rect per widget and `tools/fill_pdf.py` writes text at those
  coordinates onto the flat source PDF. The geometry path is probate's own.
- **The other three libraries** fill by *mapping*: a `mapping.json` binds
  canonical fact keys to named AcroForm fields, and the shared
  [`maine-forms-engine`](https://github.com/bedardandy/maine-forms-engine)
  (`fill_via_mapping` → `form_filler`) sets those field values. There are no
  per-widget rects to audit.

So anything that reasons about *rects and printed layout* is probate-only;
anything about *values, tests, and CI* is broadly portable.

## Per-improvement portability

| Improvement | Portable? | Notes / how to adopt |
|---|---|---|
| **pytest-in-CI** (`.github/workflows/tests.yml`) | **Yes — highest value** | Probate had regression gates in `tests/` that never ran in CI. Each sibling should confirm its own suite runs on PRs. The engine already does (`ci.yml`); verify court/tax/corp do too, and if not, mirror the workflow (path-scoped trigger + a manifest-keyed cache of fetched source PDFs). |
| **Fill-render snapshot** (`tests/test_fill_render_snapshot.py`) | **Yes — concept** | The value: lock *rendered* output (name, value, and — where available — placement/size), not just that a value was resolved. Mapping repos already produce a resolved `field_id → value` map via `maine_forms_engine.fill.fill_via_mapping.resolve_mapping` — snapshot that per representative form/case. A baked-widget snapshot of the filled PDF adds placement coverage. Best home: a shared helper in the engine so all three consume it. |
| **PII / law-firm gate discipline** | **Yes** | Every public repo shipping example/mock cases should gate diffs for real names, firms, emails, phones, SSNs, secrets, and local paths before merge. All probate example data is synthetic (RFC-2606 domains, fictional names). |
| **Split-date rendering** (`date_splits` + `fill_pdf._split_date`) | **Mostly no** | This fixes a *geometry* artifact: a printed `"…, 20__"` century stub that isn't its own field, so a whole date dumped on the month/day blank orphans the stub. In the mapping repos a `"20__"` year slot is normally its **own AcroForm field** (map it directly) or wet-ink (leave blank). The engine's `_resolve_key` already renders ISO → `MM/DD/YYYY` for all of them. Only relevant if a mapping form prints a split date across two *non-field* slots — unlikely, since AcroForm forms field-ize their blanks. |
| **`split_date_stub_unhandled` audit check** (`scripts/audit_form_geometry.py`) | **No (as-is)** | Needs `fill_geometry.json`. A mapping-layer analog could flag a printed `"20__"` with no adjacent mapped field, but that's a different check on different inputs. |
| **Geometry audit suite** (`audit_form_geometry.py`, `snap_to_blank --trim`, the collision/overrun/anchoring checks) | **No** | Entirely geometry-specific — the mapping repos have no rects. The AcroForm equivalent of "does the value fit the box" is the engine's existing `form_filler` overflow logging + `text_fit` auto-fit. |

## The one shared seam worth watching

If any sibling ever needs true split-date rendering (a printed date spread
across two slots that are *not* both fillable fields), the right home is
`maine_forms_engine.fill` — alongside `_resolve_key`'s existing ISO→US date
formatting — not a fourth copy of probate's `_split_date`. Promote it there
only when a second consumer actually exists; today probate is the only one, so
it stays local (consistent with the engine's "extract what multiple repos
share, not speculatively" rule).

## Bottom line

The durable, cross-repo wins are **process**, not geometry: run the test suite
in CI, snapshot rendered fills, and gate diffs for PII. The geometry and
split-date work is specific to this repo's fill path and does not port.

Not legal advice; outputs are drafts to verify against the official form.
