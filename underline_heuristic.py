"""
underline_heuristic.py — Native PDF heuristic AcroForm field generator.

Detection pipeline (in order, per page):
  1. Drawn underlines      — thin black filled rects → text widget above the line
  2. Table cells           — white filled rects in a grid → text widget inset inside cell
  3. Drawn checkboxes      — small black squares → checkbox widget
  4. Text underscores      — '____' sequences in text layer → text widget on the run
  5. Wingdings checkboxes  — Wingdings/symbol glyph bullets → checkbox widget
  6. Inline fields         — label ending with ':' + blank horizontal space → text widget
  7. Implied fields        — blank vertical gap between text lines → text widget

Label search (multi-directional, in priority order):
  1. Left of field (inline label)
  2. Above field (label on preceding line)
  3. Below field, centered (caption-style label beneath the line)

Section context tracking:
  - Detects "section headers" (short lines ending with ':' followed by sub-fields)
  - Prefixes sub-field names with section context: "attorney_name" not just "name"
  - Resets context when a new section header or question number is encountered

LLM naming pass (stub):
  - After geometry is complete, optional pass sends field list + page text to LLM
  - LLM assigns semantic names with full context
  - Run separately: python underline_heuristic.py <pdf> --name-with-llm

Usage:
    python underline_heuristic.py <pdf_path> [--output <out.pdf>] [--preview] [--dpi N]
"""

import argparse
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

# ─────────────────────────────────────────────────────────────────────────────
# Tuning constants
# ─────────────────────────────────────────────────────────────────────────────

# Underline detection
UNDERLINE_MAX_HEIGHT   = 1.5    # pt — max height of a thin line to treat as underline
UNDERLINE_MIN_WIDTH    = 40.0   # pt — min width to treat as a field line
UNDERLINE_BLACK_MAX    = 0.15   # fill component below this is "black"

# Widget placement for underlines
FIELD_HEIGHT           = 14.0   # pt — widget height above the underline
FIELD_SIDE_INSET       = 1.0    # pt — shrink widget left/right from line edges
FIELD_BOTTOM_PAD       = 1.0    # pt — gap between widget bottom and line top

# Table cell detection (white fills in a grid)
CELL_MAX_FILL          = 0.05   # above this → not white
CELL_MIN_WIDTH         = 30.0   # pt
CELL_MIN_HEIGHT        = 8.0    # pt
CELL_MAX_HEIGHT        = 60.0   # pt — taller → multiline
CELL_MAX_HEIGHT_MULTI  = 120.0  # pt — above this → skip entirely
CELL_INSET             = 2.0    # pt — inset inside cell rect

# Drawn checkbox detection
CHECKBOX_MIN_SIZE      = 7.0    # pt
CHECKBOX_MAX_SIZE      = 16.0   # pt
CHECKBOX_ASPECT_MAX    = 0.35   # max deviation from 1:1

# Wingdings/symbol glyph checkboxes
WINGDINGS_CB_INSET     = 1.5    # pt — inset to avoid bleeding into label text
CHECKBOX_GLYPHS        = {0xF0A8, 0xF06F, 0xF0FE, 0xF0FC, 0x2610, 0x2611, 0x2612,
                           0x25A1, 0x25A0, 0xF06E, 0xF0A3}

# Text underscore field detection
UNDERSCORE_MIN_WIDTH   = 20.0   # pt — min width of merged underscore run

# Inline field detection (label + blank space on same line)
INLINE_FIELD_MIN_WIDTH = 60.0   # pt
INLINE_LABEL_SUFFIXES  = (":", ": ", "?", "? ", "no.", "no. ", "#")

# Implied field detection (blank vertical gap)
IMPLIED_MIN_GAP        = 18.0   # pt
IMPLIED_MAX_GAP        = 80.0   # pt
IMPLIED_FIELD_H        = 14.0   # pt
IMPLIED_LEFT_MARGIN    = 72.0   # pt
IMPLIED_RIGHT_MARGIN   = 540.0  # pt

# Label search distances
LABEL_LEFT_MAX         = 160.0  # pt — search left for inline label
LABEL_ABOVE_MAX        = 28.0   # pt — search above
LABEL_BELOW_MAX        = 20.0   # pt — search below for centered caption
LABEL_CENTER_TOLERANCE = 0.20   # fraction of field width — for centered-below check

# Section context tracking
SECTION_HEADER_MAX_LEN = 80     # chars — headers are short
SECTION_RESET_KEYWORDS = re.compile(
    r'^\s*(\d{1,2}[a-z]?[.)]\s|page\s+\d|whereas|in witness)',
    re.IGNORECASE,
)

# Full-page rule exclusion
PAGE_RULE_MARGIN       = 25.0   # pt

# Widget appearance
FILL_COLOR_TEXT        = (0.93, 0.95, 1.0)
FILL_COLOR_MULTI       = (0.95, 1.0, 0.95)
BORDER_COLOR           = (0.5, 0.5, 0.5)
BORDER_WIDTH           = 0.5
TEXT_COLOR             = (0, 0, 0)

# Preview overlay colors per source
PREVIEW_COLORS = {
    "underline":       (1,    0,    0   ),  # red
    "cell":            (0,    0.6,  0   ),  # green
    "square":          (0,    0,    1   ),  # blue
    "wingdings":       (0,    0,    0.8 ),  # blue
    "text_underscore": (0.8,  0.4,  0   ),  # orange
    "inline":          (0.5,  0,    0.8 ),  # purple
    "implied":         (0,    0.6,  0.6 ),  # teal
}


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_black(fill) -> bool:
    if fill is None:
        return False
    if isinstance(fill, (int, float)):
        return fill <= UNDERLINE_BLACK_MAX
    return all(c <= UNDERLINE_BLACK_MAX for c in fill[:3])


