"""Fuse CommonForms positions with Layer1 v2 naming + recall.

Pipeline (per form):
  1. Load CF widgets from output_commonforms/maxrecall_hires/<cat>/<stem>_commonforms.pdf
  2. Load source-PDF horizontal underlines + checkbox rects from intermediate_layer1/analysis
  3. Cluster canonical sizes (underline H, checkbox W×H)
  4. Snap CF text widgets to underline (re-anchor x0/x1 to underline endpoints; canonical H)
  5. Snap CF checkbox widgets to canonical checkbox size; centre at CF rect centre
  6. Add ours-only widgets (from output_layer1) where no CF rect is within IoU 0.3
  7. NMS overlap pass: drop the smaller-IoU rect when two rects overlap >0.5
  8. Naming pass: _find_nearby_label + _find_section_header from modules.field_detector
  9. Write fused widgets via PyMuPDF into output_fused/<cat>/<stem>_fused.pdf
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

import fitz
import numpy as np
from scipy.ndimage import label as cc_label

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.field_detector import _find_nearby_label, _find_section_header  # noqa: E402
from modules.schema import Rect, TextLine, TextSpan  # noqa: E402

ORIG_DIR = ROOT / "forms"
OURS_DIR = ROOT / "output_layer1"
CF_DIR = ROOT / "output_commonforms" / "imgsize_3200"
ANALYSIS_DIR = ROOT / "intermediate_layer1" / "analysis"
OUT_DIR = ROOT / "output_fused"

PANEL = [
    ("estates", "DE-101(I) Application for Informal - Intestate (Rev. 09-12-19).pdf"),
    ("estates", "DE-104 PR Acceptance (Rev. 07-01-19).pdf"),
    ("gc_adults", "PP-205 Joined Petition for Guardian and Conservator (Rev. 07-01-19).pdf"),
    ("name_change", "NC-001 Petition for Name Change of Minor.pdf"),
    ("estates", "DE-405 Inventory (Rev. 5-6-21).pdf"),
]


@dataclass
class FusedWidget:
    page: int
    rect: tuple  # (x0, y0, x1, y1)
    type: str    # "text" | "check" | "sig"
    name: str = ""
    source: str = ""  # "cf" | "ours"
    label: str = ""
    section: str = ""
    underline_anchored: bool = False  # set when CF text rect was snapped to an underline


def iou(a: tuple, b: tuple) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


# ── Step 1+2: load inputs ─────────────────────────────────────────────────


def load_pdf_widgets(pdf: pathlib.Path) -> list[FusedWidget]:
    out = []
    if not pdf.exists():
        return out
    d = fitz.open(pdf)
    for pno, page in enumerate(d):
        for w in page.widgets() or []:
            r = w.rect
            # Skip malformed rects (inf-sentinel, negative, or out-of-page).
            if not (-50 < r.x0 < 1500 and -50 < r.y0 < 1500
                    and -50 < r.x1 < 1500 and -50 < r.y1 < 1500):
                continue
            if r.x1 - r.x0 < 1 or r.y1 - r.y0 < 1:
                continue
            t = "text"
            if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                t = "check"
            elif w.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE:
                t = "sig"
            out.append(
                FusedWidget(
                    page=pno,
                    rect=(r.x0, r.y0, r.x1, r.y1),
                    type=t,
                    name=w.field_name or "",
                    source="cf",
                )
            )
    d.close()
    return out


def load_analysis(filename: str) -> dict:
    """Resolve filename → form_id by extracting leading code (e.g. 'DE-101(I)')."""
    m = re.match(r"^([A-Z]+-?\d+(?:\([A-Z]\))?)", filename)
    if not m:
        raise FileNotFoundError(f"Cannot derive form_id from {filename}")
    form_id = m.group(1)
    p = ANALYSIS_DIR / f"{form_id}.json"
    return json.loads(p.read_text())


# ── Step 3: cluster canonical sizes ───────────────────────────────────────


def extract_underlines_and_boxes(pages: list[dict]) -> tuple[dict, dict]:
    """Return (underlines_by_page, checkboxes_by_page).

    Underlines: horizontal lines where dx >= 30pt (not page rules).
    Checkboxes: groups of 4 lines forming a small square 8-12pt.
    """
    underlines: dict[int, list[tuple]] = {}
    checkboxes: dict[int, list[tuple]] = {}
    for pg in pages:
        pno = pg["page_number"]
        ulines = []
        all_segs = []
        for dr in pg.get("drawings", []):
            if dr["kind"] != "line":
                continue
            r = dr["rect"]
            x0, y0, x1, y1 = r["x0"], r["y0"], r["x1"], r["y1"]
            dx, dy = x1 - x0, y1 - y0
            # Underline: roughly horizontal, length >= 30
            if abs(dy) <= 0.5 and dx >= 30 and dx < pg["width"] - 50:
                ulines.append((x0, y0, x1, y1))
            all_segs.append((x0, y0, x1, y1))
        underlines[pno] = ulines

        # Checkbox: cluster by (cx, cy) of segment groups whose bbox is 6-14pt square
        # Group segments by close-to-equal endpoint clusters
        from collections import defaultdict
        # Bucket lines into squares: a square = 4 segments forming closed rect ~8-12pt
        # Heuristic: scan unique (x0,y0,x1,y1) tuples, find cluster centers where 4
        # short segments share a small bounding box.
        # Simpler: build small windows; for each segment of length 6-14 along axis,
        # see if 3 perpendicular partners exist within 2pt.
        rects_seen = set()
        boxes = []
        # Index segments by approx midpoint
        for i, s in enumerate(all_segs):
            x0, y0, x1, y1 = s
            ldx = abs(x1 - x0)
            ldy = abs(y1 - y0)
            # candidate horizontal segment of a checkbox
            if not (ldy <= 0.5 and 6 <= ldx <= 14):
                continue
            cx_mid = (x0 + x1) / 2
            cy_mid = y0
            # find a parallel partner above/below within 2pt of same length and cx
            for j, t in enumerate(all_segs):
                if i == j:
                    continue
                tx0, ty0, tx1, ty1 = t
                tdx = abs(tx1 - tx0)
                tdy = abs(ty1 - ty0)
                if tdy > 0.5 or abs(tdx - ldx) > 2:
                    continue
                if abs((tx0 + tx1) / 2 - cx_mid) > 2:
                    continue
                vsep = abs(ty0 - cy_mid)
                if not (6 <= vsep <= 14):
                    continue
                bx0 = min(x0, x1, tx0, tx1)
                bx1 = max(x0, x1, tx0, tx1)
                by0 = min(y0, ty0)
                by1 = max(y0, ty0)
                key = (round(bx0, 1), round(by0, 1), round(bx1, 1), round(by1, 1))
                if key in rects_seen:
                    continue
                rects_seen.add(key)
                boxes.append((bx0, by0, bx1, by1))
                break
        checkboxes[pno] = boxes
    return underlines, checkboxes


def canonical_sizes(underlines, checkboxes):
    """Modal underline height ~ small (10-14pt assumed widget H over a line).
    Modal checkbox W and H from cluster.
    """
    box_widths = []
    box_heights = []
    for boxes in checkboxes.values():
        for x0, y0, x1, y1 in boxes:
            box_widths.append(round(x1 - x0, 1))
            box_heights.append(round(y1 - y0, 1))
    cw = Counter(box_widths).most_common(1)
    ch = Counter(box_heights).most_common(1)
    canonical_check_w = cw[0][0] if cw else 9.0
    canonical_check_h = ch[0][0] if ch else 9.0
    # Text widget height is a fixed value; underlines are essentially zero-height
    # in PyMuPDF "line" drawings. Use a sensible default.
    canonical_text_h = 12.0
    return canonical_text_h, canonical_check_w, canonical_check_h


# ── Step 4: snap CF text to nearest underline ─────────────────────────────


def snap_text_to_underline(w: FusedWidget, underlines: list[tuple], text_h: float) -> FusedWidget:
    """Re-anchor text widget to the nearest underline below or behind it."""
    cx = (w.rect[0] + w.rect[2]) / 2
    cy = (w.rect[1] + w.rect[3]) / 2
    best = None
    best_d = 1e9
    for ux0, uy0, ux1, uy1 in underlines:
        # Underline must be at roughly the bottom of the text widget (within 4pt)
        if abs(uy0 - w.rect[3]) > 6 and abs(uy0 - cy) > 6:
            continue
        # Horizontal containment: widget x-range must overlap the underline
        if ux1 < w.rect[0] - 4 or ux0 > w.rect[2] + 4:
            continue
        d = abs(uy0 - w.rect[3]) + abs((ux0 + ux1) / 2 - cx) * 0.1
        if d < best_d:
            best_d = d
            best = (ux0, uy0, ux1, uy1)
    if not best:
        return w
    ux0, uy0, ux1, uy1 = best
    new_rect = (ux0, uy0 - text_h, ux1, uy0 + 1.0)
    w.rect = new_rect
    w.underline_anchored = True
    return w


# ── Step 5: snap CF checkbox to canonical size ────────────────────────────


def snap_check_to_canonical(w: FusedWidget, cw: float, ch: float) -> FusedWidget:
    cx = (w.rect[0] + w.rect[2]) / 2
    cy = (w.rect[1] + w.rect[3]) / 2
    w.rect = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    return w


# ── Raster overlay snap (Kofax-style) ─────────────────────────────────────


RASTER_DPI = 600  # 8.33 px / pt — sub-0.12pt sub-pixel accuracy
RASTER_INK_THRESHOLD = 180  # pixel < this is "ink"
RASTER_ROI_MARGIN_PT = 4  # render ROI of (W + 2*margin) around CF seed


def _snap_one_checkbox_to_ink(page: fitz.Page, w: FusedWidget) -> bool:
    """Snap a single checkbox to the ink centroid found in a small high-DPI ROI.

    Mutates w.rect (translation only — preserves canonical size).
    Returns True if a valid ink contour was found and the position was updated.
    """
    cx = (w.rect[0] + w.rect[2]) / 2
    cy = (w.rect[1] + w.rect[3]) / 2
    canonical_w = w.rect[2] - w.rect[0]
    canonical_h = w.rect[3] - w.rect[1]
    margin = RASTER_ROI_MARGIN_PT
    roi_pdf = fitz.Rect(cx - canonical_w / 2 - margin,
                        cy - canonical_h / 2 - margin,
                        cx + canonical_w / 2 + margin,
                        cy + canonical_h / 2 + margin)
    pix = page.get_pixmap(clip=roi_pdf, dpi=RASTER_DPI, colorspace=fitz.csGRAY)
    if pix.width < 4 or pix.height < 4:
        return False
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    ink_mask = arr < RASTER_INK_THRESHOLD
    if not ink_mask.any():
        return False
    # Connected components — pick the blob nearest the ROI center.
    labels, n_blobs = cc_label(ink_mask)
    if n_blobs == 0:
        return False
    h_px, w_px = ink_mask.shape
    cy_px, cx_px = h_px / 2, w_px / 2
    best_lbl = -1
    best_dist = 1e18
    for lbl in range(1, n_blobs + 1):
        ys, xs = np.where(labels == lbl)
        if ys.size < 4:
            continue  # noise speck
        bcy, bcx = ys.mean(), xs.mean()
        d = (bcy - cy_px) ** 2 + (bcx - cx_px) ** 2
        if d < best_dist:
            best_dist = d
            best_lbl = lbl
    if best_lbl < 0:
        return False
    blob = labels == best_lbl
    rows_with_ink = np.where(blob.any(axis=1))[0]
    cols_with_ink = np.where(blob.any(axis=0))[0]
    top, bot = rows_with_ink[0], rows_with_ink[-1]
    left, rgt = cols_with_ink[0], cols_with_ink[-1]
    bbox_h_px = bot - top + 1
    bbox_w_px = rgt - left + 1
    # Convert pixel size back to PDF points to sanity-check
    pt_per_px = 72 / RASTER_DPI
    bbox_h_pt = bbox_h_px * pt_per_px
    bbox_w_pt = bbox_w_px * pt_per_px
    # Reject if the bbox is wildly off canonical (e.g. ROI caught neighbor text)
    if not (0.6 * canonical_w <= bbox_w_pt <= 1.6 * canonical_w):
        return False
    if not (0.6 * canonical_h <= bbox_h_pt <= 1.6 * canonical_h):
        return False
    # Centroid of the ink bbox in PDF coordinates
    bbox_cx_px = (left + rgt) / 2
    bbox_cy_px = (top + bot) / 2
    bbox_cx_pdf = roi_pdf.x0 + bbox_cx_px * pt_per_px
    bbox_cy_pdf = roi_pdf.y0 + bbox_cy_px * pt_per_px
    # Reposition canonical-sized box around the ink centroid
    w.rect = (
        bbox_cx_pdf - canonical_w / 2,
        bbox_cy_pdf - canonical_h / 2,
        bbox_cx_pdf + canonical_w / 2,
        bbox_cy_pdf + canonical_h / 2,
    )
    return True


def raster_snap_checkboxes(widgets: list[FusedWidget], src_pdf: pathlib.Path) -> dict:
    """Render-and-snap pass for all checkbox widgets. Returns per-form stats."""
    by_page: dict[int, list[FusedWidget]] = {}
    for w in widgets:
        if w.type == "check":
            by_page.setdefault(w.page, []).append(w)
    snapped, fallback = 0, 0
    if not by_page:
        return {"snapped": 0, "fallback": 0}
    d = fitz.open(src_pdf)
    for pno, ws in by_page.items():
        page = d[pno]
        for w in ws:
            if _snap_one_checkbox_to_ink(page, w):
                snapped += 1
            else:
                fallback += 1
    d.close()
    return {"snapped": snapped, "fallback": fallback}


# ── Per-form checkbox canonicalization ────────────────────────────────────


def canonicalize_checkboxes(widgets: list[FusedWidget],
                            square_tolerance: float = 1.5) -> list[FusedWidget]:
    """Force every checkbox in a form to the modal size, anchored on its center.

    Wingdings checkbox glyphs in a single PDF are all the same point size, so
    the visible squares should be uniform. CF's YOLO detection rounds to whole
    image pixels, introducing ±1pt jitter (e.g. 7.25 → 7 vs 8). This pass picks
    the modal (W, H), forces a square if they're within tolerance, and recenters
    every detection to that exact size.
    """
    checks = [w for w in widgets if w.type == "check"]
    if len(checks) < 2:
        return widgets
    widths = Counter(round(w.rect[2] - w.rect[0]) for w in checks)
    heights = Counter(round(w.rect[3] - w.rect[1]) for w in checks)
    mw, _ = widths.most_common(1)[0]
    mh, _ = heights.most_common(1)[0]
    # If nearly square, force a square at max(mw, mh) — preserves visible glyph.
    if abs(mw - mh) <= square_tolerance:
        canonical = max(mw, mh)
        cw, ch = canonical, canonical
    else:
        cw, ch = mw, mh
    for w in checks:
        cx = (w.rect[0] + w.rect[2]) / 2
        cy = (w.rect[1] + w.rect[3]) / 2
        w.rect = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    return widgets


# ── Rule C: text-line column snap for unanchored text widgets ────────────


def _text_line_peaks(pg: dict, bin_size: float = 1.0, min_count: int = 3) -> list[int]:
    """Return dominant text-line left margins (x0) on this page.

    These are the paragraph indent positions readers see — body margin
    (e.g. 72pt = 1in) and tab stops. Snapping to these aligns widgets with
    the visual paragraph indent, not with page geometry or widget clusters.
    """
    bins: dict[int, int] = {}
    for tb in pg.get("text_blocks", []):
        for ln in tb.get("lines", []):
            x0 = int(round(ln["bbox"]["x0"] / bin_size) * bin_size)
            bins[x0] = bins.get(x0, 0) + 1
    return [x for x, n in bins.items() if n >= min_count]


def filter_widgets_in_header_cells(widgets: list[FusedWidget], analysis: dict) -> list[FusedWidget]:
    """Drop any text widget (CF or ours) whose center falls inside a header cell.

    A header cell is one whose interior contains printed label text — placing a
    widget there would overlay e.g. "Name" / "Address" / "Relationship".
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    page_headers: dict[int, list[tuple]] = {}
    for pno, pg in pages.items():
        cells = _detect_table_cells(pg)
        if not cells:
            continue
        page_headers[pno] = _detect_header_cells(pg, cells)
    if not page_headers:
        return widgets
    keep = []
    for w in widgets:
        if w.type == "text":
            headers = page_headers.get(w.page, [])
            cx = (w.rect[0] + w.rect[2]) / 2
            cy = (w.rect[1] + w.rect[3]) / 2
            if any(h[0] <= cx <= h[2] and h[1] <= cy <= h[3] for h in headers):
                continue
        keep.append(w)
    return keep


