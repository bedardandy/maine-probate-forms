"""Add a small "Clear" pushbutton next to each radio group.

Edge/PDFium silently enforces the NoToggleToOff radio behavior even when
the bit is clear, so users cannot deselect a chosen radio kid by clicking
it again. The standard PDF workaround is a button that fires a /ResetForm
action scoped to just that group's field name — universally supported
across viewers (Acrobat, Edge/PDFium, Firefox PDF.js, Preview).

For each radio /Btn parent in the AcroForm:
  * Compute the rect that bounds all of its kids on each page they appear on.
  * Add one pushbutton to the right of the rightmost kid on the LAST kid's
    page, with /A = << /S /ResetForm /Fields [(<group_T>)] >>.
  * Appearance is a small grey "clr" label inside a thin-bordered box —
    intentionally muted so it doesn't look like a checkable answer.

Run as a post-pass after the radio promote + restyle steps.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import fitz


CLR_BUTTON_WIDTH = 22.0
CLR_BUTTON_HEIGHT = 10.0
CLR_BUTTON_GAP = 6.0  # horizontal gap between rightmost kid and the button

# Appearance: thin border + grey "clr" label drawn in Helvetica.
CLR_STREAM = (
    b"q\n"
    b"1 1 1 rg\n"
    b"0 0 22 10 re\n"
    b"f\n"
    b"0.5 w\n"
    b"0.4 0.4 0.4 RG\n"
    b"0.5 0.5 21 9 re\n"
    b"S\n"
    b"BT\n"
    b"/Helv 7 Tf\n"
    b"0.4 0.4 0.4 rg\n"
    b"4 2 Td\n"
    b"(clr) Tj\n"
    b"ET\n"
    b"Q\n"
)


def _iter_radio_parents(doc: fitz.Document):
    """Yield (parent_xref, group_t, kid_xrefs) for every radio /Btn parent."""
    cat_xref = doc.pdf_catalog()
    typ, acroform = doc.xref_get_key(cat_xref, "AcroForm")
    if typ == "xref":
        m = re.match(r"(\d+) \d+ R", acroform)
        if not m:
            return
        af_xref = int(m.group(1))
        typ_f, fields_val = doc.xref_get_key(af_xref, "Fields")
        if typ_f != "array":
            return
        fields_str = fields_val
    elif typ == "dict":
        m = re.search(r"/Fields\s*\[([^\]]*)\]", acroform)
        if not m:
            return
        fields_str = "[" + m.group(1) + "]"
    else:
        return

    refs = re.findall(r"(\d+) \d+ R", fields_str)
    for r in refs:
        xr = int(r)
        ft = doc.xref_get_key(xr, "FT")
        if ft != ("name", "/Btn"):
            continue
        ff = doc.xref_get_key(xr, "Ff")
        try:
            ffv = int(ff[1]) if ff[0] == "int" else 0
        except ValueError:
            ffv = 0
        if not (ffv & 32768):  # radio bit
            continue
        kids = doc.xref_get_key(xr, "Kids")
        if kids[0] != "array":
            continue
        kid_xrefs = [int(k) for k in re.findall(r"(\d+) \d+ R", kids[1])]
        t = doc.xref_get_key(xr, "T")
        gname = ""
        if t[0] == "string":
            gname = t[1].strip("()")
        yield xr, gname, kid_xrefs


def _kid_page_and_rect(doc: fitz.Document, kid_xref: int):
    """Return (page_index, fitz.Rect) for a kid widget."""
    rect_v = doc.xref_get_key(kid_xref, "Rect")
    if rect_v[0] != "array":
        return None
    nums = [float(t) for t in rect_v[1].strip("[]").split()]
    if len(nums) != 4:
        return None
    rect = fitz.Rect(nums)
    for pno in range(doc.page_count):
        for w in (doc[pno].widgets() or []):
            if w.xref == kid_xref:
                return pno, rect
    return None


def add_clear_buttons(doc: fitz.Document, verbose: bool = False) -> int:
    added = 0
    new_field_xrefs: list[int] = []
    # Phase 1: read-only collection. Building a {kid_xref: (page, rect)} map
    # up front avoids re-iterating page.widgets() after we start adding widgets
    # — PyMuPDF chokes if widgets() encounters a partially-registered xref.
    kid_locations: dict[int, tuple[int, fitz.Rect]] = {}
    for pno in range(doc.page_count):
        for w in (doc[pno].widgets() or []):
            kid_locations[w.xref] = (pno, fitz.Rect(w.rect))

    parents = list(_iter_radio_parents(doc))

    # Phase 2: write pass.
    for parent_xref, gname, kid_xrefs in parents:
        if not kid_xrefs or not gname:
            continue
        per_page: dict[int, list[fitz.Rect]] = {}
        for k in kid_xrefs:
            info = kid_locations.get(k)
            if info is None:
                continue
            pno, r = info
            per_page.setdefault(pno, []).append(r)
        if not per_page:
            continue
        # Place on the page of the last kid (highest page number; tiebreak: lowest y).
        target_pno = max(per_page.keys())
        rects = per_page[target_pno]
        rightmost = max(rects, key=lambda r: r.x1)
        # Anchor the button to the rightmost kid's row.
        bx0 = rightmost.x1 + CLR_BUTTON_GAP
        by0 = rightmost.y0
        bx1 = bx0 + CLR_BUTTON_WIDTH
        by1 = by0 + CLR_BUTTON_HEIGHT
        # Clamp into page bounds; if no horizontal room, place below.
        page = doc[target_pno]
        if bx1 > page.rect.x1 - 4:
            bx0 = rightmost.x0
            by0 = rightmost.y1 + 2
            bx1 = bx0 + CLR_BUTTON_WIDTH
            by1 = by0 + CLR_BUTTON_HEIGHT

        # Create the appearance form xobject.
        ap_xref = doc.get_new_xref()
        doc.update_object(ap_xref, (
            "<<\n"
            "/Type /XObject\n"
            "/Subtype /Form\n"
            "/FormType 1\n"
            f"/BBox [0 0 {CLR_BUTTON_WIDTH:g} {CLR_BUTTON_HEIGHT:g}]\n"
            "/Resources << /Font << /Helv << /Type /Font /Subtype /Type1 "
            "/BaseFont /Helvetica >> >> >>\n"
            ">>"
        ))
        doc.update_stream(ap_xref, CLR_STREAM)

        # Create the widget annotation.
        # /F flags: bit 3 (4) = Print. We DELIBERATELY clear it so the
        # clr buttons display on screen but disappear when the form is
        # printed — they are an interactive aid, not part of the legal
        # document. /F 0 (or omitting /F entirely) achieves screen-only.
        btn_xref = doc.get_new_xref()
        doc.update_object(btn_xref, (
            "<<\n"
            "/Type /Annot\n"
            "/Subtype /Widget\n"
            "/FT /Btn\n"
            "/Ff 65536\n"  # pushbutton
            f"/T (clear_{gname})\n"
            "/TU (Clear this answer)\n"
            f"/Rect [{bx0:g} {by0:g} {bx1:g} {by1:g}]\n"
            f"/A << /S /ResetForm /Fields [({gname})] >>\n"
            f"/AP << /N {ap_xref} 0 R >>\n"
            "/BS << /W 0 /S /S >>\n"
            "/F 0\n"   # screen-visible, NOT printed
            ">>"
        ))

        # Register on the page's /Annots array.
        page_xref = page.xref
        annots_v = doc.xref_get_key(page_xref, "Annots")
        if annots_v[0] == "array":
            new_arr = annots_v[1].rstrip("]") + f" {btn_xref} 0 R]"
            doc.xref_set_key(page_xref, "Annots", new_arr)
        elif annots_v[0] == "xref":
            m = re.match(r"(\d+) \d+ R", annots_v[1])
            if m:
                arr_xref = int(m.group(1))
                arr_v = doc.xref_get_key(arr_xref, "")  # whole object
                # Update via update_object: read existing via xref_object
                obj_str = doc.xref_object(arr_xref)
                new_obj = obj_str.rstrip("]\n ") + f" {btn_xref} 0 R]"
                doc.update_object(arr_xref, new_obj)
        else:
            doc.xref_set_key(page_xref, "Annots", f"[{btn_xref} 0 R]")

        new_field_xrefs.append(btn_xref)
        added += 1
        if verbose:
            print(f"  + clear[{gname}] on page {target_pno} "
                  f"rect=[{bx0:.1f},{by0:.1f},{bx1:.1f},{by1:.1f}]")

    # Register the new pushbuttons in /AcroForm/Fields so PDF readers see them.
    if new_field_xrefs:
        cat_xref = doc.pdf_catalog()
        typ, acroform = doc.xref_get_key(cat_xref, "AcroForm")
        new_refs_str = " ".join(f"{x} 0 R" for x in new_field_xrefs)
        if typ == "xref":
            m = re.match(r"(\d+) \d+ R", acroform)
            af_xref = int(m.group(1))
            typ_f, fields_val = doc.xref_get_key(af_xref, "Fields")
            if typ_f == "array":
                new_arr = fields_val.rstrip("]") + " " + new_refs_str + "]"
                doc.xref_set_key(af_xref, "Fields", new_arr)
        elif typ == "dict":
            new_acroform = re.sub(
                r"(/Fields\s*\[)([^\]]*)(\])",
                lambda m_: m_.group(1) + m_.group(2) + " " + new_refs_str + m_.group(3),
                acroform,
                count=1,
            )
            doc.xref_set_key(cat_xref, "AcroForm", new_acroform)

    return added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr)
        return 2
    out = args.out or args.pdf.with_name(args.pdf.stem + "_clr.pdf")

    doc = fitz.open(args.pdf)
    n = add_clear_buttons(doc, verbose=args.verbose)
    if out.resolve() == args.pdf.resolve():
        doc.save(out, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    else:
        doc.save(out, deflate=True)
    doc.close()
    print(f"\nadded {n} clear button(s)")
    print(f"output: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
