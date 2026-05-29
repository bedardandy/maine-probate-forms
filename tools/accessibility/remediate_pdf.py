#!/usr/bin/env python3
"""Deterministic, safe accessibility remediation for born-digital PDFs.

Scope (honest): this does the document-level items OSS tooling can do RELIABLY and
deterministically. It does NOT synthesize the logical structure/tag tree (H1-H6,
lists, tables with TH/scope, reading order, artifacts) — no open-source tool does
that dependably for an untagged PDF; that needs Adobe Auto-Tag or manual Acrobat.
It deliberately does NOT set /MarkInfo /Marked true over a fake tree (that would
fail veraPDF UA-1 and mislead assistive tech).

  inspect:   report born-digital vs scanned, fonts, existing tags, /Lang, title,
             and a heuristic heading/list guess. Changes nothing.
  remediate: write a NEW file with: doc title (Info + XMP) + DisplayDocTitle,
             /Lang, outline/bookmarks from detected headings, URL->link annots.
             Never overwrites the input.

    python3 remediate_pdf.py inspect  in.pdf
    python3 remediate_pdf.py remediate in.pdf out.pdf [--lang en-US] [--title "..."]
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
import pathlib

import fitz          # PyMuPDF
import pikepdf

URL_RE = re.compile(r"(https?://[^\s)\]}>,]+)")


def inspect(path):
    doc = fitz.open(path)
    rep = {"pages": doc.page_count, "scanned_pages": [], "born_digital": True,
           "fonts": set(), "headings": [], "url_count": 0}
    sizes = []
    for i, pg in enumerate(doc):
        txt = pg.get_text("text")
        imgs = pg.get_images()
        if len(txt.strip()) < 20 and imgs:
            rep["scanned_pages"].append(i)
        for f in pg.get_fonts():
            rep["fonts"].add(f[3])
        d = pg.get_text("dict")
        for b in d.get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    sizes.append(round(sp["size"], 1))
        rep["url_count"] += len(URL_RE.findall(txt))
    if rep["scanned_pages"]:
        rep["born_digital"] = len(rep["scanned_pages"]) < doc.page_count
    # heading heuristic: spans notably larger than the body median size
    body = statistics.median(sizes) if sizes else 0
    if sizes:
        for i, pg in enumerate(doc):
            for b in pg.get_text("dict").get("blocks", []):
                for ln in b.get("lines", []):
                    for sp in ln.get("spans", []):
                        t = sp["text"].strip()
                        if t and sp["size"] >= body * 1.25 and len(t) < 120:
                            rep["headings"].append((i, round(sp["size"], 1), t[:70]))
    # existing tags / lang / title via pikepdf
    with pikepdf.open(path) as p:
        root = p.Root
        rep["has_struct_tree"] = "/StructTreeRoot" in root
        rep["marked"] = bool(root.get("/MarkInfo", {}).get("/Marked", False)) \
            if "/MarkInfo" in root else False
        rep["lang"] = str(root.get("/Lang", "")) or None
        rep["title"] = str(p.docinfo.get("/Title", "")) or None
        rep["display_doc_title"] = bool(
            root.get("/ViewerPreferences", {}).get("/DisplayDocTitle", False)) \
            if "/ViewerPreferences" in root else False
    rep["fonts"] = sorted(rep["fonts"])
    rep["body_font_size"] = body
    doc.close()
    return rep


def print_inspect(path, rep):
    print(f"# Inspect: {path}")
    print(f"- pages: {rep['pages']}")
    print(f"- born-digital: {rep['born_digital']}"
          + (f"  (scanned pages: {rep['scanned_pages']} -> need OCR, separate decision)"
             if rep['scanned_pages'] else "  (all pages have extractable text)"))
    print(f"- existing tag tree (StructTreeRoot): {rep['has_struct_tree']}; "
          f"/MarkInfo Marked: {rep['marked']}")
    print(f"- /Lang: {rep['lang']!r}; doc Title: {rep['title']!r}; "
          f"DisplayDocTitle: {rep['display_doc_title']}")
    print(f"- fonts ({len(rep['fonts'])}): {', '.join(rep['fonts'][:8])}"
          + (" ..." if len(rep['fonts']) > 8 else ""))
    print(f"- body font size ~{rep['body_font_size']}; "
          f"candidate headings: {len(rep['headings'])}; URLs found: {rep['url_count']}")
    for pgno, sz, t in rep["headings"][:10]:
        print(f"    p{pgno} {sz}pt  {t}")


def remediate(inp, outp, lang, title):
    rep = inspect(inp)
    notes = {"done": [], "needs_review": [], "cannot": []}

    doc = fitz.open(inp)
    # --- outline/bookmarks from detected headings (heuristic) ---
    if rep["headings"]:
        # dedup consecutive, build a flat level-1 toc (size-based level would be guessy)
        toc, seen = [], set()
        sizes = sorted({h[1] for h in rep["headings"]}, reverse=True)
        level_of = {s: min(3, i + 1) for i, s in enumerate(sizes)}
        for pgno, sz, t in rep["headings"]:
            key = (pgno, t)
            if key in seen:
                continue
            seen.add(key)
            toc.append([level_of.get(sz, 3), t, pgno + 1])
        if toc:
            doc.set_toc(toc)
            notes["done"].append(f"outline/bookmarks: {len(toc)} entries "
                                 "(heuristic from font sizes — spot-check)")
            notes["needs_review"].append("bookmark hierarchy/labels (auto from heading sizes)")
    else:
        notes["cannot"].append("outline: no headings detected by font-size heuristic")

    # --- URL text -> link annotations ---
    links = 0
    for pg in doc:
        words = pg.get_text("words")
        for w in words:
            m = URL_RE.search(w[4])
            if m:
                rect = fitz.Rect(w[:4])
                pg.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": m.group(1)})
                links += 1
    if links:
        notes["done"].append(f"URL->link annotations: {links}")
        notes["needs_review"].append("link purpose/descriptive text (annot has URI only)")

    tmp = str(pathlib.Path(outp).with_suffix(".tmp.pdf"))
    doc.save(tmp)
    doc.close()

    # --- metadata title + DisplayDocTitle + /Lang via pikepdf ---
    final_title = title or rep["title"] or (rep["headings"][0][2] if rep["headings"]
                                            else pathlib.Path(inp).stem)
    with pikepdf.open(tmp) as p:
        with p.open_metadata(set_pikepdf_as_editor=False) as xmp:
            xmp["dc:title"] = final_title
        p.docinfo["/Title"] = final_title
        vp = p.Root.get("/ViewerPreferences", pikepdf.Dictionary())
        vp["/DisplayDocTitle"] = True
        p.Root["/ViewerPreferences"] = vp
        p.Root["/Lang"] = pikepdf.String(lang)
        p.save(outp)
    pathlib.Path(tmp).unlink(missing_ok=True)
    notes["done"].append(f"document title set ('{final_title}') + DisplayDocTitle on")
    notes["done"].append(f"/Lang = {lang}")
    if title is None and not rep["title"]:
        notes["needs_review"].append(f"title was derived ('{final_title}') — confirm it's right")

    # --- the honest 'cannot (needs Adobe/manual)' bucket ---
    notes["cannot"] += [
        "logical tag tree: H1-H6 hierarchy / paragraphs / lists / tables (TH+scope) "
        "/ reading order / artifact marking — OSS cannot synthesize this reliably; "
        "needs Adobe Auto-Tag API or manual Acrobat. NOT faked here.",
        "alt text for images/figures — none invented (no Adobe; would need human-justified text)",
        "per-element /Lang for foreign-language runs — requires the tag tree",
        f"/MarkInfo Marked left {rep['marked']} — NOT set true without a real tag tree "
        "(would fail veraPDF UA-1)",
    ]
    if rep["scanned_pages"]:
        notes["cannot"].append(f"scanned pages {rep['scanned_pages']}: need OCR (ocrmypdf) first")
    return rep, notes


def print_report(inp, outp, rep, notes):
    print(f"# Remediation report\n- input:  {inp}\n- output: {outp} (NEW file; original untouched)\n")
    print("## (a) done & deterministic")
    for x in notes["done"]:
        print(f"  - {x}")
    print("\n## (b) done but NEEDS HUMAN REVIEW")
    for x in notes["needs_review"]:
        print(f"  - {x}")
    print("\n## (c) NOT auto-fixed (and why)")
    for x in notes["cannot"]:
        print(f"  - {x}")
    print("\nNext: validate with veraPDF (`verapdf --flavour ua1 OUT.pdf`); the tag "
          "tree (the bulk of WCAG 1.3.1 / PDF-UA structure) still needs Adobe "
          "Auto-Tag or Acrobat. This script does the safe document-level subset.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("inspect"); pi.add_argument("pdf")
    pr = sub.add_parser("remediate")
    pr.add_argument("pdf"); pr.add_argument("out")
    pr.add_argument("--lang", default="en-US"); pr.add_argument("--title", default=None)
    a = ap.parse_args()
    if a.cmd == "inspect":
        print_inspect(a.pdf, inspect(a.pdf))
    else:
        if pathlib.Path(a.out).resolve() == pathlib.Path(a.pdf).resolve():
            print("refusing to overwrite the original", file=sys.stderr); return 2
        rep, notes = remediate(a.pdf, a.out, a.lang, a.title)
        print_report(a.pdf, a.out, rep, notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