def filter_ours_in_table_areas(widgets: list[FusedWidget], analysis: dict) -> list[FusedWidget]:
    """Drop ours-injected widgets in header cells or outside any cell on cell-heavy pages.

    Two kinds of removal:
      • widget center is outside ALL cells (e.g. v2's mis-placed pre-header widget)
      • widget center is inside a HEADER cell (a cell whose interior contains
        printed label text — placing a widget there would overlay "Name" /
        "Address" / etc.)
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    page_data: dict[int, tuple[list, list]] = {}
    for pno, pg in pages.items():
        cells = _detect_table_cells(pg)
        if not cells:
            continue
        headers = _detect_header_cells(pg, cells)
        page_data[pno] = (cells, headers)
    if not page_data:
        return widgets
    keep = []
    for w in widgets:
        if w.source != "ours" or w.type != "text":
            keep.append(w)
            continue
        if w.page not in page_data:
            keep.append(w)
            continue
        cells, headers = page_data[w.page]
        cx = (w.rect[0] + w.rect[2]) / 2
        cy = (w.rect[1] + w.rect[3]) / 2
        in_any_cell = any(c[0] <= cx <= c[2] and c[1] <= cy <= c[3] for c in cells)
        if not in_any_cell:
            continue
        in_header = any(h[0] <= cx <= h[2] and h[1] <= cy <= h[3] for h in headers)
        if in_header:
            continue
        keep.append(w)
    return keep


def snap_widget_y_to_cell_rows(widgets: list[FusedWidget], analysis: dict) -> list[FusedWidget]:
    """Snap multi-row text widgets in tables so their y-edges sit on horizontal borders.

    Multi-row CF widgets (e.g. a 34pt-tall address span across 3 cells) bypass
    table_cell_snap's max_h_ratio guard. They keep their CF y-coords, which can
    be 1-3pt off the true cell row borders. Here we find the horizontal-line
    cluster and snap each y-edge to its nearest line.
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    for w in widgets:
        if w.type != "text":
            continue
        pg = pages.get(w.page)
        if not pg:
            continue
        h_lines = _detect_horizontal_separators(pg)
        if len(h_lines) < 2:
            continue
        y0, y1 = w.rect[1], w.rect[3]
        b0 = min(h_lines, key=lambda hy: abs(hy - y0))
        b1 = min(h_lines, key=lambda hy: abs(hy - y1))
        # Only snap when both edges are within ~3pt of borders — otherwise the
        # widget is in a non-table area and we'd damage its layout.
        if abs(b0 - y0) > 3 or abs(b1 - y1) > 3:
            continue
        if b1 - b0 < 8:
            continue
        w.rect = (w.rect[0], b0, w.rect[2], b1)
    return widgets


