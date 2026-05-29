#!/usr/bin/env python3
"""Embed open, metric-compatible substitutes for the unembedded fonts a filled
AcroForm leaves behind, and attach ToUnicode maps — the last mile to clean
PDF/UA-1. Pure OSS (pikepdf + fontTools); appearance preserved.

Three repairs, all PDF/UA font clauses:

  1. Widget text font (7.21.4.1) — PyMuPDF/fitz injects field text in base-14
     Helvetica, which is never embedded. Replace every unembedded Helvetica font
     object in place with an embedded TrueType built from Liberation Sans
     (metric-compatible with Helvetica/Arial), WinAnsi-encoded, with Widths +
     ToUnicode.
  2. Checkbox symbol font (7.21.4.1 + 7.21.6/4) — checkmarks use ZapfDingbats,
     also unembedded. Embed a ZapfDingbats TrueType via its byte-code (1,0) cmap,
     symbolic (Flags=4, no /Encoding), and strip its cmap to a single (1,0)
     subtable so the "symbolic TrueType: exactly one cmap or include MS-Symbol
     3,0" rule is satisfied. Optional — only runs if a ZapfDingbats TTF is found.
  3. ToUnicode (7.21.7) — gs-embedded subset fonts (e.g. Calibri/MS-Gothic from
     a re-distilled source) carry non-standard glyph names and no ToUnicode;
     attach a WinAnsi-derived ToUnicode to any simple font with WinAnsi (or no)
     encoding and no ToUnicode.

Run this LAST, after accessibility_pipeline.py. Font search paths are
overridable via env: WIDGET_TTF (Liberation Sans), ZAPF_TTF (ZapfDingbats).

    python3 embed_widget_font.py in.pdf out.pdf [--ttf <LiberationSans.ttf>]
"""
import argparse
import io
import os
import pathlib
import sys

import pikepdf
from fontTools.ttLib import TTFont

# Liberation Sans is the metric-compatible Helvetica/Arial substitute. Ships with
# fonts-liberation on Debian/Ubuntu; override with WIDGET_TTF if elsewhere.
_LIB_CANDIDATES = [
    os.environ.get("WIDGET_TTF"),
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
]
LIB = next((p for p in _LIB_CANDIDATES if p and pathlib.Path(p).exists()),
           "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")

# ZapfDingbats TTF for the checkbox symbol font (optional repair). Override with
# ZAPF_TTF; symbol embedding is skipped entirely if none is found.
_ZAPF_CANDIDATES = [
    os.environ.get("ZAPF_TTF"),
    "/usr/share/fonts/truetype/ttf-bitstream-vera/VeraMono.ttf",  # not ideal; placeholder
    "/System/Library/Fonts/ZapfDingbats.ttf",
    "/Library/Fonts/ZapfDingbats.ttf",
]
ZAPF = next((p for p in _ZAPF_CANDIDATES
             if p and "ZapfDingbats" in p and pathlib.Path(p).exists()), None)


def _metrics(ttf_path):
    f = TTFont(ttf_path)
    upm = f["head"].unitsPerEm
    cmap = f.getBestCmap()
    hmtx = f["hmtx"]
    def width_for(uni):
        gn = cmap.get(uni)
        if not gn:
            return 0, None
        adv = hmtx[gn][0]
        return round(adv * 1000 / upm), gn
    head = f["head"]; os2 = f["OS/2"]; post = f["post"]
    desc = {
        "bbox": [round(v * 1000 / upm) for v in (head.xMin, head.yMin, head.xMax, head.yMax)],
        "italic": post.italicAngle,
        "ascent": round(os2.sTypoAscender * 1000 / upm),
        "descent": round(os2.sTypoDescender * 1000 / upm),
        "capheight": round(getattr(os2, "sCapHeight", os2.sTypoAscender) * 1000 / upm),
    }
    return width_for, desc, f


