#!/usr/bin/env python3
"""Snap text-field widget x-extents to the printed underscore line.

For each text widget, render a crop covering the current rect plus a wide
horizontal margin (so we can see if the underscore extends left or right of
the widget). Find the dominant horizontal underscore by looking for rows
with long contiguous dark runs — an underscore is one or two thin rows of
densely connected dark pixels, distinct from label glyph rows which are
sparse with letter gaps.

Snap widget x0 to the underscore start and x1 to the underscore end.
Preserve the widget's vertical position. If no clear underscore is found
(e.g. multi-line text areas, or the widget is in the middle of body text
with no underline), skip — better to leave the widget alone than land it
somewhere wrong.
"""
from __future__ import annotations
import argparse
import pathlib
import sys
import fitz
import numpy as np


def render_crop(page: fitz.Page, rect: fitz.Rect, *,
                zoom: float, margin_x: float, margin_y_above: float,
                margin_y_below: float
                ) -> tuple[np.ndarray, fitz.Rect]:
    expanded = fitz.Rect(rect.x0 - margin_x, rect.y0 - margin_y_above,
                         rect.x1 + margin_x,
                         rect.y1 + margin_y_below) & page.rect
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


def longest_run(row: np.ndarray, max_gap: int = 0) -> tuple[int, int, int]:
    """Longest run of dark pixels in a binary row, allowing up to `max_gap`
    bright pixels inside the run. Returns (length, start, end_inclusive)."""
    best = (0, 0, 0)
    n = len(row)
    i = 0
    while i < n:
        if row[i] == 0:
            i += 1
            continue
        start = i
        gap = 0
        last_dark = i
        j = i
        while j < n:
            if row[j]:
                last_dark = j
                gap = 0
            else:
                gap += 1
                if gap > max_gap:
                    break
            j += 1
        length = last_dark - start + 1
        if length > best[0]:
            best = (length, start, last_dark)
        i = j + 1
    return best


def find_underscore(binary: np.ndarray, *, min_length_px: int,
                    max_gap_px: int = 4) -> tuple[int, int, int] | None:
    """Find the most prominent underscore-like horizontal line.
    Returns (row_idx, x_start, x_end) in pixel coords, or None.

    An underscore is a long contiguous dark run on ONE row whose
    neighbors are mostly empty — distinguishes underscores from body
    text rows where letters give short runs even if the row is "wide"."""
    h, w = binary.shape
    best = (0, 0, 0, 0)  # (length, row, start, end)
    for r in range(h):
        length, s, e = longest_run(binary[r], max_gap=max_gap_px)
        if length > best[0]:
            best = (length, r, s, e)
    length, row, s, e = best
    if length < min_length_px:
        return None
    # Reject if the row above and below have similarly long runs (likely
    # a tall stroke or text block, not an underscore which is 1-2px thick).
    if 1 <= row < h - 1:
        above_len = longest_run(binary[row - 1], max_gap=max_gap_px)[0]
        below_len = longest_run(binary[row + 1], max_gap=max_gap_px)[0]
        if min(above_len, below_len) > length * 0.6:
            # Symmetric thick band — body text or shaded box, not an underscore
            return None
    return row, s, e


