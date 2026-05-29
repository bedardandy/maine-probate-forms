"""Addendum page renderer for overflowed form answers.

When a form's answer is too large to fit in its allotted widget(s), the
remainder is rendered as an addendum page appended to the PDF, and the
inline widget is rewritten to read "See Addendum Q1" (or similar).

Page layout:
  [== black header band: "ADDENDUM — Form X" ==]

  Q1. <prompt label>
      <answer body, wrapped>

  Q2. <prompt label>
      ...

                                      [Form X — Addendum page N of M]

Pagination is deterministic: lines are pre-wrapped by font-metric width
and laid out by tracked Y; a new addendum page is spawned when remaining
vertical space can't hold the next line.
"""
from __future__ import annotations
from typing import Iterable

import fitz  # PyMuPDF


HEADER_HEIGHT = 36
FOOTER_HEIGHT = 24
MARGIN_X = 54
TOP_MARGIN = 20
BOTTOM_PADDING = 10

BODY_FONT = "helv"
BODY_SIZE = 11
HEAD_FONT = "hebo"  # Helvetica-Bold
HEAD_SIZE = 12
LINE_GAP = 14  # for 11pt body
HEAD_GAP = 18  # after a question header
Q_BLOCK_GAP = 14  # between Q blocks


def _wrap_lines(text: str, width_pt: float,
                fontname: str = BODY_FONT,
                fontsize: float = BODY_SIZE) -> list[str]:
    """Greedy word-wrap into lines fitting width_pt at the given font."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        cur = ""
        for word in paragraph.split():
            candidate = (cur + " " + word).strip() if cur else word
            if fitz.get_text_length(
                    candidate, fontname=fontname, fontsize=fontsize) <= width_pt:
                cur = candidate
            else:
                if cur:
                    lines.append(cur)
                # If a single word is wider than the rect, force-break it.
                while fitz.get_text_length(
                        word, fontname=fontname,
                        fontsize=fontsize) > width_pt and len(word) > 1:
                    # Binary search the largest prefix that fits.
                    lo, hi = 1, len(word)
                    while lo < hi:
                        mid = (lo + hi + 1) // 2
                        if fitz.get_text_length(
                                word[:mid], fontname=fontname,
                                fontsize=fontsize) <= width_pt:
                            lo = mid
                        else:
                            hi = mid - 1
                    lines.append(word[:lo])
                    word = word[lo:]
                cur = word
        if cur:
            lines.append(cur)
        else:
            lines.append("")  # preserve blank paragraph
    return lines or [""]


def render_addendum_pages(
    doc: fitz.Document,
    form_id: str,
    overflows: Iterable[tuple[str, str]],
    prompts: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Append addendum pages for each overflowed answer. Returns a list
    of (field_name, ref_string) pairs that the caller should write into
    the corresponding inline widget — e.g. "See Addendum Q1".

    overflows: iterable of (field_name, body_text). field_name must
        match the AcroForm widget /T. body_text is the remainder that
        didn't fit (or the full answer — caller decides).
    prompts: optional dict mapping field_name → human-readable prompt
        label for the question header. Falls back to field_name.
    """
    overflows = list(overflows)
    if not overflows:
        return []
    prompts = prompts or {}

    # Use existing page 0 dimensions as the addendum format reference.
    ref_rect = doc[0].rect if len(doc) > 0 else fitz.Rect(0, 0, 612, 792)
    page_w, page_h = ref_rect.width, ref_rect.height

    body_x0 = MARGIN_X + 16
    body_x1 = page_w - MARGIN_X
    body_width = body_x1 - body_x0
    head_x0 = MARGIN_X

    body_top = HEADER_HEIGHT + TOP_MARGIN
    body_bottom = page_h - FOOTER_HEIGHT - BOTTOM_PADDING

    # Pre-build the full ordered list of "items to draw" so we can paginate.
    # Each Q produces: 1 head line, blank gap, K body lines, blank gap.
    items: list[tuple[str, str]] = []  # (kind, text); kind ∈ head|body|gap
    refs: list[tuple[str, str]] = []
    for i, (name, body) in enumerate(overflows, 1):
        label = prompts.get(name, name)
        items.append(("head", f"Q{i}. {label}"))
        for line in _wrap_lines(body, body_width):
            items.append(("body", line))
        items.append(("gap", ""))
        refs.append((name, f"See Addendum Q{i}"))

    # Strip trailing gap.
    while items and items[-1][0] == "gap":
        items.pop()

    # Pre-create addendum pages and lay items out, paginating as needed.
    addendum_pages: list[fitz.Page] = []
    cur_page: fitz.Page | None = None
    cur_y = 0.0

    def _start_new_page() -> fitz.Page:
        nonlocal cur_page, cur_y
        page = doc.new_page(width=page_w, height=page_h)
        # Header band
        band = fitz.Rect(0, 0, page_w, HEADER_HEIGHT)
        page.draw_rect(band, color=(0, 0, 0), fill=(0, 0, 0))
        page.insert_text(
            (MARGIN_X, HEADER_HEIGHT * 0.66),
            f"ADDENDUM — Form {form_id}",
            fontname="hebo", fontsize=14, color=(1, 1, 1),
        )
        addendum_pages.append(page)
        cur_page = page
        cur_y = body_top
        return page

    _start_new_page()
    assert cur_page is not None

    for kind, text in items:
        if kind == "head":
            line_height = HEAD_GAP + 4
            x = head_x0
            fn, fs = HEAD_FONT, HEAD_SIZE
        elif kind == "body":
            line_height = LINE_GAP
            x = body_x0
            fn, fs = BODY_FONT, BODY_SIZE
        else:  # gap
            cur_y += Q_BLOCK_GAP
            continue
        if cur_y + line_height > body_bottom:
            _start_new_page()
        cur_page.insert_text((x, cur_y), text, fontname=fn, fontsize=fs,
                             color=(0, 0, 0))
        cur_y += line_height

    # Now draw footer on every addendum page (need total count).
    total = len(addendum_pages)
    for i, page in enumerate(addendum_pages, 1):
        footer_text = f"Form {form_id} — Addendum page {i} of {total}"
        text_w = fitz.get_text_length(footer_text, fontname=BODY_FONT,
                                       fontsize=10)
        fx = page_w - MARGIN_X - text_w
        fy = page_h - FOOTER_HEIGHT / 2
        page.insert_text((fx, fy), footer_text,
                         fontname=BODY_FONT, fontsize=10, color=(0.3, 0.3, 0.3))
        # Thin separator above footer.
        sep_y = page_h - FOOTER_HEIGHT
        page.draw_line(
            fitz.Point(MARGIN_X, sep_y),
            fitz.Point(page_w - MARGIN_X, sep_y),
            color=(0.7, 0.7, 0.7), width=0.5,
        )

    return refs