def build_font(pdf, ttf_path):
    """Build an embedded WinAnsi TrueType (Liberation Sans) for the widget text."""
    width_for, d, _ = _metrics(ttf_path)
    raw = open(ttf_path, "rb").read()
    first, last = 32, 255
    widths, uni_map = [], {}
    for code in range(first, last + 1):
        try:
            ch = bytes([code]).decode("cp1252")  # WinAnsi ~= cp1252
            uni = ord(ch)
        except Exception:
            widths.append(0); continue
        w, gn = width_for(uni)
        widths.append(w)
        if gn:
            uni_map[code] = uni
    tou = pdf.make_stream(_tounicode_stream(uni_map).encode())
    ff = pdf.make_stream(raw); ff["/Length1"] = len(raw)
    fd = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": pikepdf.Name("/FontDescriptor"), "/FontName": pikepdf.Name("/LiberationSans"),
        "/Flags": 32, "/FontBBox": d["bbox"], "/ItalicAngle": d["italic"],
        "/Ascent": d["ascent"], "/Descent": d["descent"], "/CapHeight": d["capheight"],
        "/StemV": 80, "/FontFile2": ff}))
    return pikepdf.Dictionary({
        "/Type": pikepdf.Name("/Font"), "/Subtype": pikepdf.Name("/TrueType"),
        "/BaseFont": pikepdf.Name("/LiberationSans"), "/FirstChar": first, "/LastChar": last,
        "/Widths": widths, "/FontDescriptor": fd,
        "/Encoding": pikepdf.Name("/WinAnsiEncoding"), "/ToUnicode": tou})


def _tounicode_stream(uni_map):
    """Serialize a CMap mapping single-byte codes -> Unicode scalars."""
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "/CIDSystemInfo <</Registry (Adobe)/Ordering (UCS)/Supplement 0>> def",
             "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
             "1 begincodespacerange <00> <FF> endcodespacerange"]
    items = [f"<{c:02X}> <{u:04X}>" for c, u in sorted(uni_map.items())]
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        lines.append(f"{len(chunk)} beginbfchar"); lines += chunk; lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines)


def add_tounicode(pdf):
    """7.21.7: every used code must map to Unicode. gs-embedded subset fonts with
    non-standard glyph names lack that; attach a WinAnsi-derived ToUnicode to any
    simple font that has WinAnsi (or no) encoding and no ToUnicode."""
    uni = {}
    for code in range(32, 256):
        try:
            uni[code] = ord(bytes([code]).decode("cp1252"))
        except Exception:
            pass
    tou = None
    n = 0
    for obj in pdf.objects:
        if not (isinstance(obj, pikepdf.Dictionary) and str(obj.get("/Type", "")) == "/Font"):
            continue
        if "/ToUnicode" in obj:
            continue
        # simple (non-CID) fonts only; Type0/CID derive Unicode via the parent.
        if str(obj.get("/Subtype", "")) not in ("/Type1", "/TrueType"):
            continue
        enc = obj.get("/Encoding")
        # WinAnsi, or no explicit encoding (TrueType subsets gs leaves bare) — the
        # used codes are Latin form text, so a WinAnsi-derived map is correct.
        if not (str(enc) == "/WinAnsiEncoding" or enc is None):
            continue
        if tou is None:
            tou = pdf.make_indirect(pdf.make_stream(_tounicode_stream(uni).encode()))
        obj["/ToUnicode"] = tou
        n += 1
    return n


