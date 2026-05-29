# Filled-form accessibility — method + what it actually achieves

Opus established this on a real probate artifact (DE-101 filled), validated each
claim with veraPDF 1.30.1 (PDF/UA-1), then distilled it into a deterministic,
model-free script (`remediate_form.py`) that runs on any filled form via its
schema. This is the "Opus implements → backport to deterministic" pattern.

## The method (deterministic, schema-driven)

The repo's `schema.json` already holds a human-readable `label` per `field_id`,
and `fill_pdf.py` names each widget by `field_id`. So for a filled form we can,
with no model:

1. **Accessible field names** — for each widget, `/TU = schema label`
   (`county_probate_court` → "County Probate Court"). WCAG 1.3.1 / 4.1.2.
2. **Document title** — set `/Info /Title` + XMP `dc:title`, and
   `ViewerPreferences /DisplayDocTitle true` (show title, not filename). 2.4.2.
3. **Language** — `/Root /Lang`. 3.1.1.
4. **Logical tab order** — `/Tabs = /S` on each page. 2.4.3.

## What it achieves (veraPDF-measured, DE-101 filled)

| | before | after |
|---|---|---|
| form fields with accessible name (/TU) | 0/17 | **17/17** |
| document title / DisplayDocTitle | none / false | set / true |
| UA-1 failed checks | 234 | **216** |
| UA-1 failed rules | 13 | 11 |

Backports across forms: DE-101/DE-301/PB-007/PP-406 all reach **100% /TU**.
Real-world effect: a screen reader announces "County Probate Court" instead of
"county_probate_court" — the change that actually matters for a fillable form.

## The structural tag tree — OpenDataLoader closes it (offline, free)

UPDATE: the content tag tree (7.1/7.2) and form-field tagging (7.18.x) — which
remediate_form.py alone could not build — ARE achievable in pure OSS via
**OpenDataLoader PDF** (Apache-2.0, v2.4.7, fully offline, Java 11+/Python 3.10+,
no API/GPU). Its free tier auto-tags untagged PDFs into Well-Tagged PDFs.

Validated full pipeline (DE-101 filled, veraPDF UA-1):

| stage | failed checks |
|---|---|
| raw filled | 234 |
| + remediate_form.py (/TU, title, lang, tabs) | 216 |
| + OpenDataLoader auto-tag (content + structure tree) | 10 |
| + PDF/UA identifier stamp (pikepdf XMP `pdfuaid:part`) | 9 |
| + Form-element child fix (7.18.4 — strip stray MCIDs) | **7** |

The 7.1/7.2 structural clauses (71+71+21) and all 7.18 form-field clauses go to
**0**, with filled values, text, and /TU names preserved. The remaining **7 are
ALL source fonts not embedded** (7.21.4.1/.2) — a defect inherited from the
government source PDF, not introduced here. The pipeline's own structural and
form-field tagging is fully clean.

Tested across 6 forms incl. table-heavy inventories/accounts (DE-101/DE-301/
PB-007/PP-406/DE-405/DE-406): all generalize to **font-only residual** (4–7
fails), and the financial tables tag with **zero** table-clause failures. The
Form-element fix (`finalize()`) clears 7.18.4/2 everywhere with no regression
(veraPDF-verified).

## One command, end to end (no PDFs distributed)

`make_accessible.py` runs the whole flow in one driver: it **fetches the blank
form from its `source_url` at runtime** (this repo ships no court PDFs), embeds
fonts on the blank, fills it from your case data, tags it, repairs the fonts, and
optionally validates. With every optional tool present (OpenDataLoader for the
content tag tree, ghostscript, veraPDF) the result is a veraPDF-verified PDF/UA-1
form. Without the tag-tree tool it still applies the form-level accessibility
criteria OSS can do reliably — embedded field names (`/TU`), document title,
language, tab order, and repaired/embedded fonts — and degrades with a warning
rather than failing. The full content tag tree (PDF/UA 7.1/7.2) needs
OpenDataLoader or Adobe Acrobat; see [`remediate_form.py`](remediate_form.py).

```bash
python3 tools/accessibility/make_accessible.py \
    --form DE-101 --case case.json --out DE-101.accessible.pdf --verify
# [1/6] fetch  http://www.maineprobate.net/.../DE-101(I)...pdf
# [2/6] embed  fonts embedded on blank source
# [3/6] fill   DE-101 from case.json
# [4/6] tag    field names + OpenDataLoader tag tree
# [5/6] repair widget/checkbox/subset fonts + ToUnicode
# [6/6] verify ✓ veraPDF UA-1: compliant=true failedChecks=0
```

Already have the blank? `--source blank.pdf` skips the fetch. Every external tool
(ghostscript, OpenDataLoader, veraPDF) is auto-detected and optional — a missing
one degrades with a warning rather than failing (no ghostscript just leaves a few
source-font checks). Override binaries via `GHOSTSCRIPT` / `ODL_PYTHON` /
`VERAPDF` / `WIDGET_TTF` / `ZAPF_TTF`. The steps below are what it runs; read on
if you want to drive them individually.

## Closing the font fails too — full UA-1, still free/OSS

The font residual is *also* fixable in pure OSS — the earlier "leave them /
needs enterprise export" conclusion was wrong. Two non-obvious moves:

