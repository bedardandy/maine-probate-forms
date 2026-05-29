"""Patch E — geometric line-anchored snapper.

Replaces VLM-driven alignment correction with deterministic snap-to-line:
  * For each text/signature widget, find the nearest horizontal vector
    line under/near its bottom edge (within tolerance); snap bbox bottom
    to that y, x-extent to the line's endpoints.
  * For each checkbox widget, find the nearest small-square stroke; center
    bbox on it, normalize size to 10x10pt.

Anchor extraction reads vector geometry directly from the PDF via
PyMuPDF — no model, no rendering, no OCR. For authored Maine probate
forms this is reliable; scanned forms would need an OCR fallback.

Used by scripts/recursive_improvement.py as the primary "realign" backend.
Patch D (FFDetr crop) remains as a fallback when no anchor is found.

API:
  snap_widget_rect(pdf_path, page_no, widget_name) -> [x0,y0,x1,y1] | None
  extract_anchors(page) -> {"hlines": [...], "squares": [...]}
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import fitz


# Tolerances (PDF points)
HLINE_MIN_LENGTH = 5.0
HLINE_FLAT_EPS = 0.6           # |y0-y1| < this for a segment to count as horizontal
HLINE_RECT_FLAT_EPS = 1.6      # rectangle thinner than this counts as a line
ANCHOR_VERT_TOLERANCE = 15.0   # underline must be within this many points of widget.y1
ANCHOR_X_OVERLAP_SLOP = 12.0   # underline x-range must overlap widget x-range within this
ANCHOR_VERT_BIAS_BELOW = 4.0   # prefer lines slightly below widget bottom (typical drift)
SNAP_X_CLAMP_SLOP = 8.0        # widget x may extend up to this far past underline endpoints
ANCHOR_WIDTH_MISMATCH_ALPHA = 0.05  # score penalty per pt the anchor is narrower than the widget
ANCHOR_MIN_WIDTH_FRAC = 0.3    # reject snap if anchor < this fraction of widget width
SIBLING_Y_TOLERANCE = 3.0      # how close a sibling widget's y1 must be to the anchor's y to count as 'sharing the line'
SQUARE_SIZE_MIN = 6.0          # checkbox glyphs in these forms are ~8-12pt
SQUARE_SIZE_MAX = 16.0
SQUARE_RECT_TOLERANCE = 14.0   # checkbox glyph must be within this many pts of widget center
TEXT_DEFAULT_HEIGHT = 12.0     # canonical height for all text widgets
SIG_DEFAULT_HEIGHT = 18.0      # canonical height for all signature widgets


@dataclass
class HLine:
    x0: float
    y: float
    x1: float

    @property
    def length(self) -> float:
        return self.x1 - self.x0


@dataclass
class Square:
    cx: float
    cy: float
    size: float


def _extract_text_underscore_lines(page: fitz.Page) -> list[HLine]:
    """Find runs of underscore characters in extracted text and treat each
    contiguous run as a virtual horizontal line at the span's baseline.

    Reads per-character bboxes via rawdict so the run's x-endpoints are the
    actual rendered glyph positions, not a proportional estimate. The
    proportional `span_width / char_count` average was biased on spans
    mixing narrow spaces and wide underscores: a row with 20 leading spaces
    landed its anchor ~38pt to the right of the visible underline because
    each space was charged the full average char width. Rendered PDFs show
    pairs of underline rows aligned exactly, but our anchor estimates drifted
    apart by ~20pt between rows with different leading-whitespace counts.
    """
    out: list[HLine] = []
    try:
        td = page.get_text("rawdict")
    except Exception:
        return out
    for block in td.get("blocks", []):
        if block.get("type") != 0:  # 0 = text
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                chars = span.get("chars") or []
                if not chars:
                    continue
                text = "".join(c.get("c", "") for c in chars)
                if "_" not in text:
                    continue
                bbox = span.get("bbox")
                if not bbox or len(bbox) != 4:
                    continue
                y_baseline = bbox[3]
                # Locate every contiguous '_' run in the span and emit one
                # hline per run. Per-char bboxes give exact endpoints, so a
                # mixed-width span like '              ____...' anchors at
                # the actual first-underscore x rather than at an estimate
                # biased by the run of narrow spaces preceding it.
                run_start = None
                last_us_idx = None
                for i, ch in enumerate(text):
                    if ch == "_":
                        if run_start is None:
                            run_start = i
                        last_us_idx = i
                    elif run_start is not None:
                        rx0 = chars[run_start]["bbox"][0]
                        rx1 = chars[last_us_idx]["bbox"][2]
                        if rx1 - rx0 >= HLINE_MIN_LENGTH:
                            out.append(HLine(x0=rx0, y=y_baseline, x1=rx1))
                        run_start = None
                        last_us_idx = None
                if run_start is not None and last_us_idx is not None:
                    rx0 = chars[run_start]["bbox"][0]
                    rx1 = chars[last_us_idx]["bbox"][2]
                    if rx1 - rx0 >= HLINE_MIN_LENGTH:
                        out.append(HLine(x0=rx0, y=y_baseline, x1=rx1))
    return out


def extract_anchors(page: fitz.Page) -> dict:
    """Walk page vector graphics + text. Return horizontal-line + square anchors.
    Hlines come from: stroked line segments, thin filled rectangles, and runs
    of underscore characters in extracted text."""
    hlines: list[HLine] = []
    squares: list[Square] = []
    for d in page.get_drawings():
        for item in d.get("items", []):
            op = item[0]
            if op == "l":
                p0, p1 = item[1], item[2]
                if abs(p0.y - p1.y) <= HLINE_FLAT_EPS:
                    x0, x1 = (p0.x, p1.x) if p0.x < p1.x else (p1.x, p0.x)
                    if x1 - x0 >= HLINE_MIN_LENGTH:
                        hlines.append(HLine(x0=x0, y=(p0.y + p1.y) / 2, x1=x1))
            elif op == "re":
                rect: fitz.Rect = item[1]
                # Thin rect → likely a drawn underline.
                if rect.height <= HLINE_RECT_FLAT_EPS and rect.width >= HLINE_MIN_LENGTH:
                    hlines.append(HLine(x0=rect.x0, y=(rect.y0 + rect.y1) / 2, x1=rect.x1))
                # Square-ish rect of checkbox-glyph size → checkbox anchor.
                elif (SQUARE_SIZE_MIN <= rect.width <= SQUARE_SIZE_MAX
                      and SQUARE_SIZE_MIN <= rect.height <= SQUARE_SIZE_MAX
                      and abs(rect.width - rect.height) <= 3.0):
                    squares.append(Square(
                        cx=(rect.x0 + rect.x1) / 2,
                        cy=(rect.y0 + rect.y1) / 2,
                        size=max(rect.width, rect.height),
                    ))
    # Text-based underscore underlines (forms that don't draw vector lines).
    hlines.extend(_extract_text_underscore_lines(page))
    return {"hlines": hlines, "squares": squares}


def find_underline(widget_rect: fitz.Rect, hlines: list[HLine]) -> HLine | None:
    """Pick the underline whose y is nearest widget.y1, with strong horizontal overlap.
    Bias slightly toward lines below the widget bottom (typical AcroForm drift).
    Penalize anchors much narrower than the widget — without this, a 9pt
    checkbox-glyph stroke just below a 480pt text widget wins on y-proximity
    and produces a catastrophic snap.
    """
    widget_width = widget_rect.width
    best: tuple[float, HLine] | None = None
    for hl in hlines:
        # Horizontal compatibility — line and widget must roughly overlap on x.
        if hl.x1 < widget_rect.x0 - ANCHOR_X_OVERLAP_SLOP:
            continue
        if hl.x0 > widget_rect.x1 + ANCHOR_X_OVERLAP_SLOP:
            continue
        dy = hl.y - widget_rect.y1
        if abs(dy) > ANCHOR_VERT_TOLERANCE:
            continue
        # Score: y-proximity, plus a small bias against above-bottom lines,
        # plus a penalty when the anchor is much narrower than the widget.
        bias = 0.0 if 0 <= dy <= ANCHOR_VERT_BIAS_BELOW else 0.5
        width_gap = max(0.0, widget_width - hl.length)
        score = abs(dy) + bias + ANCHOR_WIDTH_MISMATCH_ALPHA * width_gap
        if best is None or score < best[0]:
            best = (score, hl)
    return best[1] if best else None


def find_checkbox_anchor(widget_rect: fitz.Rect, squares: list[Square]) -> Square | None:
    cx = (widget_rect.x0 + widget_rect.x1) / 2
    cy = (widget_rect.y0 + widget_rect.y1) / 2
    best: tuple[float, Square] | None = None
    for sq in squares:
        d = max(abs(sq.cx - cx), abs(sq.cy - cy))
        if d > SQUARE_RECT_TOLERANCE:
            continue
        if best is None or d < best[0]:
            best = (d, sq)
    return best[1] if best else None


def snap_text_rect(widget_rect: fitz.Rect, hl: HLine,
                   default_height: float,
                   sole_owner: bool = False) -> fitz.Rect | None:
    """Bottom of widget = underline y; height set to canonical default.

    Height is normalized to `default_height` (12pt for text, 18pt for sig)
    rather than preserved from the upstream detector — column-stack views
    look tidy and descenders consistently rest on the underline.

    x-handling depends on `sole_owner`:
      * False (default, shared row): clamp x to widget x-range +/- slop. Stops
        a row-spanning underline from absorbing a small widget into
        full-row width when other widgets share that line.
      * True: use the underline's full endpoints. Use this when no other
        widget on the page has its bottom near this anchor's y AND x-range
        overlapping the anchor — in that case the anchor is the genuine field
        underline and the widget should fill it.

    Returns None if the chosen anchor is implausibly narrow relative to the
    widget — in that case Patch E declines to snap rather than collapsing the
    widget to a checkbox-glyph stroke.
    """
    if hl.length < ANCHOR_MIN_WIDTH_FRAC * widget_rect.width:
        return None
    if sole_owner:
        return fitz.Rect(hl.x0, hl.y - default_height, hl.x1, hl.y)
    new_x0 = max(hl.x0, widget_rect.x0 - SNAP_X_CLAMP_SLOP)
    new_x1 = min(hl.x1, widget_rect.x1 + SNAP_X_CLAMP_SLOP)
    if new_x1 - new_x0 < HLINE_MIN_LENGTH:
        new_x0, new_x1 = hl.x0, hl.x1
    return fitz.Rect(new_x0, hl.y - default_height, new_x1, hl.y)


def snap_checkbox_rect(sq: Square) -> fitz.Rect:
    half = sq.size / 2
    return fitz.Rect(sq.cx - half, sq.cy - half, sq.cx + half, sq.cy + half)


def snap_widget_rect(pdf_path: pathlib.Path, page_no: int,
                     widget_name: str,
                     widget_rect: list[float] | tuple | fitz.Rect | None = None,
                     widget_type: int | None = None) -> list[float] | None:
    """Public entry — given a widget identity, return a snapped rect or None.

    Returns None if the widget isn't found, no anchor is in tolerance, or the
    snap would be a no-op (delta < 1pt).

    When a form has duplicate widget names (PB-007 has eight `minor_name_row1`
    instances), name-only lookup picks the first match and every duplicate
    snaps to the same rect, stacking them. Pass `widget_rect` and
    `widget_type` from the caller's iteration to disambiguate.
    """
    d = fitz.open(pdf_path)
    try:
        if page_no >= d.page_count:
            return None
        page = d[page_no]
        if widget_rect is not None and widget_type is not None:
            rect = fitz.Rect(*widget_rect) if not isinstance(widget_rect, fitz.Rect) else widget_rect
            ftype = widget_type
        else:
            target = next((w for w in (page.widgets() or [])
                           if w.field_name == widget_name), None)
            if target is None:
                return None
            ftype = target.field_type
            rect = target.rect
        anchors = extract_anchors(page)
        # Snapshot all widgets' (rect, type) for sole-owner detection.
        # Skip the target itself by approximate rect match (PB-007 has
        # duplicate field names, so name-equality won't disambiguate).
        sibling_text_rects: list[fitz.Rect] = []
        for sw in (page.widgets() or []):
            if sw.field_type not in (6, 7):
                continue
            sr = sw.rect
            if (abs(sr.x0 - rect.x0) < 0.5 and abs(sr.y0 - rect.y0) < 0.5
                    and abs(sr.x1 - rect.x1) < 0.5 and abs(sr.y1 - rect.y1) < 0.5):
                continue  # the target itself
            sibling_text_rects.append(sr)

        def _is_sole_owner(hl: HLine) -> bool:
            # 'Shares the line' = sibling sits on the same physical anchor.
            # Tight y-tolerance: a widget one row up (y diff ~12pt) is on a
            # different line, not sharing this one.
            for sr in sibling_text_rects:
                if abs(sr.y1 - hl.y) > SIBLING_Y_TOLERANCE:
                    continue
                if sr.x1 < hl.x0 or sr.x0 > hl.x1:
                    continue
                return False
            return True

        new_rect: fitz.Rect | None = None
        if ftype == 7:  # PDF_WIDGET_TYPE_TEXT
            hl = find_underline(rect, anchors["hlines"])
            if hl is not None:
                new_rect = snap_text_rect(rect, hl, TEXT_DEFAULT_HEIGHT,
                                          sole_owner=_is_sole_owner(hl))
        elif ftype == 6:  # PDF_WIDGET_TYPE_SIGNATURE
            hl = find_underline(rect, anchors["hlines"])
            if hl is not None:
                new_rect = snap_text_rect(rect, hl, SIG_DEFAULT_HEIGHT,
                                          sole_owner=_is_sole_owner(hl))
        elif ftype in (2, 5):  # CHECKBOX or RADIOBUTTON
            sq = find_checkbox_anchor(rect, anchors["squares"])
            if sq is not None:
                new_rect = snap_checkbox_rect(sq)

        if new_rect is None:
            return None
        # No-op gate — only return if snap moves bbox by >=1pt on any side.
        if (abs(new_rect.x0 - rect.x0) < 1.0 and abs(new_rect.y0 - rect.y0) < 1.0
                and abs(new_rect.x1 - rect.x1) < 1.0
                and abs(new_rect.y1 - rect.y1) < 1.0):
            return None
        return [new_rect.x0, new_rect.y0, new_rect.x1, new_rect.y1]
    finally:
        d.close()


# CLI for quick inspection.
if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=0)
    args = ap.parse_args()
    doc = fitz.open(args.pdf)
    page = doc[args.page]
    a = extract_anchors(page)
    out = {"page": args.page,
           "n_hlines": len(a["hlines"]),
           "n_squares": len(a["squares"]),
           "sample_hlines": [(round(h.x0, 1), round(h.y, 1), round(h.x1, 1))
                             for h in a["hlines"][:8]],
           "sample_squares": [(round(s.cx, 1), round(s.cy, 1), round(s.size, 1))
                              for s in a["squares"][:8]]}
    print(json.dumps(out, indent=2))