def right_margin_snap(widgets: list[FusedWidget], analysis: dict,
                      tolerance: float = 8.0) -> list[FusedWidget]:
    """Snap free-form text widgets ending near the page right margin to the margin exactly.

    Common pattern on probate forms: page width 612, right margin 540 (1in).
    Widgets that end at 524/533/540 due to varied underline endpoints get
    consolidated to a uniform x1=540 (or the page's actual modal right edge).
    Skips already-anchored widgets (cell + vertical-separator snapped).
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    for pno, pg in pages.items():
        # Detect this page's modal right margin from text-line endings
        line_x1s: list[float] = []
        for tb in pg.get("text_blocks", []):
            for ln in tb.get("lines", []):
                line_x1s.append(ln["bbox"]["x1"])
        if not line_x1s:
            continue
        # Mode = the most common right edge among long text lines (full-width body)
        from collections import Counter
        right_edge_bins = Counter(round(x) for x in line_x1s if x > 400)
        if not right_edge_bins:
            continue
        # Pick the modal right edge as page right margin
        margin_x, _ = right_edge_bins.most_common(1)[0]
        for w in widgets:
            if w.page != pno or w.type != "text" or w.underline_anchored:
                continue
            x1 = w.rect[2]
            if abs(x1 - margin_x) <= tolerance:
                w.rect = (w.rect[0], w.rect[1], float(margin_x), w.rect[3])
    return widgets


def vertical_separator_snap(widgets: list[FusedWidget],
                             analysis: dict,
                             tolerance: float = 8.0) -> list[FusedWidget]:
    """For non-cell text widgets, snap x0 and x1 to the nearest vertical separator
    in the source PDF (column boundaries, page margins). Skips cell widgets so
    table-cell geometry remains pixel-tight. Marks successfully-snapped widgets
    as anchored so the downstream column/cluster passes leave them alone.
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    for w in widgets:
        if w.type != "text" or w.underline_anchored:
            continue
        pg = pages.get(w.page)
        if not pg:
            continue
        bounds = _detect_vertical_separators(pg)
        if not bounds:
            continue
        x0, x1 = w.rect[0], w.rect[2]
        b0 = min(bounds, key=lambda b: abs(b - x0))
        b1 = min(bounds, key=lambda b: abs(b - x1))
        snap_x0 = abs(b0 - x0) <= tolerance
        snap_x1 = abs(b1 - x1) <= tolerance
        if not (snap_x0 or snap_x1):
            continue
        new_x0 = b0 if snap_x0 else x0
        new_x1 = b1 if snap_x1 else x1
        if new_x1 - new_x0 < 8:
            continue
        w.rect = (new_x0, w.rect[1], new_x1, w.rect[3])
        # Both edges anchored — don't let later passes move this widget around.
        if snap_x0 and snap_x1:
            w.underline_anchored = True
    return widgets


def widget_x1_cluster_snap(widgets: list[FusedWidget],
                            gap: float = 20.0,
                            min_cluster_size: int = 2,
                            min_widget_w: float = 100.0) -> list[FusedWidget]:
    """Cluster wide-ish widget right edges per page; right-align each cluster to its max x1.

    Catches free-form fields that should share a right margin but were left at
    underline-detected x1 values (e.g. 524 / 533 / 540 → all to 540). Restricted
    to wide widgets (default ≥100pt) to avoid pulling short-label fields like
    'docket no:' to the same x1 as wide multi-line answer blocks.
    """
    by_page: dict[int, list[FusedWidget]] = {}
    for w in widgets:
        if w.type != "text" or w.underline_anchored:
            continue
        if (w.rect[2] - w.rect[0]) < min_widget_w:
            continue
        by_page.setdefault(w.page, []).append(w)
    for p, ws in by_page.items():
        sorted_ws = sorted(ws, key=lambda w: w.rect[2])
        clusters: list[list[FusedWidget]] = [[sorted_ws[0]]]
        for w in sorted_ws[1:]:
            if w.rect[2] - clusters[-1][-1].rect[2] <= gap:
                clusters[-1].append(w)
            else:
                clusters.append([w])
        for c in clusters:
            if len(c) < min_cluster_size:
                continue
            target = max(w.rect[2] for w in c)
            for w in c:
                if w.rect[2] != target:
                    w.rect = (w.rect[0], w.rect[1], target, w.rect[3])
    return widgets


