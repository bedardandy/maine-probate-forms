"""Inject a doc-level Validate pushbutton into a fillable PDF.

The button:
  * Lives on the LAST page in the bottom-right margin so it's always
    findable but doesn't crowd the form body.
  * Has /F = 0 (no Print bit) so it shows on screen but vanishes when
    printed — it's an interactive aid, not part of the legal document.
  * /A is a /JavaScript action that runs the supplied .js file's
    `validateForm()` function.
  * Reset button option (--reset) adds a second pushbutton that resets
    every field via /A /S /ResetForm.

After the user clicks Validate, app.alert() shows either "all checks
passed" or a list of warnings. The attorney can save anyway — we never
block the save workflow.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import fitz


BTN_W = 90.0
BTN_H = 20.0
BTN_GAP = 8.0
PAGE_MARGIN = 36.0  # half-inch from the page edges


def _appearance_stream(label: str, fill_rgb: tuple[float, float, float]) -> bytes:
    r, g, b = fill_rgb
    return (
        f"q\n"
        f"{r} {g} {b} rg\n"
        f"0 0 {BTN_W} {BTN_H} re\n"
        f"f\n"
        f"0.6 w\n"
        f"0 0 0 RG\n"
        f"0.5 0.5 {BTN_W - 1} {BTN_H - 1} re\n"
        f"S\n"
        f"BT\n"
        f"/Helv 11 Tf\n"
        f"0 0 0 rg\n"
        f"6 6 Td\n"
        f"({label}) Tj\n"
        f"ET\n"
        f"Q\n"
    ).encode("latin1")


def _make_appearance_xref(doc: fitz.Document, label: str,
                          rgb: tuple[float, float, float]) -> int:
    ap_xref = doc.get_new_xref()
    doc.update_object(ap_xref, (
        "<<\n"
        "/Type /XObject\n"
        "/Subtype /Form\n"
        "/FormType 1\n"
        f"/BBox [0 0 {BTN_W:g} {BTN_H:g}]\n"
        "/Resources << /Font << /Helv << /Type /Font /Subtype /Type1 "
        "/BaseFont /Helvetica >> >> >>\n"
        ">>"
    ))
    doc.update_stream(ap_xref, _appearance_stream(label, rgb))
    return ap_xref


def _add_button(doc: fitz.Document, page_no: int, x0: float, y0: float,
                label: str, action_dict: str,
                rgb: tuple[float, float, float],
                field_name: str) -> int:
    page = doc[page_no]
    ap_xref = _make_appearance_xref(doc, label, rgb)
    btn_xref = doc.get_new_xref()
    doc.update_object(btn_xref, (
        "<<\n"
        "/Type /Annot\n"
        "/Subtype /Widget\n"
        "/FT /Btn\n"
        "/Ff 65536\n"   # pushbutton
        f"/T ({field_name})\n"
        f"/Rect [{x0:g} {y0:g} {x0 + BTN_W:g} {y0 + BTN_H:g}]\n"
        f"/A {action_dict}\n"
        f"/AP << /N {ap_xref} 0 R >>\n"
        "/BS << /W 0 /S /S >>\n"
        "/F 0\n"   # screen-visible, NOT printed
        ">>"
    ))

    # Append to page /Annots.
    page_xref = page.xref
    annots_v = doc.xref_get_key(page_xref, "Annots")
    if annots_v[0] == "array":
        new_arr = annots_v[1].rstrip("]") + f" {btn_xref} 0 R]"
        doc.xref_set_key(page_xref, "Annots", new_arr)
    elif annots_v[0] == "xref":
        m = re.match(r"(\d+) \d+ R", annots_v[1])
        if m:
            arr_xref = int(m.group(1))
            obj_str = doc.xref_object(arr_xref)
            new_obj = obj_str.rstrip("]\n ") + f" {btn_xref} 0 R]"
            doc.update_object(arr_xref, new_obj)
    else:
        doc.xref_set_key(page_xref, "Annots", f"[{btn_xref} 0 R]")

    # Register in /AcroForm/Fields so PDF readers see the action.
    cat = doc.pdf_catalog()
    typ, val = doc.xref_get_key(cat, "AcroForm")
    new_ref = f" {btn_xref} 0 R"
    if typ == "dict":
        rewritten = re.sub(
            r"(/Fields\s*\[)([^\]]*)(\])",
            lambda mm: mm.group(1) + mm.group(2) + new_ref + mm.group(3),
            val, count=1,
        )
        doc.xref_set_key(cat, "AcroForm", rewritten)
    elif typ == "xref":
        m = re.match(r"(\d+) \d+ R", val)
        if m:
            af_xref = int(m.group(1))
            typ_f, fields_val = doc.xref_get_key(af_xref, "Fields")
            if typ_f == "array":
                new_arr = fields_val.rstrip("]") + new_ref + "]"
                doc.xref_set_key(af_xref, "Fields", new_arr)
    return btn_xref


def _escape_js_for_pdf_string(js: str) -> str:
    """PDF /JavaScript string content needs balanced parens (or use hex/literal).
    Since our generated JS may contain parens, we use a literal string with
    \\ escaping. Backslash, paren, and CR/LF need escaping per PDF spec.
    """
    return (js.replace("\\", "\\\\")
              .replace("(", "\\(")
              .replace(")", "\\)")
              .replace("\r", "")
              .replace("\n", "\\n"))


def add_buttons(doc: fitz.Document, js: str,
                add_reset: bool = True, verbose: bool = False) -> None:
    last_page = doc.page_count - 1
    page = doc[last_page]
    pw, ph = page.rect.width, page.rect.height

    # Bottom-right placement. PDF spec origin is bottom-left with y-up,
    # so "near the bottom of the visible page" means y0 close to 0.
    # PAGE_MARGIN above the page bottom keeps the buttons clear of the
    # printed page edge.
    btn_y0 = PAGE_MARGIN
    validate_x0 = pw - PAGE_MARGIN - BTN_W
    js_escaped = _escape_js_for_pdf_string(js)

    _add_button(
        doc, last_page, validate_x0, btn_y0,
        label="Validate",
        action_dict=f"<< /S /JavaScript /JS ({js_escaped}) >>",
        rgb=(0.85, 0.92, 1.0),  # pale blue
        field_name="validate_form_btn",
    )
    if verbose:
        print(f"  + Validate at page {last_page + 1} "
              f"rect=[{validate_x0:.1f},{btn_y0:.1f}]")

    if add_reset:
        reset_x0 = validate_x0 - BTN_GAP - BTN_W
        _add_button(
            doc, last_page, reset_x0, btn_y0,
            label="Reset Form",
            action_dict="<< /S /ResetForm >>",
            rgb=(1.0, 0.92, 0.88),  # pale orange
            field_name="reset_form_btn",
        )
        if verbose:
            print(f"  + Reset at page {last_page + 1} "
                  f"rect=[{reset_x0:.1f},{btn_y0:.1f}]")

    # Make sure the doc has /AcroForm/NeedAppearances set so freshly-added
    # appearances render on first open.
    cat = doc.pdf_catalog()
    typ, val = doc.xref_get_key(cat, "AcroForm")
    if typ == "dict" and "/NeedAppearances" not in val:
        new_val = val.replace("<<", "<</NeedAppearances true", 1)
        doc.xref_set_key(cat, "AcroForm", new_val)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("validate_js", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--no-reset", action="store_true",
                    help="Skip the Reset Form button (Validate only).")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr); return 2
    if not args.validate_js.exists():
        print(f"missing: {args.validate_js}", file=sys.stderr); return 2

    js = args.validate_js.read_text()
    doc = fitz.open(args.pdf)
    add_buttons(doc, js, add_reset=not args.no_reset, verbose=args.verbose)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.resolve() == args.pdf.resolve():
        doc.save(args.out, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    else:
        doc.save(args.out, deflate=True)
    doc.close()
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
