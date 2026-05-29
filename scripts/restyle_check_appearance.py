"""Replace checkbox + radio AP/N streams with masked-square / corner-X style.

Some forms (PB-007) bake ⊠-style decorative glyphs into the page content as
their "this is a checkbox" visual indicator. Our AcroForm widget overlays
on top, but PyMuPDF's default appearances are:
  /Off — thin gray border only (transparent interior). Source ⊠ shows through.
  /Yes — same border + a ZapfDingbats char(3) ("✗") near the upper-left.

Result: the user sees the source ⊠ regardless of widget state, and there's
no clear visual difference between checked and unchecked. This script
rewrites every checkbox/radio widget's AP form-xobject streams to:

  /Off           — opaque white fill + thin black border. Masks the source
                   ⊠ underneath so an unchecked widget genuinely looks empty.
  /<on-state>    — opaque white fill + thin border + corner-to-corner thin X.
                   Matches the form's native ⊠ visual style so a "checked"
                   widget looks like a marked checkbox.

Run as a post-pass after the writer + radio promote.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import fitz


# Stream content for an empty (off) checkbox: white-fill the BBox, then
# stroke a thin black border at small inset. BBox is assumed [0 0 10 10] —
# every checkbox/radio kid in our pipeline uses that size, set by the
# AcroForm writer.
OFF_STREAM = (
    b"q\n"
    b"1 1 1 rg\n"
    b"0 0 10 10 re\n"
    b"f\n"
    b"0.5 w\n"
    b"0 0 0 RG\n"
    b"0.5 0.5 9 9 re\n"
    b"S\n"
    b"Q\n"
)

# "Checked" state: same opaque box plus thin diagonal X (corner-to-corner).
ON_STREAM = (
    b"q\n"
    b"1 1 1 rg\n"
    b"0 0 10 10 re\n"
    b"f\n"
    b"0.5 w\n"
    b"0 0 0 RG\n"
    b"0.5 0.5 9 9 re\n"
    b"S\n"
    b"1 1 m 9 9 l S\n"
    b"1 9 m 9 1 l S\n"
    b"Q\n"
)


def restyle(doc: fitz.Document, verbose: bool = False) -> tuple[int, int]:
    """Returns (widgets_restyled, streams_rewritten)."""
    widgets_restyled = 0
    streams_rewritten = 0
    seen_xrefs: set[int] = set()
    for pno in range(doc.page_count):
        page = doc[pno]
        for w in (page.widgets() or []):
            if w.field_type_string not in ("CheckBox", "RadioButton"):
                continue
            apn = doc.xref_get_key(w.xref, "AP/N")
            if apn[0] != "dict":
                continue
            entries = re.findall(r"/(\w+)\s+(\d+)\s+\d+\s+R", apn[1])
            if not entries:
                continue
            widgets_restyled += 1
            for state, sxr_str in entries:
                sxr = int(sxr_str)
                if sxr in seen_xrefs:
                    # Stream is shared with another widget we already restyled.
                    continue
                seen_xrefs.add(sxr)
                stream = OFF_STREAM if state == "Off" else ON_STREAM
                # Make sure the BBox is what we expect; if not, fall back to
                # leaving the stream alone rather than draw garbage. Parse
                # numerically: late-added widgets often carry float drift
                # (e.g. "0 0 9.999992 10" or "0 0 10.000015 10") that a strict
                # string match would reject.
                bbox = doc.xref_get_key(sxr, "BBox")
                if bbox[0] != "array":
                    if verbose:
                        print(f"  skip xref={sxr}: BBox={bbox} (not an array)")
                    continue
                try:
                    nums = [float(t) for t in bbox[1].strip("[]").split()]
                except ValueError:
                    nums = []
                if len(nums) != 4 or any(abs(a - b) > 0.01 for a, b in zip(nums, (0, 0, 10, 10))):
                    if verbose:
                        print(f"  skip xref={sxr}: BBox={bbox} (expected ~[0 0 10 10])")
                    continue
                doc.update_stream(sxr, stream)
                # The on-state form xobject originally referenced ZapfDingbats
                # via /Resources/Font/ZaDb. We don't need it anymore — clear
                # the resources so the form xobject is self-contained vector.
                if state != "Off":
                    doc.xref_set_key(sxr, "Resources", "<<>>")
                streams_rewritten += 1
                if verbose:
                    print(f"  restyle xref={sxr} state=/{state}")
    return widgets_restyled, streams_rewritten


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr)
        return 2
    out = args.out or args.pdf.with_name(args.pdf.stem + "_styled.pdf")

    doc = fitz.open(args.pdf)
    n_widgets, n_streams = restyle(doc, verbose=args.verbose)
    if out.resolve() == args.pdf.resolve():
        doc.save(out, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    else:
        doc.save(out, deflate=True)
    doc.close()
    print(f"\nrestyled {n_widgets} widgets ({n_streams} unique appearance streams)")
    print(f"output: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