def page_margin_clamp(widgets: list[FusedWidget],
                       top_margin: float = 18.0,
                       bottom_margin: float = 18.0,
                       side_margin: float = 12.0,
                       page_height: float = 792.0,
                       page_width: float = 612.0,
                       drop_threshold: float = 6.0) -> list[FusedWidget]:
    """Clamp widget rects to inside the page margins; drop only widgets whose
    CENTER is in the margin (clearly outside the content area).

    Permissive on edges so multi-line widgets that legitimately extend close to
    the page bottom (e.g. answer fields filling out the page) are clamped, not
    dropped. Only widgets centered in the margin are CF false positives.
    """
    keep = []
    for w in widgets:
        x0, y0, x1, y1 = w.rect
        cy = (y0 + y1) / 2
        cx = (x0 + x1) / 2
        # Drop only if widget center is in the margin band
        if cy < top_margin or cy > page_height - bottom_margin:
            continue
        if cx < side_margin or cx > page_width - side_margin:
            continue
        # Clamp gentle overshoots
        y0 = max(y0, top_margin)
        y1 = min(y1, page_height - bottom_margin)
        x0 = max(x0, side_margin)
        x1 = min(x1, page_width - side_margin)
        if y1 - y0 < drop_threshold or x1 - x0 < drop_threshold:
            continue
        w.rect = (x0, y0, x1, y1)
        keep.append(w)
    return keep


def filter_orphan_widgets(widgets: list[FusedWidget], analysis: dict,
                          edge_radius: float = 200.0,
                          min_label_chars: int = 3) -> list[FusedWidget]:
    """Drop widgets that are TRULY isolated — no text within edge_radius pt.

    Uses edge-to-edge distance (NOT center-to-center) so tall multi-line widgets
    correctly find their question label even when it sits well above the widget
    center. Widget at y=190-412 finds text at y=71-86 with edge distance =
    190-86 = 104pt — well under the threshold.
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    keep = []
    for w in widgets:
        if w.type != "text" or w.underline_anchored:
            keep.append(w)
            continue
        pg = pages.get(w.page)
        if not pg:
            keep.append(w)
            continue
        wx0, wy0, wx1, wy1 = w.rect
        has_text = False
        for tb in pg.get("text_blocks", []):
            for ln in tb.get("lines", []):
                bb = ln["bbox"]
                text = " ".join(s.get("text", "") for s in ln.get("spans", []))
                if len(text.strip()) < min_label_chars:
                    continue
                # Edge-to-edge vertical distance (0 if overlapping)
                v_dist = max(0, max(bb["y0"] - wy1, wy0 - bb["y1"]))
                if v_dist > edge_radius:
                    continue
                # Horizontal proximity: overlap or close
                h_overlap = min(wx1, bb["x1"]) - max(wx0, bb["x0"])
                h_dist = -h_overlap if h_overlap > 0 else min(abs(bb["x0"] - wx1), abs(wx0 - bb["x1"]))
                if h_dist <= 300:
                    has_text = True
                    break
            if has_text:
                break
        if has_text:
            keep.append(w)
    return keep


_FOOTER_PATTERN = re.compile(
    r"(M\.R\.S\.|U\.S\.C\.|Rev\.\s*\d|Form\s+\d|§\s*\d|page\s+\d+\s+of\s+\d+)",
    re.IGNORECASE,
)


def filter_footer_line_widgets(widgets: list[FusedWidget], analysis: dict,
                               proximity: float = 30.0) -> list[FusedWidget]:
    """Drop widgets sitting near a statutory-citation footer OR page header.

    Catches three patterns:
      • widget ABOVE a horizontal line with citation text below the line (footer separator)
      • widget OVERLAPS a citation text block (page header / footer text)
      • widget BELOW a horizontal line with citation text above the line (header separator)
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    keep = []
    for w in widgets:
        if w.type != "text":
            keep.append(w)
            continue
        pg = pages.get(w.page)
        if not pg:
            keep.append(w)
            continue
        wx0, wy0, wx1, wy1 = w.rect
        is_chrome = False
        # Check 1: widget OVERLAPS citation text (header/footer text inside widget bbox)
        for tb in pg.get("text_blocks", []):
            for ln in tb.get("lines", []):
                bb = ln["bbox"]
                if (wx0 <= bb["x0"] <= wx1 or wx0 <= bb["x1"] <= wx1) and \
                   (wy0 <= bb["y0"] <= wy1):
                    text = " ".join(s.get("text", "") for s in ln.get("spans", []))
                    if _FOOTER_PATTERN.search(text):
                        is_chrome = True
                        break
            if is_chrome:
                break
        if is_chrome:
            continue
        # Check 2: horizontal line within proximity below widget + citation below the line
        for dr in pg.get("drawings", []):
            if dr["kind"] not in ("rect", "line"):
                continue
            r = dr["rect"]
            ww = r["x1"] - r["x0"]
            hh = r["y1"] - r["y0"]
            if hh > 1.5 or ww < 100:
                continue
            line_y = r["y0"]
            # Require x-overlap so a line under one widget doesn't filter a
            # widget on the other side of the page.
            if min(r["x1"], wx1) - max(r["x0"], wx0) <= 0:
                continue
            # Below widget within proximity?
            # Require strict gap below the widget so the widget's own anchor
            # underline (often drawn at y ≈ wy1) isn't mistaken for a footer
            # separator line.
            if wy1 + 2 <= line_y <= wy1 + proximity:
                for tb in pg.get("text_blocks", []):
                    for ln in tb.get("lines", []):
                        bb = ln["bbox"]
                        if line_y < bb["y0"] <= line_y + 40:
                            text = " ".join(s.get("text", "") for s in ln.get("spans", []))
                            if _FOOTER_PATTERN.search(text):
                                is_chrome = True
                                break
                    if is_chrome:
                        break
            # Above widget within proximity (header separator)?
            if wy0 - proximity <= line_y <= wy0 - 2:
                for tb in pg.get("text_blocks", []):
                    for ln in tb.get("lines", []):
                        bb = ln["bbox"]
                        if line_y - 40 <= bb["y0"] <= line_y:
                            text = " ".join(s.get("text", "") for s in ln.get("spans", []))
                            if _FOOTER_PATTERN.search(text):
                                is_chrome = True
                                break
                    if is_chrome:
                        break
            if is_chrome:
                break
        if not is_chrome:
            keep.append(w)
    return keep


def _has_inline_text(widget: FusedWidget, pg: dict, side: str,
                      max_gap: float = 35.0, y_tolerance: float = 5.0) -> bool:
    """Check if there's a text label on widget's y-row, on the given side.

    Catches `text ____` (right has nothing, but left has label) and
    `____ text` (right has label) and `text ____ text` (both sides).
    Used to suppress wide-margin snaps that would clobber inline placements.
    """
    wy_center = (widget.rect[1] + widget.rect[3]) / 2
    for tb in pg.get("text_blocks", []):
        for ln in tb.get("lines", []):
            bb = ln["bbox"]
            line_cy = (bb["y0"] + bb["y1"]) / 2
            if abs(line_cy - wy_center) > y_tolerance:
                continue
            text = " ".join(s.get("text", "") for s in ln.get("spans", []))
            if len(text.strip()) < 2:
                continue
            if side == "left":
                gap = widget.rect[0] - bb["x1"]
                if 0 < gap < max_gap:
                    return True
            elif side == "right":
                gap = bb["x0"] - widget.rect[2]
                if 0 < gap < max_gap:
                    return True
    return False