1. **Embed the source fonts on the BLANK source, before fill/tag** — NOT on the
   filled form. A ghostscript re-distill (`gs -sDEVICE=pdfwrite -dEmbedAllFonts`,
   `NeverEmbed []`) of a *filled* form flattens it (loses widgets + /TU) and
   strips toUnicode, exploding UA-1 from 9 to **826** (tested). Run the same gs
   pass on the blank form first, then fill and tag — the widgets are added after,
   so nothing is flattened.
2. **Repair the fonts the fill step itself introduces** with
   `embed_widget_font.py` (run LAST):
   - **7.21.4.1** — PyMuPDF injects field text in unembedded base-14 Helvetica;
     replace it in place with embedded Liberation Sans (metric-compatible).
   - **7.21.4.1 + 7.21.6/4** — checkmarks use unembedded ZapfDingbats; embed a
     ZapfDingbats TTF as a symbolic TrueType and strip its cmap to a single (1,0)
     subtable. (Optional — only if a ZapfDingbats TTF is present; set `ZAPF_TTF`.)
   - **7.21.7** — gs-embedded subset fonts (Calibri/MS-Gothic) carry no
     ToUnicode; attach a WinAnsi-derived ToUnicode to any simple WinAnsi/no-enc
     font that lacks one.

`finalize()` also strips an incomplete `/CIDSet` (7.21.4.2) and asserts
`MarkInfo /Marked` (6.2, only when a real tag tree exists).

**Fleet result across all 79 Maine probate forms (veraPDF UA-1, 2026-05-29):**

| bucket | count | |
|---|---|---|
| **CLEAN** (0 fails + anti-cheat integrity gate holds) | 74 | content tree, reading order, tables, field names, form-field tags, **fonts** all pass |
| CLEAN-UNFILLED | 2 | sparse test case filled 0 widgets; veraPDF=0 |
| residual (honest ceiling) | 3 | AF-105 (OpenDataLoader under-tagged a dense form); PP-506 / PP-601 (`.notdef` from stray PMingLiU CJK junk in the source Word doc — upstream defect) |

= **76/79 (96%) clean PDF/UA-1, fully free and offline.** No Adobe, no enterprise
export, no GPU, no network. The 3 residuals are a free-tier tagger limit + an
upstream source defect — not a paywall. Every "pass" is checked by an
**anti-reward-hack integrity gate** (pages/text/widgets/tagged/Marked must all
survive) so a flatten or a marked-all-artifact cheat can't be scored as clean.
Never fake `/MarkInfo Marked` over an empty tree — build a real one.

Full baseline per form:

```bash
# 1. embed fonts on the BLANK source (before fill)
gs -q -dNOPAUSE -dBATCH -sDEVICE=pdfwrite -dEmbedAllFonts=true -dSubsetFonts=true \
   -dCompatibilityLevel=1.7 -sOutputFile=src_embed.pdf \
   -c "<</NeverEmbed [ ]>> setdistillerparams" -f source.pdf
# 2. fill (fill_pdf.py), then 3. tag + finalize:
python3 accessibility_pipeline.py filled.pdf tagged.pdf --schema repo/forms/<ID>/schema.json
# 4. repair the widget/checkbox/subset fonts LAST:
python3 embed_widget_font.py tagged.pdf final.pdf
verapdf --flavour ua1 final.pdf   # -> isCompliant="true" on 74/79
```

## One-command pipeline

`accessibility_pipeline.py` runs steps 1-3 and (optional) validates:

```bash
python3 accessibility_pipeline.py filled.pdf out.pdf \
    --schema repo/forms/<ID>/schema.json --validate
# -> field names + title + lang + tabs, OpenDataLoader tag tree, UA-id stamp,
#    veraPDF report. Reproduced 9 fails on DE-101; widgets/values/tags preserved.
```

remediate_form.py and OpenDataLoader are **complementary**: the script supplies
precise field accessible names from the schema (which OpenDataLoader can't know),
OpenDataLoader supplies the logical content/structure tree. Run remediate_form
first, then tag.

```bash
python3 remediate_form.py filled.pdf step1.pdf --schema repo/forms/<ID>/schema.json
opendataloader-pdf --format tagged-pdf step1.pdf      # -> step1_tagged.pdf
# then stamp pdfuaid:part=1 via pikepdf; validate:
verapdf --flavour ua1 final.pdf
```

The earlier "needs Adobe/manual" ceiling applied to pikepdf/PyMuPDF ALONE. With
OpenDataLoader added, the tag tree is OSS/offline; only font embedding remains.
Still: never fake `/MarkInfo Marked` over an empty tree — build a real one.

## Run

```bash
python3 remediate_form.py <filled.pdf> <out.pdf> \
    --schema repo/forms/<FORM_ID>/schema.json [--title "..."] [--lang en-US]
# validate:
verapdf --flavour ua1 <out.pdf>
```

## Backport into the repo (two options)

1. **Retrofit (this script):** run as a post-step on any already-produced filled
   PDF. No change to existing code.
2. **At the source (recommended):** `fill_pdf.py` already has the schema labels at
   injection time — add a pikepdf post-pass (the four steps above) so every filled
   artifact is born with accessible field names + title + lang + tab order. Then
   accessibility is free and automatic for all forms.
