"""Visual fill+flatten demo for Patch E.

For a given fused PDF, produce a side-by-side composite (per page) showing:
  LEFT  — original AcroForm filled with placeholder values + flattened
  RIGHT — Patch-E-snapped version filled with the same values + flattened

Lets you eyeball whether geometric snap actually puts text on the underlines.

Usage:
  scripts/visual_snap_demo.py output_fused/guardian_minor/PB-007*.pdf [--out demo/pb007]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.geometric_snap import snap_widget_rect  # noqa: E402

RENDER_DPI = 150
COMPOSITE_GUTTER = 12  # pixels between LEFT and RIGHT panels


def fill_with_placeholders(pdf_path: pathlib.Path, out_path: pathlib.Path) -> int:
    """Fill every widget with its own field name (text/sig) or check (checkbox).
    Returns the count of widgets filled."""
    d = fitz.open(pdf_path)
    n = 0
    for page in d:
        for w in (page.widgets() or []):
            try:
                if w.field_type in (2, 5):  # CHECKBOX, RADIOBUTTON
                    w.field_value = True
                else:  # TEXT, SIGNATURE, etc.
                    name = w.field_name or "field"
                    # Cap to keep it readable inside the bbox.
                    w.field_value = name[:48]
                w.update()
                n += 1
            except Exception:
                pass
    out_path.parent.mkdir(parents=True, exist_ok=True)
    d.save(out_path, deflate=True)
    d.close()
    return n


def apply_snap(pdf_path: pathlib.Path, out_path: pathlib.Path) -> tuple[int, int]:
    """Run Patch E on every widget. Returns (snapped, total)."""
    d = fitz.open(pdf_path)
    snapped = 0
    total = 0
    for pno in range(d.page_count):
        page = d[pno]
        for w in (page.widgets() or []):
            total += 1
            # Pass widget_rect + widget_type so duplicate-named widgets each
            # get snapped to their own anchor (PB-007 has eight 'minor_name_row*'
            # widgets sharing names across upper and lower sections).
            new_rect = snap_widget_rect(
                pdf_path, pno, w.field_name,
                widget_rect=w.rect, widget_type=w.field_type,
            )
            if new_rect is None:
                continue
            try:
                w.rect = fitz.Rect(*new_rect)
                w.update()
                snapped += 1
            except Exception:
                pass
    out_path.parent.mkdir(parents=True, exist_ok=True)
    d.save(out_path, deflate=True)
    d.close()
    return snapped, total


def render_pages(pdf_path: pathlib.Path, dpi: int) -> list[Image.Image]:
    d = fitz.open(pdf_path)
    pages = []
    for page in d:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    d.close()
    return pages


def composite_side_by_side(left_pages: list[Image.Image],
                           right_pages: list[Image.Image],
                           label_left: str, label_right: str) -> list[Image.Image]:
    out = []
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    for L, R in zip(left_pages, right_pages):
        h = max(L.height, R.height)
        # Header strip with labels.
        header_h = 42
        total_w = L.width + COMPOSITE_GUTTER + R.width
        canvas = Image.new("RGB", (total_w, h + header_h), "white")
        canvas.paste(L, (0, header_h))
        canvas.paste(R, (L.width + COMPOSITE_GUTTER, header_h))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([(0, 0), (total_w, header_h)], fill="#222")
        draw.text((20, 6), label_left, fill="#ffaa66", font=font)
        draw.text((L.width + COMPOSITE_GUTTER + 20, 6),
                  label_right, fill="#66ddaa", font=font)
        # Vertical gutter line.
        draw.rectangle(
            [(L.width, header_h), (L.width + COMPOSITE_GUTTER, header_h + h)],
            fill="#888")
        out.append(canvas)
    return out


def run(pdf_in: pathlib.Path, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    work.mkdir(exist_ok=True)

    # 1. Original → fill → flatten → render
    orig_filled = work / "orig_filled.pdf"
    n_orig = fill_with_placeholders(pdf_in, orig_filled)

    # 2. Snap → fill → flatten → render
    snapped_pdf = work / "snapped.pdf"
    n_snapped, n_total = apply_snap(pdf_in, snapped_pdf)
    snap_filled = work / "snap_filled.pdf"
    fill_with_placeholders(snapped_pdf, snap_filled)

    print(f"Filled {n_orig} widgets in original; "
          f"snapped {n_snapped}/{n_total} widgets in v2.")

    # 3. Render both
    left_pages = render_pages(orig_filled, RENDER_DPI)
    right_pages = render_pages(snap_filled, RENDER_DPI)

    # 4. Side-by-side composite per page
    composites = composite_side_by_side(
        left_pages, right_pages,
        label_left=f"BEFORE (original AcroForm) — {pdf_in.stem[:60]}",
        label_right=f"AFTER (Patch E snap, {n_snapped}/{n_total}) "
                    f"— {pdf_in.stem[:60]}")
    for pno, img in enumerate(composites):
        out_path = out_dir / f"page_{pno:02d}.png"
        img.save(out_path, optimize=True)
        print(f"  wrote {out_path} ({img.width}x{img.height})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path("reports/snap-demo"))
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing {args.pdf}", file=sys.stderr)
        return 2
    out_dir = args.out / args.pdf.stem
    run(args.pdf, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