def wide_widget_right_margin_snap(widgets: list[FusedWidget],
                                  analysis: dict,
                                  min_width_for_snap: float = 250.0) -> list[FusedWidget]:
    """For wide free-form widgets, push x1 to the page's modal text-line right edge.

    Mirror of the left-margin snap. Widgets short of the right margin without
    apparent reason get pulled to the page's modal right edge so wide answer
    fields span edge-to-edge. Skips widgets with inline text on their right
    (like 'Address: ___ Phone:'), which should keep their detected x1.
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    for pno, pg in pages.items():
        from collections import Counter
        right_edges = Counter(round(ln["bbox"]["x1"])
                              for tb in pg.get("text_blocks", [])
                              for ln in tb.get("lines", [])
                              if ln["bbox"]["x1"] > 400)
        if not right_edges:
            continue
        margin_x, _ = right_edges.most_common(1)[0]
        for w in widgets:
            if w.page != pno or w.type != "text" or w.underline_anchored:
                continue
            wd = w.rect[2] - w.rect[0]
            if wd < min_width_for_snap:
                continue
            # Skip if there's a text token directly to the right (inline label)
            if _has_inline_text(w, pg, side="right"):
                continue
            if w.rect[2] < margin_x - 5:
                w.rect = (w.rect[0], w.rect[1], float(margin_x), w.rect[3])
    return widgets


def snap_widget_top_below_text(widgets: list[FusedWidget], analysis: dict,
                                gap: float = 2.0,
                                inline_threshold: float = 5.0) -> list[FusedWidget]:
    """Push widget y0 down so it doesn't overlap printed text directly above.

    For each widget, find the highest text line whose CENTER is clearly above
    the widget's center (more than inline_threshold pt — so we don't move
    inline 'Date: ___' widgets that share a y row with their label). If that
    text line's bottom (y1) is at or below the widget's top, push the widget
    down to text.y1 + gap so it sits cleanly below.
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    for w in widgets:
        if w.type != "text" or w.underline_anchored:
            continue
        pg = pages.get(w.page)
        if not pg:
            continue
        wcx = (w.rect[0] + w.rect[2]) / 2
        wcy = (w.rect[1] + w.rect[3]) / 2
        max_text_y1_above = -1.0
        for tb in pg.get("text_blocks", []):
            for ln in tb.get("lines", []):
                bb = ln["bbox"]
                line_cy = (bb["y0"] + bb["y1"]) / 2
                # Strictly above (not inline)
                if line_cy >= wcy - inline_threshold:
                    continue
                # Horizontally overlapping the widget
                hx_overlap = min(w.rect[2], bb["x1"]) - max(w.rect[0], bb["x0"])
                if hx_overlap < 10:
                    continue
                if bb["y1"] > max_text_y1_above:
                    max_text_y1_above = bb["y1"]
        if max_text_y1_above < 0:
            continue
        # If the closest text-above bottom is at or past widget top → push down
        if max_text_y1_above > w.rect[1] - gap:
            new_y0 = max_text_y1_above + gap
            new_y1 = max(w.rect[3], new_y0 + 12)
            w.rect = (w.rect[0], new_y0, w.rect[2], new_y1)
    return widgets


def wide_widget_left_margin_snap(widgets: list[FusedWidget],
                                 analysis: dict,
                                 min_width_for_snap: float = 250.0,
                                 page_left_margin: float = 72.0) -> list[FusedWidget]:
    """For wide free-form text widgets, push x0 to the page body-text left margin.

    The user prefers wide multi-line answer fields to span edge-to-edge from
    the body margin to the right margin. CF often anchors x0 to where the
    visible underline starts (e.g., x=238 mid-page after a printed prompt),
    but visually a wide answer block should claim the full body width.

    Skips cell-anchored widgets and short widgets (<min_width).
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    for w in widgets:
        if w.type != "text" or w.underline_anchored:
            continue
        wd = w.rect[2] - w.rect[0]
        if wd < min_width_for_snap:
            continue
        pg = pages.get(w.page)
        if not pg:
            continue
        # Skip if there's a text token directly to the left (inline label like
        # 'Date: ____' — moving x0 to body margin would clobber the colon.)
        if _has_inline_text(w, pg, side="left"):
            continue
        # Use modal text-line x0 if available; fallback to default left margin
        from collections import Counter
        line_x0s = Counter(round(ln["bbox"]["x0"])
                           for tb in pg.get("text_blocks", [])
                           for ln in tb.get("lines", []))
        target_x0 = page_left_margin
        if line_x0s:
            modal_x0, _ = line_x0s.most_common(1)[0]
            target_x0 = float(modal_x0)
        if w.rect[0] > target_x0 + 5:
            w.rect = (target_x0, w.rect[1], w.rect[2], w.rect[3])
    return widgets


def widget_cluster_snap(widgets: list[FusedWidget],
                        gap: float = 18.0,
                        min_cluster_size: int = 2) -> list[FusedWidget]:
    """Group nearby x0 values per page into clusters and snap each to the cluster median.

    Catches near-misses where 5 widgets at x=87 and 2 at x=86 should all be at x=86.5.
    Only snaps widgets that aren't already underline_anchored (preserves table-cell
    and underline-anchored widgets that have a deterministic correct position).
    """
    by_page: dict[int, list[FusedWidget]] = {}
    for w in widgets:
        by_page.setdefault(w.page, []).append(w)
    for p, page_ws in by_page.items():
        text_ws = [w for w in page_ws if w.type == "text" and not w.underline_anchored]
        if len(text_ws) < min_cluster_size:
            continue
        # Single-linkage clustering by x0
        sorted_ws = sorted(text_ws, key=lambda w: w.rect[0])
        clusters: list[list[FusedWidget]] = [[sorted_ws[0]]]
        for w in sorted_ws[1:]:
            if w.rect[0] - clusters[-1][-1].rect[0] <= gap:
                clusters[-1].append(w)
            else:
                clusters.append([w])
        for c in clusters:
            if len(c) < min_cluster_size:
                continue
            xs = sorted(w.rect[0] for w in c)
            median = xs[len(xs) // 2]
            for w in c:
                dx = median - w.rect[0]
                if abs(dx) > 0.01:
                    w.rect = (w.rect[0] + dx, w.rect[1], w.rect[2] + dx, w.rect[3])
    return widgets


def column_snap(widgets: list[FusedWidget],
                analysis: dict,
                tolerance: float = 6.0,
                max_peaks: int = 4) -> list[FusedWidget]:
    """Snap unanchored text widgets to the nearest source-PDF text-line indent.

    Reads paragraph indents from the source PDF analysis (not from widget
    clusters), so widgets align with the visible text body, not with whatever
    drift CF introduced. Skips pages with >max_peaks indents (likely tabular).
    Only snaps text widgets whose underline_anchored is False.
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    by_page: dict[int, list[FusedWidget]] = {}
    for w in widgets:
        by_page.setdefault(w.page, []).append(w)
    for p, page_ws in by_page.items():
        pg = pages.get(p)
        if not pg:
            continue
        peak_xs = _text_line_peaks(pg)
        if not peak_xs or len(peak_xs) > max_peaks:
            continue
        for w in page_ws:
            if w.type != "text" or w.underline_anchored:
                continue
            best_x = min(peak_xs, key=lambda px: abs(px - w.rect[0]))
            if abs(best_x - w.rect[0]) <= tolerance:
                dx = best_x - w.rect[0]
                w.rect = (w.rect[0] + dx, w.rect[1], w.rect[2] + dx, w.rect[3])
    return widgets


# ── Bordered-table cell snap ──────────────────────────────────────────────


