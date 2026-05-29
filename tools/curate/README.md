# Hand-curation feedback loop for field geometry

Auto-detected field placement is usually right, but some forms need a nudge. The
fastest fix is to move the field **by hand** in a PDF editor, then teach the repo
what you changed. `curate_geometry.py` turns a hand-edited PDF into a reviewable
diff and a one-command patch to `fill_geometry.json`.

```
render  ->  (hand-edit the PDF in any editor)  ->  diff  ->  apply
```

## The loop

```bash
# 1. Render the current geometry as an editable, field-named PDF (over the real
#    blank fetched from metadata.source_url; pass --source for a local blank).
python3 -m tools.curate.curate_geometry render --form DE-101 --out DE-101.edit.pdf

# 2. Open DE-101.edit.pdf in Acrobat / LibreOffice Draw / Preview. Each widget is
#    named by field_id. Drag one to where it belongs, rename it, add or delete
#    fields, and save in place.

# 3. Diff the edited PDF against fill_geometry.json. Prints a Markdown report;
#    --emit-override writes a mergeable patch.
python3 -m tools.curate.curate_geometry diff --form DE-101 \
    --edited DE-101.edit.pdf --emit-override DE-101.override.json

# 4. Merge the patch into the form's fill_geometry.json (writes a .bak first).
python3 -m tools.curate.curate_geometry apply --form DE-101 \
    --override DE-101.override.json
```

The `diff` report is meant to live in a pull request: it shows exactly what moved
(with point deltas), what was renamed, added, or removed — a human can sanity-check
the correction before it lands. `apply` is the only verb that writes; everything
else is read-only.

## What it detects

| change | how |
| --- | --- |
| **moved** widget | rect changed beyond `--tol` (default 0.5 pt) on a same-named field |
| **renamed** field | a field disappears under one name and reappears at the same spot under another (primary-rect IoU > 0.5) — reported as a rename, not delete+add; carries any nudge too |
| **added** field | a widget whose `field_id` isn't in the current geometry |
| **removed** field | a geometry field with no widget in the edited PDF |

## Conventions (must match for a clean read-back)

- **Coordinates** are PyMuPDF top-left points `[x0, y0, x1, y1]` — the same space
  `tools/fill_pdf.py` writes and `CLAUDE.md` documents. Edit in an editor that
  preserves the page geometry; don't re-paginate or rotate.
- **Widget names** follow `fill_pdf.py`: `field_id` (primary text widget),
  `field_id__<n>` (continuation text widgets), `field_id__<value>` (option boxes).
  Keep that scheme when you rename, or the diff will read a rename as add+remove.
- The override patch is the same shape as `fill_geometry.json` (`fields.<id>.widgets`
  / `.options`), plus `_removed` and `_renamed`. `apply` preserves each field's
  original `type` when the patch doesn't override it.

> Not legal advice. Curation changes *where* fields sit, not *what* belongs on
> the form.