def _is_white(fill) -> bool:
    if fill is None:
        return False
    if isinstance(fill, (int, float)):
        return fill >= (1.0 - CELL_MAX_FILL)
    return all(c >= (1.0 - CELL_MAX_FILL) for c in fill[:3])


def _rw(r: fitz.Rect) -> float:
    return r.x1 - r.x0


def _rh(r: fitz.Rect) -> float:
    return r.y1 - r.y0


def _h_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))


def _overlaps(a: fitz.Rect, b: fitz.Rect, min_h_overlap: float = 20.0) -> bool:
    return (
        a.y0 < b.y1 and a.y1 > b.y0
        and _h_overlap(a, b) > min_h_overlap
    )


# ─────────────────────────────────────────────────────────────────────────────
# Text extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_text_lines(page: fitz.Page) -> list[dict]:
    """Return all non-empty text lines as {text, rect, spans}."""
    lines = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    for b in blocks:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = " ".join(s["text"].strip() for s in spans)
            lx0 = min(fitz.Rect(s["bbox"]).x0 for s in spans)
            lx1 = max(fitz.Rect(s["bbox"]).x1 for s in spans)
            ly0 = min(fitz.Rect(s["bbox"]).y0 for s in spans)
            ly1 = max(fitz.Rect(s["bbox"]).y1 for s in spans)
            lines.append({
                "text": text,
                "rect": fitz.Rect(lx0, ly0, lx1, ly1),
                "spans": spans,
            })
    lines.sort(key=lambda l: (l["rect"].y0, l["rect"].x0))
    return lines


def _get_text_spans(page: fitz.Page) -> list[dict]:
    """Return all non-empty text spans as {text, rect}."""
    spans = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    for b in blocks:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for s in line.get("spans", []):
                text = s.get("text", "").strip()
                if text:
                    spans.append({"text": text, "rect": fitz.Rect(s["bbox"])})
    return spans


# ─────────────────────────────────────────────────────────────────────────────
# Multi-directional label search
# ─────────────────────────────────────────────────────────────────────────────

def find_label(field_rect: fitz.Rect, spans: list[dict]) -> str:
    """
    Find the best label for a field rect. Priority:
      1. Left (inline): same y-band, to the left
      2. Above: horizontally overlapping, just above
      3. Below-centered: horizontally centered on field, just below (caption style)
    """
    fx0, fy0, fx1, fy1 = field_rect
    fw = fx1 - fx0
    fc = (fx0 + fx1) / 2  # field center x

    candidates = []
    for sp in spans:
        sr = sp["rect"]
        text = sp["text"]
        if not text:
            continue

        # 1. Left (inline): right edge of span is left of field, same y band
        if (sr.x1 <= fx0 + 5
                and sr.x1 >= fx0 - LABEL_LEFT_MAX
                and sr.y0 < fy1 + 4
                and sr.y1 > fy0 - 4):
            dist = fx0 - sr.x1
            candidates.append((0, dist, text))
            continue

        # 2. Above: horizontal overlap, span is above the field
        h_ov = _h_overlap(sr, field_rect)
        if (h_ov > 5
                and sr.y1 <= fy0 + 4
                and sr.y1 >= fy0 - LABEL_ABOVE_MAX):
            dist = fy0 - sr.y1
            candidates.append((1, dist, text))
            continue

        # 3. Below-centered: span is just below, horizontally centered on field
        sc = (sr.x0 + sr.x1) / 2
        if (sr.y0 >= fy1 - 2
                and sr.y0 <= fy1 + LABEL_BELOW_MAX
                and abs(sc - fc) <= fw * LABEL_CENTER_TOLERANCE):
            dist = sr.y0 - fy1
            candidates.append((2, dist, text))

    if not candidates:
        return ""

    # Sort by weighted score: left=best, then compare above vs below-centered by
    # distance. Below-centered gets a slight discount (0.8×) so it beats above
    # when distances are similar — this matches the common Maine probate form
    # pattern where the label is printed under the line.
    # candidates = (priority, dist, text)  priority: 0=left, 1=above, 2=below-centered
    # Give below-centered a 20% distance discount so it beats above when equidistant
    # (Maine forms commonly print the label centered under the fill line)
    DISCOUNT = {0: 1.0, 1: 1.0, 2: 0.8}
    candidates.sort(key=lambda c: (0 if c[0] == 0 else 1, DISCOUNT[c[0]] * c[1]))
    return candidates[0][2]


# ─────────────────────────────────────────────────────────────────────────────
# Section context tracker
# ─────────────────────────────────────────────────────────────────────────────

class SectionContext:
    """
    Tracks the current "section header" as fields are detected top-to-bottom.
    A section header is a text line that:
      - Is short (< SECTION_HEADER_MAX_LEN chars)
      - Ends with ':' or is a question number pattern
      - Is followed by multiple fields below it
    Sub-fields get their name prefixed with the section slug.
    """

    def __init__(self, text_lines: list[dict]):
        self._lines = text_lines
        self._current: str = ""
        self._current_y: float = 0.0
        self._next_section_y: float = float("inf")
        self._build_index()

    def _build_index(self):
        """Pre-identify candidate section headers and their y ranges."""
        self._sections: list[tuple[float, float, str]] = []  # (y0, y1_next, slug)
        for i, line in enumerate(self._lines):
            text = line["text"].strip()
            if not text or len(text) > SECTION_HEADER_MAX_LEN:
                continue
            # Section header patterns:
            # - Short line ending with ':'
            # - Numbered question: "1." "10." "11a."
            is_header = (
                (text.endswith(":") and len(text) > 4)
                or bool(re.match(r'^\d{1,2}[a-z]?[.)]\s', text))
            )
            if not is_header:
                continue
            y0 = line["rect"].y0
            # Next section starts at next header
            y1_next = float("inf")
            for j in range(i + 1, len(self._lines)):
                next_text = self._lines[j]["text"].strip()
                if len(next_text) <= SECTION_HEADER_MAX_LEN and (
                    next_text.endswith(":") or re.match(r'^\d{1,2}[a-z]?[.)]\s', next_text)
                ):
                    y1_next = self._lines[j]["rect"].y0
                    break
            slug = _slugify(text)
            self._sections.append((y0, y1_next, slug))

    def get_prefix(self, field_y: float) -> str:
        """Return the section slug that applies at this y position, or ''."""
        best = ""
        best_y0 = -1.0
        for (y0, y1_next, slug) in self._sections:
            if y0 <= field_y < y1_next and y0 > best_y0:
                best = slug
                best_y0 = y0
        return best