def _cells_from_white_fill(pg: dict, min_w: float, min_h: float, max_h: float) -> list[tuple]:
    """Method 1: white-filled rect drawings of typical row height."""
    cells = []
    for dr in pg.get("drawings", []):
        if dr["kind"] != "rect":
            continue
        fill = dr.get("fill")
        if fill is None or list(fill) != [1.0, 1.0, 1.0]:
            continue
        r = dr["rect"]
        w_pt = r["x1"] - r["x0"]
        h_pt = r["y1"] - r["y0"]
        if not (min_w <= w_pt and min_h <= h_pt <= max_h):
            continue
        cells.append((r["x0"], r["y0"], r["x1"], r["y1"]))
    return cells


def _cluster_axis_positions(values: list[float], tol: float = 1.0) -> list[float]:
    """Cluster nearly-equal positions into representative values (mean of cluster)."""
    if not values:
        return []
    xs = sorted(values)
    out = [[xs[0]]]
    for v in xs[1:]:
        if v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


def _cells_from_line_grid(pg: dict, min_w: float, min_h: float, max_h: float) -> list[tuple]:
    """Method 2: reconstruct cells from horizontal + vertical border lines.

    Many forms (e.g. PP-205 page 4, DE-405 inventory) draw table cells as a
    grid of stroke-only lines without any white fill. This finds rows by
    pairing consecutive horizontal lines, columns by intersecting verticals.
    """
    horizs: list[tuple[float, float, float]] = []  # (y, x0, x1)
    verts: list[tuple[float, float, float]] = []   # (x, y0, y1)
    for dr in pg.get("drawings", []):
        if dr["kind"] not in ("rect", "line"):
            continue
        r = dr["rect"]
        w = r["x1"] - r["x0"]
        h = r["y1"] - r["y0"]
        if h <= 1.5 and w >= min_w:
            horizs.append((r["y0"], r["x0"], r["x1"]))
        elif w <= 1.5 and h >= min_h:
            verts.append((r["x0"], r["y0"], r["y1"]))
    if not horizs or not verts:
        return []
    # Cluster horizontals by y
    h_ys = _cluster_axis_positions([y for y, _, _ in horizs])
    h_segments_by_y = {y: [] for y in h_ys}
    for y, x0, x1 in horizs:
        nearest = min(h_ys, key=lambda hy: abs(hy - y))
        h_segments_by_y[nearest].append((x0, x1))
    # Cluster verticals by x
    v_xs = _cluster_axis_positions([x for x, _, _ in verts])
    v_segments_by_x = {x: [] for x in v_xs}
    for x, y0, y1 in verts:
        nearest = min(v_xs, key=lambda vx: abs(vx - x))
        v_segments_by_x[nearest].append((y0, y1))
    cells: list[tuple] = []
    for i in range(len(h_ys) - 1):
        y_top, y_bot = h_ys[i], h_ys[i + 1]
        cell_h = y_bot - y_top
        if not (min_h <= cell_h <= max_h):
            continue
        # Verticals that span this row
        spanning = sorted(
            vx for vx in v_xs
            if any(s[0] <= y_top + 1.5 and s[1] >= y_bot - 1.5
                   for s in v_segments_by_x[vx])
        )
        if len(spanning) < 2:
            continue
        for k in range(len(spanning) - 1):
            x0 = spanning[k]
            x1 = spanning[k + 1]
            if x1 - x0 < min_w:
                continue
            cells.append((x0, y_top, x1, y_bot))
    return cells


def _detect_table_cells(pg: dict,
                        min_w: float = 30.0,
                        min_h: float = 8.0,
                        max_h: float = 50.0) -> list[tuple]:
    """Detect table cells using white-fill rects, falling back to a line grid."""
    cells = _cells_from_white_fill(pg, min_w, min_h, max_h=25.0)
    if cells:
        return cells
    return _cells_from_line_grid(pg, min_w, min_h, max_h)


def _detect_header_cells(pg: dict, cells: list[tuple]) -> list[tuple]:
    """Cells whose interior contains substantive printed text are HEADER cells.

    A widget placed inside a header cell would overlay the printed column label
    (e.g. "Name" / "Address"). Filter those cells from snap targets.
    """
    if not cells:
        return []
    # Collect all text spans (with bbox) on this page
    spans: list[tuple] = []
    for tb in pg.get("text_blocks", []):
        for ln in tb.get("lines", []):
            for sp in ln.get("spans", []):
                txt = sp.get("text", "").strip()
                if not txt or len(txt) < 2:
                    continue
                bb = sp["bbox"]
                cx = (bb["x0"] + bb["x1"]) / 2
                cy = (bb["y0"] + bb["y1"]) / 2
                spans.append((cx, cy))
    headers = []
    for c in cells:
        if any(c[0] <= sx <= c[2] and c[1] <= sy <= c[3] for sx, sy in spans):
            headers.append(c)
    return headers


def _detect_vertical_separators(pg: dict, min_h: float = 8.0) -> list[float]:
    """Cluster x-positions of vertical stroke lines (column boundaries, margins)."""
    xs: list[float] = []
    for dr in pg.get("drawings", []):
        if dr["kind"] not in ("rect", "line"):
            continue
        r = dr["rect"]
        w = r["x1"] - r["x0"]
        h = r["y1"] - r["y0"]
        if w <= 1.5 and h >= min_h:
            xs.append(r["x0"])
    return _cluster_axis_positions(xs, tol=1.5)


def _detect_horizontal_separators(pg: dict, min_w: float = 30.0) -> list[float]:
    """Cluster y-positions of horizontal stroke lines (table row boundaries)."""
    ys: list[float] = []
    for dr in pg.get("drawings", []):
        if dr["kind"] not in ("rect", "line"):
            continue
        r = dr["rect"]
        w = r["x1"] - r["x0"]
        h = r["y1"] - r["y0"]
        if h <= 1.5 and w >= min_w:
            ys.append(r["y0"])
    return _cluster_axis_positions(ys, tol=1.5)