def snap_widget(page: fitz.Page, widget: fitz.Widget, *,
                zoom: float, margin_x: float, margin_y_above: float,
                margin_y_below: float, max_shift_x: float,
                max_shift_y: float, min_underscore_pt: float,
                canonical_height: float | None,
                narrow_width_pt: float,
                narrow_width_multiplier: float,
                ) -> tuple[fitz.Rect | None, float, float, str]:
    binary, crop = render_crop(page, widget.rect, zoom=zoom,
                               margin_x=margin_x,
                               margin_y_above=margin_y_above,
                               margin_y_below=margin_y_below)
    found = find_underscore(binary, min_length_px=int(min_underscore_pt * zoom))
    if found is None:
        return None, 0.0, 0.0, "no-underscore"
    underscore_row_px, x_start_px, x_end_px = found
    # Reject finds that pull the widget bottom too far from where it
    # currently sits — guards against grabbing the previous row's
    # underscore on tightly-spaced multi-line text.
    underscore_y_pt = crop.y0 + underscore_row_px / zoom
    if abs(underscore_y_pt - widget.rect.y1) > max_shift_y:
        return None, 0.0, 0.0, (
            f"underscore y {underscore_y_pt:.1f}pt is "
            f"{abs(underscore_y_pt - widget.rect.y1):.1f}pt from widget "
            f"bottom — wrong row"
        )
    px_to_pt = 1.0 / zoom
    new_x0 = crop.x0 + x_start_px * px_to_pt
    new_x1 = crop.x0 + (x_end_px + 1) * px_to_pt
    dx0 = new_x0 - widget.rect.x0
    dx1 = new_x1 - widget.rect.x1
    # Narrow-source guard: if the source widget is short (< narrow_width_pt),
    # the upstream detector usually already has it well-placed (short widgets
    # sit between adjacent words on the same line). Tighten both the width
    # tolerance and the shift tolerance to keep a narrow widget from being
    # captured by a longer adjacent underscore — fragmentally or whole.
    src_width = widget.rect.width
    snap_width = new_x1 - new_x0
    if src_width < narrow_width_pt:
        if snap_width > src_width * narrow_width_multiplier:
            return None, dx0, dx1, (
                f"narrow source ({src_width:.1f}pt) but snap target "
                f"is {snap_width:.1f}pt — likely wrong underscore"
            )
        src_center = (widget.rect.x0 + widget.rect.x1) / 2
        snap_center = (new_x0 + new_x1) / 2
        center_shift = abs(snap_center - src_center)
        # For a narrow widget, allow at most src_width of center shift —
        # that's enough to cover the worst legit case (underscore
        # ending exactly where the source widget ends but starting earlier),
        # but rejects shifts onto entirely separate adjacent fields.
        if center_shift > src_width:
            return None, dx0, dx1, (
                f"narrow source ({src_width:.1f}pt) — center shifted "
                f"{center_shift:.1f}pt; suspect adjacent-field grab"
            )
    if max(abs(dx0), abs(dx1)) > max_shift_x:
        return None, dx0, dx1, f"shift {max(abs(dx0), abs(dx1)):.1f}pt > max"
    # Anchor the widget bottom edge to the underscore row, then set height
    # to the canonical value. This both normalizes height across the form
    # and ensures typed text sits cleanly above the printed line.
    if canonical_height is not None:
        new_y1 = crop.y0 + underscore_row_px * px_to_pt
        new_y0 = new_y1 - canonical_height
    else:
        new_y0, new_y1 = widget.rect.y0, widget.rect.y1
    new_rect = fitz.Rect(new_x0, new_y0, new_x1, new_y1)
    return new_rect, dx0, dx1, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--zoom", type=float, default=6.0)
    ap.add_argument("--margin-x", type=float, default=60.0,
                    help="Horizontal search margin in PDF points (covers "
                         "label-then-underscore patterns)")
    ap.add_argument("--margin-y-above", type=float, default=1.0,
                    help="Vertical search margin above the widget rect "
                         "(small to avoid catching the previous row's "
                         "underscore on tightly-spaced text)")
    ap.add_argument("--margin-y-below", type=float, default=3.0,
                    help="Vertical search margin below the widget rect")
    ap.add_argument("--max-shift-x", type=float, default=200.0,
                    help="Max x-direction snap distance per edge. Default "
                         "200pt is high enough to handle long-description "
                         "lines where the label wraps before the underscore "
                         "starts (e.g. AD-008's '...consent, or adoption "
                         "process. ___').")
    ap.add_argument("--max-shift-y", type=float, default=6.0,
                    help="Max y-direction distance between the found "
                         "underscore and the widget's current bottom edge. "
                         "Larger values risk snapping to an adjacent row. "
                         "6pt is enough to fix upstream-detector misplacement "
                         "without crossing the typical 10-12pt row spacing.")
    ap.add_argument("--min-underscore-pt", type=float, default=20.0,
                    help="Minimum underscore length in PDF points")
    ap.add_argument("--canonical-height", type=float, default=12.0,
                    help="Set every snapped widget's height to this value, "
                         "with bottom edge on the underscore row. Pass 0 "
                         "to preserve original height.")
    ap.add_argument("--narrow-width-pt", type=float, default=40.0,
                    help="Source widgets narrower than this are treated as "
                         "narrow and get the width-matching guard.")
    ap.add_argument("--narrow-width-multiplier", type=float, default=2.0,
                    help="For narrow source widgets, reject snap targets "
                         "wider than source × this multiplier.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr)
        return 2
    doc = fitz.open(args.pdf)
    snapped = skipped = 0
    skip_reasons: dict[str, int] = {}
    shifts: list[tuple[float, float]] = []
    for page in doc:
        for w in list(page.widgets()):
            if w.field_type != 7:  # Text only
                continue
            new_rect, dx0, dx1, reason = snap_widget(
                page, w, zoom=args.zoom,
                margin_x=args.margin_x,
                margin_y_above=args.margin_y_above,
                margin_y_below=args.margin_y_below,
                max_shift_x=args.max_shift_x,
                max_shift_y=args.max_shift_y,
                min_underscore_pt=args.min_underscore_pt,
                canonical_height=(args.canonical_height
                                  if args.canonical_height > 0 else None),
                narrow_width_pt=args.narrow_width_pt,
                narrow_width_multiplier=args.narrow_width_multiplier,
            )
            if new_rect is None:
                skipped += 1
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                if args.verbose:
                    print(f"  skip p{page.number} {w.field_name!r}: {reason}")
                continue
            if args.verbose:
                print(f"  snap p{page.number} {w.field_name!r}: "
                      f"dx0={dx0:+.2f}pt dx1={dx1:+.2f}pt")
            shifts.append((dx0, dx1))
            if not args.dry_run:
                w.rect = new_rect
                w.update()
            snapped += 1
    print(f"snapped={snapped} skipped={skipped}")
    for r, n in skip_reasons.items():
        print(f"  skip:{r} = {n}")
    if shifts:
        avg_dx0 = sum(abs(s[0]) for s in shifts) / len(shifts)
        avg_dx1 = sum(abs(s[1]) for s in shifts) / len(shifts)
        print(f"avg |dx0|={avg_dx0:.2f}pt  |dx1|={avg_dx1:.2f}pt")
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
