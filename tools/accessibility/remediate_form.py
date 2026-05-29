#!/usr/bin/env python3
"""Schema-driven accessibility remediation for a FILLED probate form (AcroForm).

The deterministic, repeatable method Opus established, distilled so it runs on any
filled form without a model: the repo's schema.json already holds a human-readable
`label` per field_id, and fill_pdf names each widget by field_id — so we can map
every form field to its accessible name (/TU) deterministically. Also sets the
document title + DisplayDocTitle, /Lang, and a logical tab order.

This targets the form-specific accessibility criteria that matter for a fillable
form and that OSS CAN do reliably (WCAG 1.3.1 / 4.1.2 field names, 2.4.2 title,
2.4.3 tab order, 3.1.1 language). It does NOT build the full content tag tree
(PDF/UA 7.1/7.2) — that still needs Adobe Auto-Tag or Acrobat; see the report.

    python3 remediate_form.py <filled.pdf> <out.pdf> --schema repo/forms/<ID>/schema.json
                                                      [--title "..."] [--lang en-US]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import pikepdf


def label_map(schema_path):
    sch = json.loads(pathlib.Path(schema_path).read_text())
    title = sch.get("_skill_metadata_override", {}).get("form_title") \
        or sch.get("form_id", "")
    return {f["field_id"]: (f.get("label") or f["field_id"]) for f in sch["fields"]}, title


def remediate(inp, outp, schema_path, lang, title):
    labels, form_title = label_map(schema_path)
    done = {"tu_set": 0, "tu_total": 0}
    with pikepdf.open(inp) as p:
        # 1. accessible name (/TU) on every widget, from the schema label
        for pg in p.pages:
            for a in pg.get("/Annots", []):
                if a.get("/Subtype") == pikepdf.Name("/Widget") and "/T" in a:
                    done["tu_total"] += 1
                    base = str(a["/T"]).split("__")[0]
                    lbl = labels.get(base)
                    if lbl:
                        a["/TU"] = pikepdf.String(lbl)
                        done["tu_set"] += 1
            # 3. logical tab order: follow structure/annot order
            pg["/Tabs"] = pikepdf.Name("/S")
        # 2. document title (Info + XMP) + show it in the title bar
        final_title = title or form_title or pathlib.Path(inp).stem
        with p.open_metadata(set_pikepdf_as_editor=False) as xmp:
            xmp["dc:title"] = final_title
        p.docinfo["/Title"] = final_title
        vp = p.Root.get("/ViewerPreferences", pikepdf.Dictionary())
        vp["/DisplayDocTitle"] = True
        p.Root["/ViewerPreferences"] = vp
        # 4. language
        p.Root["/Lang"] = pikepdf.String(lang)
        p.save(outp)
    return done, final_title


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf"); ap.add_argument("out")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--lang", default="en-US"); ap.add_argument("--title", default=None)
    a = ap.parse_args()
    if pathlib.Path(a.out).resolve() == pathlib.Path(a.pdf).resolve():
        print("refusing to overwrite the original", file=sys.stderr); return 2
    done, title = remediate(a.pdf, a.out, a.schema, a.lang, a.title)
    print(f"# Form remediation: {a.out}  (NEW file)")
    print(f"## done & deterministic")
    print(f"  - accessible field names (/TU from schema label): {done['tu_set']}/{done['tu_total']}")
    print(f"  - document title set ('{title}') + DisplayDocTitle on")
    print(f"  - /Lang = {a.lang}")
    print(f"  - tab order = /S (logical) on every page")
    print(f"## still needs Adobe Auto-Tag or manual Acrobat (not faked here)")
    print(f"  - full content tag tree (PDF/UA 7.1/7.2: P/H/L/Table structure + reading order)")
    print(f"  - per-field association into that tag tree; artifact marking of static text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