def table_cell_snap(widgets: list[FusedWidget], analysis: dict,
                    padding: float = 0.0,
                    max_h_ratio: float = 1.5) -> list[FusedWidget]:
    """For each text widget whose center falls inside a table cell, fit it to that cell.

    Picks the OUTER (largest-area) containing cell so widgets in adjacent
    columns share borders pixel-tight. Skips header cells (cells whose
    interior contains printed label text) — placing a widget there would
    overlay the column label. Skips widgets whose CF-detected height is much
    taller than the cell (legit multi-row spans).
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    for w in widgets:
        if w.type != "text":
            continue
        pg = pages.get(w.page)
        if not pg:
            continue
        cells = _detect_table_cells(pg)
        if not cells:
            continue
        headers = _detect_header_cells(pg, cells)
        cx = (w.rect[0] + w.rect[2]) / 2
        cy = (w.rect[1] + w.rect[3]) / 2
        widget_h = w.rect[3] - w.rect[1]
        # Skip if widget's center sits in a header cell — would overlay text.
        if any(h[0] <= cx <= h[2] and h[1] <= cy <= h[3] for h in headers):
            continue
        # Most-INclusive (largest area) cell containing the widget center —
        # outer borders are shared between adjacent cells.
        containing = [c for c in cells if c[0] <= cx <= c[2] and c[1] <= cy <= c[3]]
        if not containing:
            continue
        cell = max(containing, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))
        cell_h = cell[3] - cell[1]
        if w.source == "cf" and widget_h > max_h_ratio * cell_h:
            continue
        w.rect = (cell[0] + padding, cell[1] + padding,
                  cell[2] - padding, cell[3] - padding)
        w.underline_anchored = True
    return widgets


# ── Step 6: add ours-only widgets ─────────────────────────────────────────


def add_ours_only(fused: list[FusedWidget], ours: list[FusedWidget], thr: float = 0.30) -> list[FusedWidget]:
    out = list(fused)
    by_page: dict[int, list[FusedWidget]] = {}
    for w in fused:
        by_page.setdefault(w.page, []).append(w)
    for w in ours:
        match = False
        for ow in by_page.get(w.page, []):
            if iou(w.rect, ow.rect) >= thr:
                match = True
                break
        if not match:
            out.append(FusedWidget(
                page=w.page, rect=w.rect, type=w.type, name=w.name,
                source="ours", label=w.label, section=w.section,
            ))
    return out


# ── Step 7: NMS ───────────────────────────────────────────────────────────


def nms_overlap(widgets: list[FusedWidget], thr: float = 0.5) -> list[FusedWidget]:
    by_page: dict[int, list[FusedWidget]] = {}
    for w in widgets:
        by_page.setdefault(w.page, []).append(w)
    keep: list[FusedWidget] = []
    for p, ws in by_page.items():
        # sort: cf before ours, larger before smaller (ties broken by area)
        ws.sort(key=lambda w: (-1 if w.source == "cf" else 0,
                               -((w.rect[2] - w.rect[0]) * (w.rect[3] - w.rect[1]))))
        kept: list[FusedWidget] = []
        for w in ws:
            if any(iou(w.rect, k.rect) >= thr for k in kept):
                continue
            kept.append(w)
        keep.extend(kept)
    return keep


# ── Step 8: naming via field_detector helpers ─────────────────────────────


def page_text_lines(pg: dict) -> list[TextLine]:
    lines: list[TextLine] = []
    for tb in pg.get("text_blocks", []):
        for ln in tb.get("lines", []):
            spans_data = ln.get("spans", [])
            spans = [
                TextSpan(
                    bbox=Rect(**s["bbox"]),
                    text=s.get("text", ""),
                    font=s.get("font", ""),
                    size=s.get("size", 10.0),
                    color=s.get("color", 0),
                    flags=s.get("flags", 0),
                )
                for s in spans_data
            ]
            lines.append(TextLine(bbox=Rect(**ln["bbox"]), spans=spans))
    return lines


def _slugify(text: str) -> str:
    # Strip leading numbering like "10.", "1.", "2a.", "iii.", "(b)".
    s = re.sub(r"^\s*[\(\[]?[\divxIVX]+[a-z]?[\)\].]*\s*", "", text)
    # Strip the underline-glyph runs (literal underscores used for fillable lines).
    s = re.sub(r"_{2,}", " ", s)
    # Strip non-word chars.
    s = re.sub(r"[^\w\s]", " ", s).strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = s.strip("_")
    return s[:48] or "field"


def _ours_name_index(form_id: str) -> list[tuple[int, tuple, str]]:
    """Load the v2 naming JSON: list of (page, rect, name) tuples.

    Reusing already-clean names where a fused rect lands near a v2 rect avoids
    the messy slugify step.
    """
    p = ROOT / "intermediate_layer1" / "naming" / f"{form_id}.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text())
    out = []
    for f in d.get("fields", []):
        r = f["rect"]
        out.append(
            (f["page"], (r["x0"], r["y0"], r["x1"], r["y1"]), f.get("field_name", ""))
        )
    return out


def _form_id_from_filename(filename: str) -> str:
    m = re.match(r"^([A-Z]+-?\d+(?:\([A-Z]\))?)", filename)
    return m.group(1) if m else filename


def revert_bad_widgets_to_v2(widgets: list[FusedWidget],
                             analysis: dict,
                             ours_widgets: list[FusedWidget]) -> list[FusedWidget]:
    """Detect fused widgets that swallow a printed label and revert to v2 geometry.

    A 'bad' widget is one where a printed text line BEGINS inside the widget's
    horizontal extent — meaning the widget stretched left/right past where the
    real underline starts/ends and now sits over a label like 'Date:' or 'Phone:'.
    Specifically:
      - widget.y range contains the line's center
      - line.x0 sits BETWEEN widget.x0 + 5 and widget.x1 - 10
        (i.e., the label starts well inside the widget — not at its edge)

    On revert: use v2's widget at the same position; if v2 has none, drop.
    """
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    v2_by_page: dict[int, list[FusedWidget]] = {}
    for w in ours_widgets:
        v2_by_page.setdefault(w.page, []).append(w)
    keep = []
    for w in widgets:
        if w.type != "text" or w.underline_anchored:
            keep.append(w)
            continue
        pg = pages.get(w.page)
        if not pg:
            keep.append(w)
            continue
        bad = False
        for tb in pg.get("text_blocks", []):
            for ln in tb.get("lines", []):
                bb = ln["bbox"]
                line_cy = (bb["y0"] + bb["y1"]) / 2
                if not (w.rect[1] - 1 <= line_cy <= w.rect[3] + 1):
                    continue
                # Label starts well inside the widget = swallowed
                if w.rect[0] + 5 <= bb["x0"] <= w.rect[2] - 10:
                    text = " ".join(s.get("text", "") for s in ln.get("spans", [])).strip()
                    # Filter: only treat as a label if it has alpha chars and width
                    if len(text) >= 2 and any(c.isalpha() for c in text) \
                       and (bb["x1"] - bb["x0"]) >= 10:
                        bad = True
                        break
            if bad:
                break
        if not bad:
            keep.append(w)
            continue
        replacement = None
        wcx = (w.rect[0] + w.rect[2]) / 2
        wcy = (w.rect[1] + w.rect[3]) / 2
        for ow in v2_by_page.get(w.page, []):
            if ow.type != "text":
                continue
            if ow.rect[0] <= wcx <= ow.rect[2] and ow.rect[1] <= wcy <= ow.rect[3]:
                replacement = ow
                break
        if replacement:
            w.rect = replacement.rect
            w.name = replacement.name or w.name
            w.source = "ours"
            keep.append(w)
    return keep


def normalize_row_indices(widgets: list[FusedWidget]) -> list[FusedWidget]:
    """Re-number table-column row widgets by visual y position.

    Groups widgets per (page, x0-bin) — i.e. one cluster per visual column —
    and within each column with >=3 widgets, sorts by y and assigns a uniform
    `<prefix>_rowN` name where `<prefix>` is the modal `_rowN` prefix in that
    column. Fixes:
      • inherited row-scramble from v2 (`row5` widget at visual row 2)
      • column-leader widgets misnamed from section headings (e.g.
        `a_real_property` for what should be `property_description_row1`)
    """
    pattern = re.compile(r"^(.+)_row\d+(?:_\d+)?$")
    by_col: dict[tuple, list[FusedWidget]] = {}
    for w in widgets:
        if w.type != "text" or not w.name:
            continue
        x0_bin = round(w.rect[0])
        x1_bin = round(w.rect[2])
        by_col.setdefault((w.page, x0_bin, x1_bin), []).append(w)
    from collections import Counter
    for (page, x0_bin, x1_bin), group in by_col.items():
        if len(group) < 3:
            continue
        # Find modal _rowN prefix — that's the column's true field-name prefix.
        prefixes = []
        for w in group:
            m = pattern.match(w.name)
            if m:
                prefixes.append(m.group(1))
        if not prefixes:
            continue
        common_prefix = Counter(prefixes).most_common(1)[0][0]
        # Sort by y; renumber every widget in the column (including non-_rowN ones).
        group.sort(key=lambda w: w.rect[1])
        for i, w in enumerate(group, 1):
            w.name = f"{common_prefix}_row{i}"
    return widgets


def name_widgets(widgets: list[FusedWidget], analysis: dict, form_filename: str) -> list[FusedWidget]:
    pages = {pg["page_number"]: pg for pg in analysis["pages"]}
    seen_names: set[str] = set()
    counter: Counter = Counter()
    form_id = _form_id_from_filename(form_filename)
    ours_index = _ours_name_index(form_id)
    for w in widgets:
        # If widget came from ours and already has a name, keep it
        if w.source == "ours" and w.name:
            seen_names.add(w.name)
            continue
        # Try to reuse a v2 name where positions match (IoU >= 0.30)
        reused = ""
        for (op, orect, oname) in ours_index:
            if op != w.page or not oname:
                continue
            if iou(w.rect, orect) >= 0.30:
                reused = oname
                break
        if reused:
            base = reused
        else:
            pg = pages.get(w.page)
            if not pg:
                continue
            tlines = page_text_lines(pg)
            rect = Rect(x0=w.rect[0], y0=w.rect[1], x1=w.rect[2], y1=w.rect[3])
            label = _find_nearby_label(rect, tlines)
            section = _find_section_header(rect, tlines)
            w.label = label
            w.section = section
            base_parts = [section, label] if section else [label]
            base = "_".join(_slugify(p) for p in base_parts if p) or f"{w.type}_p{w.page}"
        candidate = base
        counter[base] += 1
        if counter[base] > 1:
            candidate = f"{base}_{counter[base]}"
        while candidate in seen_names:
            counter[base] += 1
            candidate = f"{base}_{counter[base]}"
        seen_names.add(candidate)
        w.name = candidate
    return widgets


# ── Step 9: write fused PDF ───────────────────────────────────────────────


def write_fused(src_pdf: pathlib.Path, widgets: list[FusedWidget], out_pdf: pathlib.Path):
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    d = fitz.open(src_pdf)
    by_page: dict[int, list[FusedWidget]] = {}
    for w in widgets:
        by_page.setdefault(w.page, []).append(w)
    # PDF AcroForm flag bits (PyMuPDF exposes by name on Widget)
    PDF_TX_FIELD_FLAG_MULTILINE = 1 << 12  # 4096
    for pno, page in enumerate(d):
        for w in by_page.get(pno, []):
            x0, y0, x1, y1 = w.rect
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue  # skip degenerate
            r = fitz.Rect(x0, y0, x1, y1)
            wd = fitz.Widget()
            if w.type == "check":
                wd.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
            elif w.type == "sig":
                wd.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE
            else:
                wd.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            wd.field_name = w.name or f"unnamed_p{pno}_{int(r.x0)}_{int(r.y0)}"
            wd.rect = r
            wd.border_color = (0.5, 0.5, 0.5)
            wd.border_width = 0.5
            wd.text_fontsize = 10
            # Multiline + top-left for tall text fields (>=20pt) — Word-like behavior.
            if w.type == "text" and (r.y1 - r.y0) >= 20:
                wd.field_flags = (wd.field_flags or 0) | PDF_TX_FIELD_FLAG_MULTILINE
                wd.text_format = 0  # left alignment is default; ensure top via no /Q centering
            page.add_widget(wd)
    d.save(out_pdf, deflate=True)
    d.close()


# ── Driver ────────────────────────────────────────────────────────────────


def fuse_one(cat: str, name: str) -> dict:
    stem = pathlib.Path(name).stem
    src = ORIG_DIR / cat / name
    cf_pdf = CF_DIR / cat / f"{stem}_commonforms.pdf"
    ours_pdf = OURS_DIR / cat / f"{stem}_fillable.pdf"
    out_pdf = OUT_DIR / cat / f"{stem}_fused.pdf"

    cf_widgets = load_pdf_widgets(cf_pdf)
    ours_widgets = load_pdf_widgets(ours_pdf)
    analysis = load_analysis(name)

    underlines, checkboxes = extract_underlines_and_boxes(analysis["pages"])
    text_h, cw, ch = canonical_sizes(underlines, checkboxes)

    # Step 4: snap CF text widgets to underlines.
    # Step 5 (canonical-checkbox snap) intentionally removed: at image-size 3200,
    # CF detects checkbox dimensions matching the visible Wingdings glyph exactly,
    # so trust the detector rather than a heuristic cluster.
    snapped: list[FusedWidget] = []
    for w in cf_widgets:
        if w.type == "text":
            ulines = underlines.get(w.page, [])
            snapped.append(snap_text_to_underline(w, ulines, text_h))
        else:
            snapped.append(w)

    # Inject ours-only widgets while positions still match CF's original output
    # (before any cell/column snapping that would shift the geometry).
    fused = add_ours_only(snapped, ours_widgets)
    # Drop ours-injected widgets that fell outside any cell on cell-heavy pages
    # (e.g. v2's mis-placed header-row widgets).
    fused = filter_ours_in_table_areas(fused, analysis)
    # Drop any widget (CF or ours) whose center sits in a column-header cell.
    fused = filter_widgets_in_header_cells(fused, analysis)
    # Snap any text widget (CF or ours-injected) sitting inside a table cell.
    fused = table_cell_snap(fused, analysis)
    # Multi-row table widgets: snap y0/y1 to nearest horizontal cell borders.
    fused = snap_widget_y_to_cell_rows(fused, analysis)
    # Per-form: canonicalize all checkbox sizes to the modal (square if near-square)
    fused = canonicalize_checkboxes(fused)
    # Raster overlay: snap each checkbox to the actual ink centroid in the source PDF
    raster_stats = raster_snap_checkboxes(fused, src)
    # Snap free-form text widgets to vertical separators (column bounds + margins)
    fused = vertical_separator_snap(fused, analysis)
    # Snap free-form right edges near the page margin to the page modal x1
    fused = right_margin_snap(fused, analysis)
    # Rule C: column-snap remaining unanchored text widgets to body-text indents
    fused = column_snap(fused, analysis)
    # Final widget-cluster pass: align near-misses (e.g. 5 widgets at 87, 2 at 86)
    fused = widget_cluster_snap(fused)
    # Wide free-form widgets snap their x0 to the page's body-text left margin.
    fused = wide_widget_left_margin_snap(fused, analysis)
    # Wide free-form widgets snap their x1 to the page's modal right edge.
    fused = wide_widget_right_margin_snap(fused, analysis)
    # Right-edge cluster: right-align widgets sharing a column to common x1
    fused = widget_x1_cluster_snap(fused)
    # Push widget y0 below the previous text line if the widget overlaps it.
    fused = snap_widget_top_below_text(fused, analysis)
    # Drop margin bleeders + clamp gentle overshoots to page margins
    fused = page_margin_clamp(fused)
    # Drop widgets sitting above a statutory-citation footer separator line.
    fused = filter_footer_line_widgets(fused, analysis)
    # Conservative orphan filter — only drops truly isolated CF false positives.
    fused = filter_orphan_widgets(fused, analysis)
    # Per-widget revert: detect widgets stretched over printed text labels and
    # replace with v2's widget at that position (or drop if v2 has none).
    fused = revert_bad_widgets_to_v2(fused, analysis, ours_widgets)
    # NMS to drop overlapping widgets after all snaps have moved geometry around
    fused = nms_overlap(fused)
    # Step 8: naming
    fused = name_widgets(fused, analysis, name)
    # Renumber `_rowN` widgets in each column by visual y position so row indices
    # match the visual layout (fixes v2-inherited row-index scrambles).
    fused = normalize_row_indices(fused)
    # Step 9: write
    write_fused(src, fused, out_pdf)

    cf_n = sum(1 for w in fused if w.source == "cf")
    ours_n = sum(1 for w in fused if w.source == "ours")
    return dict(
        form=name, total=len(fused), from_cf=cf_n, from_ours=ours_n,
        canonical_text_h=text_h, canonical_check=(cw, ch),
        raster_snapped=raster_stats["snapped"],
        raster_fallback=raster_stats["fallback"],
        out=str(out_pdf.relative_to(ROOT)),
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{'form':60s}  total  cf  ours  raster_snap/fb")
    for cat, name in PANEL:
        r = fuse_one(cat, name)
        print(f"{r['form'][:60]:60s}  {r['total']:5d}  {r['from_cf']:3d}  "
              f"{r['from_ours']:4d}  {r['raster_snapped']:3d}/{r['raster_fallback']:<3d}")
    print(f"\nFused PDFs in {OUT_DIR}")


if __name__ == "__main__":
    main()