# ─────────────────────────────────────────────────────────────────────────────
# Drawn-table detector
# ─────────────────────────────────────────────────────────────────────────────
# When thin underlines repeat at the same x-column ranges across 3+ y-positions,
# they form a drawn table (row separators). Instead of placing stub fields above
# each line, we create tall cells spanning between consecutive row separators.

DRAWN_TABLE_COL_TOL    = 6.0    # pt — x-range match tolerance for same column
DRAWN_TABLE_MIN_ROWS   = 3      # minimum number of rows to call it a table
DRAWN_TABLE_SKIP_NARROW_COL = 80.0  # columns narrower than this are item-numbers → skip


def _group_underlines_by_y(underlines: list[fitz.Rect],
                            tol: float = 2.0) -> list[list[fitz.Rect]]:
    """Group underlines into rows where all share approximately the same y0."""
    if not underlines:
        return []
    sorted_ul = sorted(underlines, key=lambda r: r.y0)
    groups: list[list[fitz.Rect]] = [[sorted_ul[0]]]
    for r in sorted_ul[1:]:
        if abs(r.y0 - groups[-1][0].y0) <= tol:
            groups[-1].append(r)
        else:
            groups.append([r])
    return groups


def _col_sig(rects: list[fitz.Rect]) -> list[tuple[float, float]]:
    """Return sorted (x0, x1) column signature for a group of underlines."""
    return sorted((r.x0, r.x1) for r in rects)


def _cols_match(sig_a: list[tuple], sig_b: list[tuple],
                tol: float = DRAWN_TABLE_COL_TOL) -> bool:
    """True if two column signatures are the same shape."""
    if len(sig_a) != len(sig_b):
        return False
    return all(abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol
               for a, b in zip(sig_a, sig_b))


def _detect_drawn_table_cells(
        underlines: list[fitz.Rect],
) -> tuple[list[fitz.Rect], set[int]]:
    """
    Detect drawn tables (underlines used as row separators) and return:
      - list of cell rects (one per user-fillable column per row-gap)
      - set of indices into `underlines` that belong to the table
        (these should not receive individual field placements)
    """
    groups = _group_underlines_by_y(underlines)
    # Only consider groups with 2+ segments (multi-column rows)
    groups = [g for g in groups if len(g) >= 2]

    # Find the dominant column signature (most common across groups)
    from collections import Counter
    sig_counts: Counter = Counter()
    for g in groups:
        sig = tuple(_col_sig(g))
        sig_counts[sig] += 1

    if not sig_counts:
        return [], set()

    dom_sig, dom_count = sig_counts.most_common(1)[0]
    if dom_count < DRAWN_TABLE_MIN_ROWS:
        return [], set()

    # All groups matching the dominant signature = table rows
    table_groups = [g for g in groups if _cols_match(_col_sig(g), list(dom_sig))]
    table_groups.sort(key=lambda g: g[0].y0)

    # Build set of underline indices that are table members
    ul_index = {id(r): i for i, r in enumerate(underlines)}
    used_idx: set[int] = set()
    for g in table_groups:
        for r in g:
            if id(r) in ul_index:
                used_idx.add(ul_index[id(r)])

    # Create cells between consecutive table rows, for each non-narrow column
    cells: list[fitz.Rect] = []
    row_ys = [g[0].y0 for g in table_groups]

    for ri in range(len(row_ys) - 1):
        y_top = row_ys[ri]
        y_bot = row_ys[ri + 1]
        for (cx0, cx1) in dom_sig:
            col_w = cx1 - cx0
            if col_w < DRAWN_TABLE_SKIP_NARROW_COL:
                continue  # item-number / row-number column
            cells.append(fitz.Rect(cx0, y_top, cx1, y_bot))

    return cells, used_idx


# ─────────────────────────────────────────────────────────────────────────────
# Field detection — drawing paths
# ─────────────────────────────────────────────────────────────────────────────

