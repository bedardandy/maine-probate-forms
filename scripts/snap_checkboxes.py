#!/usr/bin/env python3
"""Snap each checkbox widget rect to the printed box outline on the page.

For each /Btn CheckBox widget, render a high-DPI crop around its rect (with
the widget annotation NOT drawn). Find the printed box by taking the
bounding rectangle of the largest dark connected component in the crop. The
"largest connected component near the widget center" reliably picks the
box itself rather than nearby label text.

Optionally clamp how far the widget can move — a snap distance > N points
usually means we picked up the wrong feature, so we skip rather than land
the widget on a label.
"""
from __future__ import annotations
import argparse
import pathlib
import sys
import fitz
import numpy as np
from scipy import ndimage


def render_crop(page: fitz.Page, rect: fitz.Rect, *, zoom: float,
                margin: float) -> tuple[np.ndarray, fitz.Rect]:
    expanded = fitz.Rect(rect.x0 - margin, rect.y0 - margin,
                         rect.x1 + margin, rect.y1 + margin) & page.rect
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=expanded,
                          annots=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )
    if pix.n >= 3:
        gray = (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1]
                + 0.114 * img[:, :, 2]).astype(np.uint8)
    else:
        gray = img[:, :, 0]
    binary = (gray < 160).astype(np.uint8)
    return binary, expanded


def find_box_bbox(binary: np.ndarray, *, min_fill: int = 40
                  ) -> tuple[int, int, int, int] | None:
    """Return (x0, y0, x1, y1) of the largest connected dark component
    that contains the crop center. None if no qualifying component."""
    labels, n = ndimage.label(binary)
    if n == 0:
        return None
    h, w = binary.shape
    cy, cx = h // 2, w // 2
    sizes = ndimage.sum_labels(binary, labels, range(1, n + 1))
    candidates = []
    for i in range(1, n + 1):
        size = int(sizes[i - 1])
        if size < min_fill:
            continue
        ys, xs = np.where(labels == i)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        contains_center = x0 <= cx <= x1 and y0 <= cy <= y1
        candidates.append((contains_center, size, x0, y0, x1, y1))
    if not candidates:
        return None
    # Prefer components that contain the center; among those, the largest.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, x0, y0, x1, y1 = candidates[0]
    return x0, y0, x1, y1


def find_inner_bbox(binary: np.ndarray,
                    outer: tuple[int, int, int, int]
                    ) -> tuple[int, int, int, int] | None:
    """If the outer dark feature encloses a white interior, return that
    interior's bbox. Returns None for incomplete shapes (e.g. L-shape,
    open rectangle) where no fully enclosed white region exists."""
    ox0, oy0, ox1, oy1 = outer
    crop = binary[oy0:oy1 + 1, ox0:ox1 + 1]
    h, w = crop.shape
    if h < 4 or w < 4:
        return None
    # White pixels = 1 (inverted from binary)
    white = (crop == 0).astype(np.uint8)
    labels, n = ndimage.label(white)
    if n == 0:
        return None
    # An "interior hole" is a white component that does NOT touch any
    # edge of the crop — meaning it's surrounded by dark on all sides.
    interior: list[tuple[int, int, int, int, int]] = []
    for i in range(1, n + 1):
        ys, xs = np.where(labels == i)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        if x0 == 0 or y0 == 0 or x1 == w - 1 or y1 == h - 1:
            continue
        interior.append(((y1 - y0 + 1) * (x1 - x0 + 1), x0, y0, x1, y1))
    if not interior:
        return None
    interior.sort(reverse=True)
    _, x0, y0, x1, y1 = interior[0]
    return ox0 + x0, oy0 + y0, ox0 + x1, oy0 + y1


def snap_widget(page: fitz.Page, widget: fitz.Widget, *,
                zoom: float, margin: float, max_shift: float,
                snap_mode: str
                ) -> tuple[fitz.Rect | None, float, float, str]:
    binary, crop = render_crop(page, widget.rect, zoom=zoom, margin=margin)
    outer = find_box_bbox(binary)
    if outer is None:
        return None, 0.0, 0.0, "no-component"
    bbox = outer
    used_mode = "outer"
    if snap_mode == "inner":
        inner = find_inner_bbox(binary, outer)
        if inner is not None:
            bbox = inner
            used_mode = "inner"
    x0, y0, x1, y1 = bbox
    px_to_pt = 1.0 / zoom
    new_rect = fitz.Rect(
        crop.x0 + x0 * px_to_pt,
        crop.y0 + y0 * px_to_pt,
        crop.x0 + x1 * px_to_pt,
        crop.y0 + y1 * px_to_pt,
    )
    dx = new_rect.x0 - widget.rect.x0
    dy = new_rect.y0 - widget.rect.y0
    shift = max(abs(dx), abs(dy))
    if shift > max_shift:
        return None, dx, dy, f"shift {shift:.2f}pt > max"
    return new_rect, dx, dy, f"ok-{used_mode}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path,
                    help="Output PDF (default: overwrite input)")
    ap.add_argument("--zoom", type=float, default=10.0,
                    help="Render zoom for the crop (default 10x ≈ 720 DPI)")
    ap.add_argument("--margin", type=float, default=4.0,
                    help="Search margin around widget rect, in PDF points")
    ap.add_argument("--max-shift", type=float, default=3.0,
                    help="Max snap distance in points; larger → skip")
    ap.add_argument("--snap-mode", choices=["outer", "inner"], default="inner",
                    help="inner: snap to interior (so /Yes appearance fits "
                         "inside the printed stroke); outer: snap to outer "
                         "bbox of the dark stroke. Falls back to outer "
                         "automatically when no enclosed interior is found "
                         "(e.g. incomplete L-shaped boxes).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr)
        return 2

    doc = fitz.open(args.pdf)
    snapped = skipped = 0
    skip_reasons: dict[str, int] = {}
    total_dx = total_dy = 0.0
    for page in doc:
        for w in list(page.widgets()):
            if w.field_type != 2:  # CheckBox
                continue
            new_rect, dx, dy, reason = snap_widget(
                page, w, zoom=args.zoom, margin=args.margin,
                max_shift=args.max_shift, snap_mode=args.snap_mode,
            )
            if new_rect is None:
                skipped += 1
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                if args.verbose:
                    print(f"  skip p{page.number} {w.field_name!r}: {reason}")
                continue
            if args.verbose:
                print(f"  snap p{page.number} {w.field_name!r}: "
                      f"dx={dx:+.2f}pt dy={dy:+.2f}pt")
            total_dx += abs(dx)
            total_dy += abs(dy)
            if not args.dry_run:
                w.rect = new_rect
                w.update()
            snapped += 1

    avg_dx = total_dx / max(snapped, 1)
    avg_dy = total_dy / max(snapped, 1)
    print(f"snapped={snapped} skipped={skipped}")
    if skip_reasons:
        for r, n in skip_reasons.items():
            print(f"  skip:{r} = {n}")
    if snapped:
        print(f"avg shift |dx|={avg_dx:.2f}pt |dy|={avg_dy:.2f}pt")

    if args.dry_run:
        print("dry-run — not saving")
        return 0
    out = args.out or args.pdf
    doc.save(out, incremental=(out == args.pdf),
             encryption=fitz.PDF_ENCRYPT_KEEP)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
