"""Strip checkbox/radio widgets that sit on top of pre-marked source glyphs.

Some forms (PB-007 is one) use a ⊠ Wingdings/Symbol character as a decorative
bullet marker for paragraph lists. The geometric checkbox detector picks
those up as fillable-checkbox candidates and the writer places widgets there.
The result: every bullet shows as a permanently-checked box that the user
can't toggle off (the X is page content, not a widget appearance).

This filter renders each checkbox candidate's bbox from the SOURCE PDF
(without our widgets) and measures the dark-pixel ratio inside. An empty
fillable checkbox is mostly white with a thin border (~10-15% dark pixels
near the border). A pre-marked ⊠ adds two diagonal strokes filling the
interior (~25%+ dark pixels). Above threshold, strip the widget.

This is deterministic and form-agnostic — no VLM needed.
"""
from __future__ import annotations

import argparse
import io
import pathlib
import sys

import fitz
from PIL import Image

# Threshold: fraction of dark pixels inside the rect where we consider the
# bbox "already marked" and drop the widget. Tuned by measurement on PB-007:
# the form uses a ⊠-style Symbol-font glyph as its UNCHECKED-checkbox visual
# indicator on every option, which lands legitimate widgets at ~25-35% dark
# pixels. Decorative paragraph-bullet widgets (where text body wraps inside
# the candidate's rect) come out tighter — ~45-47%. The 0.40 threshold
# isolates body-bullet phantoms without touching real form options.
DARK_RATIO_THRESHOLD = 0.40

# DPI for the per-widget render. 200 gives ~28x28px for a 10pt box — enough
# resolution to distinguish "border only" from "border + diagonals".
RENDER_DPI = 200

# A pixel counts as "dark" if its grayscale value (0=black..255=white) is
# below this. ⊠ strokes render around 0-100; antialiased borders 100-200.
DARK_PX_VALUE = 200


def render_source_bbox(source_doc: fitz.Document, page_no: int,
                       rect: fitz.Rect, dpi: int = RENDER_DPI) -> Image.Image:
    """Render a small region of the source page at given DPI."""
    page = source_doc[page_no]
    pix = page.get_pixmap(dpi=dpi, clip=rect, alpha=False)
    return Image.frombytes("L" if pix.n == 1 else "RGB",
                           (pix.width, pix.height), pix.samples).convert("L")


def dark_pixel_ratio(img: Image.Image) -> float:
    """Fraction of pixels darker than DARK_PX_VALUE."""
    px = img.load()
    w, h = img.size
    if w * h == 0:
        return 0.0
    dark = sum(1 for y in range(h) for x in range(w) if px[x, y] < DARK_PX_VALUE)
    return dark / (w * h)


def strip_phantoms(target_pdf: pathlib.Path, source_pdf: pathlib.Path,
                   out_pdf: pathlib.Path,
                   threshold: float = DARK_RATIO_THRESHOLD,
                   verbose: bool = False) -> tuple[int, int]:
    """Returns (stripped, total_checked)."""
    target = fitz.open(target_pdf)
    source = fitz.open(source_pdf)
    stripped = 0
    total = 0
    for pno in range(target.page_count):
        page = target[pno]
        # Snapshot the widgets first; deleting during iteration is awkward.
        widgets_to_check = []
        for w in (page.widgets() or []):
            if w.field_type_string in ("CheckBox", "RadioButton"):
                widgets_to_check.append((w.field_name, w.field_type_string,
                                          fitz.Rect(w.rect)))
        for name, kind, rect in widgets_to_check:
            total += 1
            img = render_source_bbox(source, pno, rect)
            ratio = dark_pixel_ratio(img)
            if ratio >= threshold:
                # Strip — find the widget again and delete it.
                for w in list(page.widgets() or []):
                    if (w.field_name == name
                            and w.field_type_string == kind
                            and abs(w.rect.x0 - rect.x0) < 0.5
                            and abs(w.rect.y0 - rect.y0) < 0.5):
                        page.delete_widget(w)
                        stripped += 1
                        if verbose:
                            print(f"  STRIP p{pno} {kind:<12} "
                                  f"name={name!r:<48} ratio={ratio:.2%}")
                        break
            elif verbose:
                print(f"  keep  p{pno} {kind:<12} name={name!r:<48} ratio={ratio:.2%}")
    if out_pdf.resolve() == target_pdf.resolve():
        target.save(out_pdf, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    else:
        target.save(out_pdf, deflate=True)
    target.close()
    source.close()
    return stripped, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=pathlib.Path,
                    help="The fused/fillable PDF to strip widgets from.")
    ap.add_argument("--source", type=pathlib.Path, required=True,
                    help="The original (no-widgets) source PDF.")
    ap.add_argument("--out", type=pathlib.Path,
                    help="Output PDF path (defaults to overwriting target).")
    ap.add_argument("--threshold", type=float, default=DARK_RATIO_THRESHOLD,
                    help=f"Dark-pixel ratio threshold (default {DARK_RATIO_THRESHOLD})")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.target.exists():
        print(f"missing: {args.target}", file=sys.stderr)
        return 2
    if not args.source.exists():
        print(f"missing: {args.source}", file=sys.stderr)
        return 2
    out = args.out or args.target.with_name(args.target.stem + "_stripped.pdf")
    stripped, total = strip_phantoms(
        args.target, args.source, out,
        threshold=args.threshold, verbose=args.verbose,
    )
    print(f"\nstripped {stripped}/{total} widgets")
    print(f"output: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