def _detect_from_paths(page: fitz.Page) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Returns (underlines, white_cells, drawn_checkboxes) from raw drawing paths.
    """
    pw = page.rect.width
    paths = page.get_drawings()

    underlines = []
    white_cells = []
    drawn_cbs = []

    for p in paths:
        r = p["rect"]
        w = _rw(r)
        h = _rh(r)
        fill = p.get("fill")

        if w < 1.0 or h < 0.01:
            continue

        if (h <= UNDERLINE_MAX_HEIGHT
                and w >= UNDERLINE_MIN_WIDTH
                and _is_black(fill)
                and w < (pw - PAGE_RULE_MARGIN)):
            underlines.append(r)
            continue

        if (_is_white(fill)
                and w >= CELL_MIN_WIDTH
                and CELL_MIN_HEIGHT <= h <= CELL_MAX_HEIGHT_MULTI):
            white_cells.append(r)
            continue

        if (CHECKBOX_MIN_SIZE <= w <= CHECKBOX_MAX_SIZE
                and CHECKBOX_MIN_SIZE <= h <= CHECKBOX_MAX_SIZE):
            aspect = abs(w - h) / max(w, h)
            color = p.get("color")
            if aspect <= CHECKBOX_ASPECT_MAX and (_is_black(fill) or _is_black(color)):
                drawn_cbs.append(r)

    return underlines, white_cells, drawn_cbs


# ─────────────────────────────────────────────────────────────────────────────
# Field detection — text layer
# ─────────────────────────────────────────────────────────────────────────────

def _detect_text_underscores(page: fitz.Page) -> list[fitz.Rect]:
    """Find '____' runs in the text layer; return merged bounding rects."""
    hits = page.search_for("____", quads=False)
    merged = []
    for r in hits:
        if (merged
                and abs(r.y0 - merged[-1].y0) < 3.0
                and r.x0 <= merged[-1].x1 + 5.0):
            merged[-1] = fitz.Rect(
                min(merged[-1].x0, r.x0), min(merged[-1].y0, r.y0),
                max(merged[-1].x1, r.x1), max(merged[-1].y1, r.y1),
            )
        else:
            merged.append(fitz.Rect(r))

    result = []
    for r in merged:
        if _rw(r) < UNDERSCORE_MIN_WIDTH:
            continue
        # Reject footnote separator lines: "_____ ¹ 18-C M.R.S. ..." pattern.
        # The citation appears on the line immediately below the separator.
        if r.x0 < 90:  # only check left-margin underscore runs
            below_clip = fitz.Rect(r.x0 - 5, r.y1, r.x0 + 80, r.y1 + 14)
            below_words = [w[4] for w in page.get_text("words", clip=below_clip)
                           if not set(w[4]).issubset(set("_ \t"))]
            # Footnote pattern: first word below is a digit or starts with superscript
            if below_words and (below_words[0][0].isdigit() or
                                below_words[0][0] in "¹²³⁴⁵⁶⁷⁸⁹"):
                continue
        result.append(r)
    return result


def _detect_wingdings_cbs(page: fitz.Page) -> list[fitz.Rect]:
    """Find Wingdings/symbol checkbox glyphs; return their inset rects."""
    seen = []
    rawdict = page.get_text("rawdict", flags=0)
    for block in rawdict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    c = char.get("c", "")
                    if c and ord(c) in CHECKBOX_GLYPHS:
                        r = fitz.Rect(char["bbox"])
                        dup = any(
                            abs(r.x0 - s.x0) < 3 and abs(r.y0 - s.y0) < 3
                            for s in seen
                        )
                        if not dup:
                            seen.append(r)
    # Apply inset
    return [
        fitz.Rect(r.x0 + WINGDINGS_CB_INSET, r.y0 + WINGDINGS_CB_INSET,
                  r.x1 - WINGDINGS_CB_INSET, r.y1 - WINGDINGS_CB_INSET)
        for r in seen
    ]


def _detect_inline_fields(page: fitz.Page,
                           existing: list[fitz.Rect],
                           text_lines: list[dict]) -> list[fitz.Rect]:
    """
    Label ending with ':' followed by blank horizontal space → inline field.
    Suppressed when the label introduces a checkbox group (e.g. 'Check one:').
    """
    rects = []
    pw = page.rect.width
    right_margin = min(IMPLIED_RIGHT_MARGIN, pw - 36.0)

    # Sort lines for lookahead
    sorted_lines = sorted(text_lines, key=lambda l: l["rect"].y0)
    line_y0s = [l["rect"].y0 for l in sorted_lines]

    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    for b in blocks:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            for idx, span in enumerate(spans):
                text = span["text"].rstrip()
                lower = text.lower()
                if not any(lower.endswith(suf) for suf in INLINE_LABEL_SUFFIXES):
                    continue

                label_x1 = fitz.Rect(span["bbox"]).x1
                sy0 = fitz.Rect(span["bbox"]).y0
                sy1 = fitz.Rect(span["bbox"]).y1

                next_x0 = (fitz.Rect(spans[idx + 1]["bbox"]).x0
                            if idx + 1 < len(spans) else right_margin)

                fx0 = label_x1 + 2.0
                fx1 = next_x0 - 2.0
                if fx1 - fx0 < INLINE_FIELD_MIN_WIDTH:
                    continue

                # Find the matching sorted_line index for lookahead checks
                closest_line_idx = min(
                    range(len(sorted_lines)),
                    key=lambda i: abs(sorted_lines[i]["rect"].y0 - sy0),
                    default=0,
                )
                ahead = sorted_lines[closest_line_idx + 1:
                                     closest_line_idx + 1 + _CHECKBOX_LOOKAHEAD]

                # Suppress if label introduces a checkbox group
                cb_count = sum(1 for l in ahead if _is_checkbox_option_line(l))
                is_cb_intro = (ahead and
                               (cb_count / len(ahead)) >= _CHECKBOX_GROUP_THRESH)
                if is_cb_intro and not _has_other_text_option(ahead):
                    continue

                # Suppress if label introduces labeled sub-fields below
                # (e.g. "Attorney for Applicant, if any:" → Name / Address / Phone below)
                if _introduces_subfield_group(
                        sorted_lines[closest_line_idx], sorted_lines, closest_line_idx):
                    continue

                # Don't create inline fields for parenthetical qualifier lines
                # e.g. "(Check all that apply):", "(if any):", "(check one):"
                span_text_stripped = span["text"].strip()
                if span_text_stripped.startswith("("):
                    continue

                r = fitz.Rect(fx0, sy0, fx1, sy1)
                if not any(_overlaps(r, e) for e in existing):
                    rects.append(r)
                    existing.append(r)
    return rects


# ─── Affirmation / perjury no-field zone patterns ────────────────────────────
# When a line matches, implied/inline fields are suppressed from that point.
AFFIRMATION_PATTERNS = re.compile(
    r'(under\s+penalty\s+of\s+perjury'
    r'|i\s+hereby\s+certif'
    r'|i\s+declare\s+(under|that)'
    r'|i\s+swear\s+(under|that)'
    r'|in\s+witness\s+whereof'
    r'|subscribed\s+and\s+sworn'
    r')',
    re.IGNORECASE,
)

# Labels that indicate sub-field groups (suppress inline on the parent line)
SUBFIELD_LABELS = re.compile(
    r'^(name|address|phone|email|fax|zip|city|state|county|'
    r'bar\s+number|license|date\s+of\s+birth|signature|title)\b',
    re.IGNORECASE,
)
# Max x0 for a sub-field label to be considered "left-margin" (attached to parent section)
SUBFIELD_LABEL_MAX_X0 = 100.0

# ─── Court-only / no-field zone patterns ────────────────────────────────────
# When a line matches, ALL fields from that y-position to page bottom are suppressed.
COURT_ONLY_PATTERNS = re.compile(
    r'(fees?\s+due\s+(upon|on)\s+filing'
    r'|for\s+(court|register|office)\s+use'
    r'|court\s+use\s+only'
    r'|do\s+not\s+write\s+(below|above)\s+this'
    r'|filing\s+fee\s*[:$]'
    r'|action\s+by\s+the\s+(register|judge|court)'
    r'|bond\s+requirement'
    r')',
    re.IGNORECASE,
)

def _find_court_only_y(text_lines: list[dict]) -> float:
    """Return the y0 at which a court-only zone starts, or inf if none found."""
    for line in text_lines:
        if COURT_ONLY_PATTERNS.search(line["text"]):
            return line["rect"].y0
    return float("inf")


def _find_affirmation_y(text_lines: list[dict]) -> float:
    """Return the y0 at which an affirmation/perjury block starts, or inf."""
    for line in text_lines:
        if AFFIRMATION_PATTERNS.search(line["text"]):
            return line["rect"].y0
    return float("inf")


def _introduces_subfield_group(line: dict, sorted_lines: list[dict], idx: int) -> bool:
    """
    True if this line is a section header whose sub-fields are labeled text lines
    directly below it at the left margin (e.g. 'Attorney for Applicant, if any:').
    """
    ahead = sorted_lines[idx + 1: idx + 1 + 8]
    subfield_count = 0
    for l in ahead:
        t = l["text"].strip()
        x0 = l["rect"].x0
        if x0 <= SUBFIELD_LABEL_MAX_X0 and SUBFIELD_LABELS.match(t):
            subfield_count += 1
    return subfield_count >= 2


# ─── Checkbox-intro line detection ───────────────────────────────────────────
# How many lines to look ahead when deciding if a prompt introduces a checkbox group
_CHECKBOX_LOOKAHEAD     = 8
# Fraction of lookahead lines that must be checkbox-style to suppress inline field
_CHECKBOX_GROUP_THRESH  = 0.5

def _is_checkbox_option_line(line: dict) -> bool:
    """True if this line looks like a checkbox/radio option (not a prompt)."""
    text = line["text"].strip()
    x0 = line["rect"].x0
    # Short and indented
    if x0 > _CHECKBOX_LABEL_MAX_X0 and len(text) < 80:
        return True
    # Starts with a Wingdings glyph
    if text and ord(text[0]) in CHECKBOX_GLYPHS:
        return True
    return False


def _has_other_text_option(lines_below: list[dict]) -> bool:
    """True if any of the nearby lines is an 'Other ___' or 'Other:' option."""
    for line in lines_below:
        t = line["text"].strip().lower()
        if re.search(r'\bother\b.{0,20}([_:$]|____)', t):
            return True
    return False


def _introduces_checkbox_group(line: dict, sorted_lines: list[dict], idx: int) -> bool:
    """
    True if this line introduces a checkbox group (should not get an inline field).
    Looks ahead up to _CHECKBOX_LOOKAHEAD lines.
    """
    ahead = sorted_lines[idx + 1: idx + 1 + _CHECKBOX_LOOKAHEAD]
    if not ahead:
        return False
    cb_count = sum(1 for l in ahead if _is_checkbox_option_line(l))
    return (cb_count / len(ahead)) >= _CHECKBOX_GROUP_THRESH


# Patterns that mark a line as a question prompt (worth creating an implied field below)
_PROMPT_LINE_RE = re.compile(
    r'(\:\s*$'                          # ends with colon
    r'|\?\s*$'                          # ends with question mark
    r'|^\s*\d{1,2}[a-z]?[.)]\s'        # numbered item: 1. 2a. 10.
    r'|explain'                          # "Explain." type lines
    r')',
    re.IGNORECASE,
)

# Patterns that mark a line as a checkbox/radio option (NOT a prompt)
_CHECKBOX_OPTION_RE = re.compile(
    r'^[\uf0a8\uf06f\uf0fe\u2610\u2611\u25a1\u25a0]',  # starts with glyph
)

# Max x0 for a line to be considered a "narrow indented" checkbox label
_CHECKBOX_LABEL_MAX_X0 = 115.0  # pt — lines starting this far right are probably options


def _is_prompt_line(line: dict) -> bool:
    """Return True if this line looks like a question/prompt (not a checkbox option)."""
    text = line["text"].strip()
    x0 = line["rect"].x0

    # Short indented lines are checkbox options, not prompts
    if x0 > _CHECKBOX_LABEL_MAX_X0 and len(text) < 60:
        return False
    if _CHECKBOX_OPTION_RE.match(text):
        return False

    return bool(_PROMPT_LINE_RE.search(text))


_NUMBERED_Q_RE = re.compile(r'^\s*\d{1,2}[a-z]?[.)]\s')


def _detect_implied_fields(page: fitz.Page,
                            existing: list[fitz.Rect],
                            text_lines: list[dict],
                            affirmation_y: float = float("inf")) -> list[fitz.Rect]:
    """
    Blank vertical gap between consecutive text lines → implied text field.
    Fires when:
      - The preceding line is a question prompt, OR
      - The next line starts a new numbered question (gap = answer area for prev Q)
    Suppressed when:
      - The preceding line introduces a checkbox group
      - The gap is inside the affirmation/perjury block
    """
    rects = []
    pw = page.rect.width
    ph = page.rect.height
    right_margin = min(IMPLIED_RIGHT_MARGIN, pw - 36.0)

    if not text_lines:
        return rects

    sorted_lines = sorted(text_lines, key=lambda l: l["rect"].y0)
    SENTINEL = {"text": "###SENTINEL###", "rect": fitz.Rect(0, ph, pw, ph)}
    sorted_lines.append(SENTINEL)

    for i in range(len(sorted_lines) - 1):
        line = sorted_lines[i]
        next_line = sorted_lines[i + 1]

        lb = line["rect"].y1
        nt = next_line["rect"].y0
        gap = nt - lb

        if gap < IMPLIED_MIN_GAP or gap > IMPLIED_MAX_GAP:
            continue

        # Skip sentinel gaps (last text → page bottom) to avoid page-bottom orphans
        if next_line is SENTINEL:
            continue

        # Suppress inside or entering affirmation block
        gap_center = lb + gap / 2
        if gap_center >= affirmation_y:
            continue

        # Determine if this gap warrants a field:
        preceding_is_prompt = _is_prompt_line(line)
        next_is_new_question = bool(_NUMBERED_Q_RE.match(next_line["text"]))

        if not preceding_is_prompt and not next_is_new_question:
            continue

        # Suppress if preceding line introduces a checkbox group
        if _introduces_checkbox_group(line, sorted_lines, i):
            continue

        field_h = min(gap - 4.0, IMPLIED_FIELD_H)
        fy0 = lb + (gap - field_h) / 2.0
        fy1 = fy0 + field_h

        # Don't place field rect inside the affirmation block
        if fy0 >= affirmation_y:
            continue

        r = fitz.Rect(IMPLIED_LEFT_MARGIN, fy0, right_margin, fy1)

        if not any(_overlaps(r, e) for e in existing):
            rects.append(r)
            existing.append(r)
    return rects


# ─────────────────────────────────────────────────────────────────────────────
# Master detect_fields — assembles all detectors for one page
# ─────────────────────────────────────────────────────────────────────────────

def detect_fields(page: fitz.Page) -> list[dict]:
    """
    Run all detectors and return a list of field dicts:
      {rect, kind, source, label, section_prefix}
    """
    pw = page.rect.width
    text_lines    = _get_text_lines(page)
    spans         = _get_text_spans(page)
    section_ctx   = SectionContext(text_lines)
    court_y       = _find_court_only_y(text_lines)
    affirmation_y = _find_affirmation_y(text_lines)
    existing: list[fitz.Rect] = []
    fields: list[dict] = []

    def _add(rect, kind, source):
        fields.append({
            "rect": rect,
            "kind": kind,
            "source": source,
            "label": "",
            "section_prefix": "",
        })
        existing.append(rect)

    # ── 1. Drawn underlines ───────────────────────────────────────────────
    underlines, white_cells, drawn_cbs = _detect_from_paths(page)
    cell_tops = [(c.y0, c.x0, c.x1) for c in white_cells]

    # Detect drawn tables (underlines acting as row separators) first.
    # Those underlines get converted to tall cell fields; skip individual stubs.
    drawn_table_cells, table_ul_idx = _detect_drawn_table_cells(underlines)

    for i, ul in enumerate(underlines):
        if i in table_ul_idx:
            continue  # handled as drawn table cell below

        # Skip if this is the top border of a white-fill table row
        is_border = any(
            abs(cy0 - ul.y0) < 3.0 and (min(cx1, ul.x1) - max(cx0, ul.x0)) > 20.0
            for (cy0, cx0, cx1) in cell_tops
        )
        if is_border:
            continue
        fy1 = ul.y0 - FIELD_BOTTOM_PAD
        fy0 = fy1 - FIELD_HEIGHT
        fx0 = ul.x0 + FIELD_SIDE_INSET
        fx1 = ul.x1 - FIELD_SIDE_INSET
        if fx1 - fx0 >= 10.0:
            _add(fitz.Rect(fx0, fy0, fx1, fy1), "text", "underline")

    # Add drawn-table cells (these are essentially tall text cells)
    for cell in drawn_table_cells:
        if any(_overlaps(cell, e, min_h_overlap=20.0) for e in existing):
            continue
        h = _rh(cell)
        inset = CELL_INSET
        _add(fitz.Rect(cell.x0 + inset, cell.y0 + inset,
                       cell.x1 - inset, cell.y1 - inset),
             "text", "cell")

    # ── 2. Table cells ────────────────────────────────────────────────────
    # Deduplicate nested cells: if cell A is entirely contained within cell B,
    # discard A (keep the outer cell as the answer area).
    def _cell_contains(outer: fitz.Rect, inner: fitz.Rect, tol: float = 3.0) -> bool:
        return (outer.x0 <= inner.x0 + tol and outer.y0 <= inner.y0 + tol
                and outer.x1 >= inner.x1 - tol and outer.y1 >= inner.y1 - tol)

    deduped_cells = []
    for cell in white_cells:
        contained_by_another = any(
            _cell_contains(other, cell) and other is not cell
            for other in white_cells
            if abs(_rw(other) - _rw(cell)) > 5 or abs(_rh(other) - _rh(cell)) > 5
        )
        if not contained_by_another:
            deduped_cells.append(cell)

    for cell in deduped_cells:
        if any(_overlaps(cell, e, min_h_overlap=20.0) for e in existing):
            continue
        h = _rh(cell)
        kind = "multiline" if h > CELL_MAX_HEIGHT else "text"
        inset = CELL_INSET
        _add(fitz.Rect(cell.x0 + inset, cell.y0 + inset,
                       cell.x1 - inset, cell.y1 - inset), kind, "cell")

    # ── 3. Drawn checkboxes ───────────────────────────────────────────────
    seen_sq = []
    for sq in drawn_cbs:
        dup = any(abs(sq.x0 - s.x0) < 2 and abs(sq.y0 - s.y0) < 2 for s in seen_sq)
        if not dup:
            seen_sq.append(sq)
            _add(sq, "checkbox", "square")

    # ── 4. Text underscores ───────────────────────────────────────────────
    for r in _detect_text_underscores(page):
        if not any(_overlaps(r, e, min_h_overlap=20.0) for e in existing):
            _add(r, "text", "text_underscore")

    # ── 5. Wingdings checkboxes ───────────────────────────────────────────
    for r in _detect_wingdings_cbs(page):
        dup = any(abs(r.x0 - e.x0) < 5 and abs(r.y0 - e.y0) < 5 for e in existing)
        if not dup:
            _add(r, "checkbox", "wingdings")

    # ── 6. Inline fields ──────────────────────────────────────────────────
    for r in _detect_inline_fields(page, existing, text_lines):
        _add(r, "text", "inline")

    # ── 7. Implied fields ─────────────────────────────────────────────────
    for r in _detect_implied_fields(page, existing, text_lines, affirmation_y):
        _add(r, "text", "implied")

    # ── Attach labels + section context ──────────────────────────────────
    for f in fields:
        r = f["rect"]
        f["label"] = find_label(r, spans)
        f["section_prefix"] = section_ctx.get_prefix((r.y0 + r.y1) / 2)

    # ── Court-only zone filter ─────────────────────────────────────────────
    # Drop ALL fields whose center-y is at or below the court-only boundary.
    # No exemptions — even drawn underlines inside a court section are excluded.
    if court_y < float("inf"):
        def _cy(f): return (f["rect"].y0 + f["rect"].y1) / 2
        fields = [f for f in fields if _cy(f) < court_y]

    # ── Text-overlap filter ────────────────────────────────────────────────
    # Reject fields where the rect interior contains significant printed text.
    # Underlines/squares/wingdings/text_underscores are anchored to PDF primitives
    # and are largely trusted — but we do a looser check on underlines to catch:
    #   • Footnote rule lines (short underline near footnote citation text)
    #   • Decorative lines below paragraph-ending words ("hereby:", etc.)
    # For underlines we use larger insets so only text well inside the rect triggers.
    SKIP_OVERLAP_CHECK = {"square", "wingdings", "text_underscore"}
    UL_H_INSET = 6.0   # tighter horizontal inset for underline check
    UL_V_INSET = 3.0   # tighter vertical inset for underline check
    cleaned = []
    for f in fields:
        if f["source"] in SKIP_OVERLAP_CHECK:
            cleaned.append(f)
            continue
        r = f["rect"]
        if f["source"] == "underline":
            # Use larger insets — only flag text clearly inside, not edge-touching labels
            clip = fitz.Rect(r.x0 + UL_H_INSET, r.y0 + UL_V_INSET,
                             r.x1 - UL_H_INSET, r.y1 - UL_V_INSET)
        else:
            h_inset = min(3.0, max(1.0, (r.x1 - r.x0) * 0.03))
            v_inset = min(2.0, max(0.5, (r.y1 - r.y0) * 0.05))
            clip = fitz.Rect(r.x0 + h_inset, r.y0 + v_inset,
                             r.x1 - h_inset, r.y1 - v_inset)
        if clip.is_empty:
            cleaned.append(f)
            continue
        words_inside = page.get_text("words", clip=clip)
        if words_inside:
            continue
        cleaned.append(f)

    return cleaned


# ─────────────────────────────────────────────────────────────────────────────
# Field naming
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = text.lower().strip()
    # Strip trailing punctuation
    text = text.rstrip(":.,?#")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:45] or "field"


def assign_names(fields: list[dict]) -> list[dict]:
    """
    Assign unique field names using label + section context.
    Name format: {section_prefix}_{label_slug} or just {label_slug} if no prefix.
    Numbered suffixes added for duplicates.
    """
    used: dict[str, int] = {}
    result = []
    for f in fields:
        label = f.get("label", "")
        prefix = f.get("section_prefix", "")
        kind = f["kind"]

        label_slug = _slugify(label) if label else kind

        # Avoid redundancy: if label already starts with prefix words, skip prefix
        if prefix and not label_slug.startswith(prefix[:8]):
            base = f"{prefix}_{label_slug}"
        else:
            base = label_slug

        # Deduplicate
        count = used.get(base, 0) + 1
        used[base] = count
        name = base if count == 1 else f"{base}_{count}"

        result.append({**f, "name": name})
    return result


# ─────────────────────────────────────────────────────────────────────────────
# LLM naming pass (stub — wire in later)
# ─────────────────────────────────────────────────────────────────────────────

def llm_naming_pass(fields: list[dict], page_text: str, model: str = "haiku") -> list[dict]:
    """
    Optional post-processing: send field list + page text to an LLM and get
    back contextually-named fields.

    Input to LLM:
      - Structured list of detected fields with coords, raw label, section prefix
      - Full page text (for context)

    Output: same list with 'name' overwritten with LLM-assigned names.

    Stub — implement when geometry is finalized across 10+ forms.
    The prompt should ask the LLM to:
      1. Identify which fields belong to the same logical group
      2. Assign {context}_{field_type} names (e.g. personal_representative_first_name)
      3. Distinguish repeated labels (Name appears 6 times → pr_name, heir_name_1, etc.)
      4. Return JSON: [{original_name, new_name}]
    """
    raise NotImplementedError(
        "LLM naming pass not yet implemented. "
        "Geometry must be finalized across 10+ forms first. "
        "See comments in this function for the prompt design."
    )


# ─────────────────────────────────────────────────────────────────────────────
# AcroForm writer
# ─────────────────────────────────────────────────────────────────────────────

def write_fields(source_pdf: Path, output_pdf: Path) -> int:
    """Detect and write AcroForm fields to all pages. Returns total field count."""
    doc = fitz.open(str(source_pdf))
    used_names: set[str] = set()
    total = 0

    for pg_num in range(len(doc)):
        page = doc[pg_num]
        raw = detect_fields(page)
        named = assign_names(raw)

        for f in named:
            name = f["name"]
            if name in used_names:
                sfx = 2
                while f"{name}_{sfx}" in used_names:
                    sfx += 1
                name = f"{name}_{sfx}"
            used_names.add(name)

            rect = f["rect"]
            kind = f["kind"]

            widget = fitz.Widget()
            widget.field_name = name
            widget.rect = rect
            widget.border_color = BORDER_COLOR
            widget.border_width = BORDER_WIDTH

            if kind == "checkbox":
                widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
                widget.field_value = "Off"
            else:
                widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                if kind == "multiline":
                    widget.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
                    widget.fill_color = FILL_COLOR_MULTI
                else:
                    widget.fill_color = FILL_COLOR_TEXT
                widget.text_color = TEXT_COLOR
                widget.text_font = "TiRo"  # Times-Roman (matches Maine probate form font)
                widget.text_fontsize = 0    # auto-size (matches GT behavior)

            try:
                page.add_widget(widget)
                total += 1
            except Exception as e:
                print(f"  WARNING p{pg_num} '{name}': {e}")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    # Use incremental save so the original content streams (fonts, images) are
    # untouched — only the AcroForm additions are appended to the file.
    doc.save(str(output_pdf), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Preview renderer
# ─────────────────────────────────────────────────────────────────────────────

def render_previews(source_pdf: Path, output_prefix: Path, dpi: int = 150):
    """Render all pages with field overlays. Saves {prefix}_p{n}.png."""
    doc = fitz.open(str(source_pdf))
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    for pg_num in range(len(doc)):
        page = doc[pg_num]
        spans = _get_text_spans(page)
        raw = detect_fields(page)
        named = assign_names(raw)

        tmp = fitz.open()
        tmp.insert_pdf(doc, from_page=pg_num, to_page=pg_num)
        tmp_page = tmp[0]

        for f in named:
            r = f["rect"]
            color = PREVIEW_COLORS.get(f["source"], (0.5, 0, 0.5))
            tmp_page.draw_rect(r, color=color, width=1.0)
            label = f.get("label", "")
            name = f.get("name", "")
            display = (label or name)[:22]
            if display:
                try:
                    tmp_page.insert_text(
                        (r.x0 + 1, r.y0 + 7),
                        display,
                        fontsize=4.5,
                        color=color,
                    )
                except Exception:
                    pass

        out = output_prefix.parent / f"{output_prefix.stem}_p{pg_num}.png"
        tmp_page.get_pixmap(matrix=mat, alpha=False).save(str(out))
        tmp.close()
        print(f"  preview p{pg_num}: {out}")

    doc.close()
    print(f"  legend: red=underline  green=cell  blue=checkbox  "
          f"orange=underscore  purple=inline  teal=implied")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Heuristic AcroForm field generator")
    parser.add_argument("pdf", help="Source PDF path")
    parser.add_argument("--output", "-o", help="Output fillable PDF (default: auto)")
    parser.add_argument("--outdir", "-d", help="Output directory (overrides --output)")
    parser.add_argument("--preview", "-p", action="store_true",
                        help="Render PNG previews of all pages")
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()

    source = Path(args.pdf)
    if not source.exists():
        print(f"ERROR: {source} not found", file=sys.stderr)
        sys.exit(1)

    if args.outdir:
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        out_pdf = outdir / f"{source.stem}_heuristic.pdf"
    elif args.output:
        out_pdf = Path(args.output)
    else:
        out_pdf = source.parent / f"{source.stem}_fillable.pdf"

    # Dry-run report
    doc = fitz.open(str(source))
    total = 0
    for pg_num in range(len(doc)):
        page = doc[pg_num]
        raw = detect_fields(page)
        named = assign_names(raw)
        print(f"\nPage {pg_num}: {len(named)} fields")
        for f in named:
            print(f"  [{f['kind']:10s}|{f['source']:14s}] "
                  f"{f['name']:45s} label={f.get('label','')!r:.40s}")
        total += len(named)
    doc.close()
    print(f"\nTotal: {total} fields")

    if total == 0:
        print("No fields detected.")
        sys.exit(0)

    n = write_fields(source, out_pdf)
    print(f"Saved: {out_pdf}  ({n} fields written)")

    if args.preview:
        render_previews(source, out_pdf.with_suffix(".preview"), dpi=args.dpi)

    # Copy ground truth if it exists alongside source
    gt_path = source.parent.parent / "samples" / source.name
    if not gt_path.exists():
        # Try stem match (ground truth may have slightly different name)
        stem = source.stem.split(" ")[0]  # e.g. "DE-101(I)"
        candidates = list((source.parent.parent / "samples").glob(f"{stem}*"))
        gt_path = candidates[0] if candidates else None

    if gt_path and gt_path.exists() and args.outdir:
        import shutil
        gt_dest = outdir / f"{source.stem}_GROUND_TRUTH.pdf"
        shutil.copy(str(gt_path), str(gt_dest))
        print(f"Ground truth: {gt_dest}")


if __name__ == "__main__":
    main()