def build_symbol_font(pdf, ttf_path):
    """Embed a symbolic font (e.g. ZapfDingbats checkbox font) as TrueType using
    its byte-code (1,0) cmap. Symbolic: no /Encoding; ToUnicode via cmap reverse;
    cmap stripped to the single (1,0) subtable to satisfy 7.21.6/4."""
    f = TTFont(ttf_path)
    upm = f["head"].unitsPerEm
    hmtx = f["hmtx"]
    c10 = f["cmap"].getcmap(1, 0)
    code2glyph = c10.cmap if c10 else {}
    uni_rev = {}
    for tb in f["cmap"].tables:
        if tb.platformID in (0, 3):
            for u, g in tb.cmap.items():
                uni_rev.setdefault(g, u)
    keep = [t for t in f["cmap"].tables if t.platformID == 1 and t.platEncID == 0] \
        or f["cmap"].tables[:1]
    f["cmap"].tables = keep
    _buf = io.BytesIO(); f.save(_buf); raw = _buf.getvalue()
    first, last = 32, 255
    widths, uni_map = [], {}
    for code in range(first, last + 1):
        g = code2glyph.get(code)
        if g and g in hmtx.metrics:
            widths.append(round(hmtx[g][0] * 1000 / upm))
            if g in uni_rev:
                uni_map[code] = uni_rev[g]
        else:
            widths.append(0)
    head = f["head"]
    bbox = [round(v * 1000 / upm) for v in (head.xMin, head.yMin, head.xMax, head.yMax)]
    tou = pdf.make_stream(_tounicode_stream(uni_map).encode())
    ff = pdf.make_stream(raw); ff["/Length1"] = len(raw)
    fd = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": pikepdf.Name("/FontDescriptor"), "/FontName": pikepdf.Name("/ZapfDingbats"),
        "/Flags": 4, "/FontBBox": bbox, "/ItalicAngle": 0, "/Ascent": bbox[3],
        "/Descent": bbox[1], "/CapHeight": bbox[3], "/StemV": 80, "/FontFile2": ff}))
    return pikepdf.Dictionary({
        "/Type": pikepdf.Name("/Font"), "/Subtype": pikepdf.Name("/TrueType"),
        "/BaseFont": pikepdf.Name("/ZapfDingbats"), "/FirstChar": first, "/LastChar": last,
        "/Widths": widths, "/FontDescriptor": fd, "/ToUnicode": tou})


def _is_unembedded(obj, base):
    try:
        if str(obj.get("/Type", "")) != "/Font":
            return False
        if base not in str(obj.get("/BaseFont", "")):
            return False
        fdd = obj.get("/FontDescriptor")
        if fdd and any(k in fdd for k in ("/FontFile", "/FontFile2", "/FontFile3")):
            return False
        return True
    except Exception:
        return False


def is_unembedded_symbol(obj):
    return _is_unembedded(obj, "ZapfDingbats")


def is_unembedded_helvetica(obj):
    return _is_unembedded(obj, "Helvetica")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("out"); ap.add_argument("--ttf", default=LIB)
    ap.add_argument("--zapf", default=ZAPF, help="ZapfDingbats TTF (optional)")
    a = ap.parse_args()
    if a.out == a.pdf:
        print("refusing to overwrite", file=sys.stderr); return 2
    if not pathlib.Path(a.ttf).exists():
        print(f"widget font not found: {a.ttf} (set WIDGET_TTF or --ttf)", file=sys.stderr)
        return 2
    pdf = pikepdf.open(a.pdf)
    newfont = build_font(pdf, a.ttf)
    symfont = build_symbol_font(pdf, a.zapf) if (a.zapf and pathlib.Path(a.zapf).exists()) else None
    n = 0
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Dictionary):
            continue
        repl = newfont if is_unembedded_helvetica(obj) else (
            symfont if (symfont and is_unembedded_symbol(obj)) else None)
        if repl is not None:
            for k in list(obj.keys()):
                del obj[k]
            for k, v in repl.items():
                obj[k] = v
            n += 1
    nt = add_tounicode(pdf)
    pdf.save(a.out)
    sym = " (incl. ZapfDingbats)" if symfont else " (ZapfDingbats TTF not found — skipped)"
    print(f"embedded {n} unembedded font obj(s){sym}; added ToUnicode to {nt} font(s) -> {a.out}")


if __name__ == "__main__":
    raise SystemExit(main())
