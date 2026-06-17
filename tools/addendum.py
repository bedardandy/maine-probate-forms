#!/usr/bin/env python3
"""Overflow -> addendum continuation pages for probate fills.

Maine probate forms give a fixed, small space for answers that can be lists of
arbitrary length: heirs and devisees, pledged property, creditors, persons
served. When such a value will not fit its box, cramming it either clips the
text or shrinks it to an unreadable size. Instead this module:

  1. writes a short reference in the form field --
       "See attached Addendum N for <subject>."
     (for a fixed-row table, the visible rows are filled and the reference goes
     in a full-width final row, centred), and
  2. appends an addendum PAGE per overflowed field at the end of the document:
       * a heading = the field's printed question + " (continued)",
       * the full content, one item per line, wrapped to the margins,
       * a footer page number that continues the form's own page sequence
         ("Page N of TOTAL").

One field is one addendum, spilling onto as many pages as it needs (each still
titled "(continued)") so a reader can follow it. The engine is deterministic:
the addendum shows the same value the fill plan already produced -- it never
invents content. Not legal advice; verify the assembled packet before filing.

Used by tools/fill_pdf.py (overflow=True). Self-test:
    python3 tools/addendum.py --selftest
"""
from __future__ import annotations

import re

import fitz

_PAGENUM_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)

# Page layout for an addendum sheet (US Letter, 1" margins).
MARGIN = 72.0
TITLE_FS = 13.0
LABEL_FS = 9.0
BODY_FS = 11.0
FOOT_FS = 9.0
LINE_H = 1.34            # line pitch as a multiple of the font size
ITEM_GAP = 0.5          # blank-line fraction between list items
INDENT = 16.0           # hanging indent for item wrap-lines
RULE_GAP = 12.0


def _w(text: str, fs: float, font: str = "helv") -> float:
    try:
        return fitz.get_text_length(text, fontname=font, fontsize=fs)
    except Exception:
        return len(text) * fs * 0.5


def wrap(text: str, width: float, fs: float, font: str = "helv") -> list[str]:
    """Greedy word-wrap `text` to `width` points at font size `fs`."""
    out: list[str] = []
    for para in str(text).replace("\r", "").split("\n"):
        words = para.split()
        if not words:
            out.append("")
            continue
        cur = ""
        for word in words:
            cand = (cur + " " + word).strip()
            if cur and _w(cand, fs, font) > width:
                out.append(cur)
                cur = word
            else:
                cur = cand
        out.append(cur)
    return out or [""]


def fits(value: str, rect, fs: float, pad: float = 2.0) -> bool:
    """True if `value` wraps within the widget rect at font size `fs`."""
    x0, y0, x1, y1 = rect
    lines = wrap(value, max(1.0, (x1 - x0) - 2 * pad), fs)
    return len(lines) * fs * LINE_H <= (y1 - y0) + 0.5


def field_reference(subject: str, no: int) -> str:
    """The text written into an overflowed single field."""
    return f"See attached Addendum {no} for {subject}."


def table_overflow_row(subject: str, no: int) -> str:
    """The centred text for a table's full-width final (overflow) row."""
    return f"See attached Addendum {no} for remaining {subject}."


def render_table(page: fitz.Page, spec: dict, rows: list,
                 overflow_no: int | None = None) -> list:
    """Draw a list of `rows` into a column table on `page` per `spec`.

    `spec` = {columns:[{label,x:[x0,x1]}], row_top, row_h, rows, subject}. Rows
    are dicts keyed by column label. When there are more rows than fit and
    `overflow_no` is given, the last visible row is replaced by a full-width
    centred "See attached Addendum N for remaining <subject>." and the unshown
    rows are returned for the caller to place on that addendum. Returns the
    remainder list (empty if everything fit)."""
    cols, rt, rh, nr = spec["columns"], spec["row_top"], spec["row_h"], spec["rows"]
    fs = spec.get("font_size", 9.0)
    spill = overflow_no is not None and len(rows) > nr
    cap = nr - 1 if spill else min(nr, len(rows))
    for i in range(cap):
        y = rt + i * rh
        for c in cols:
            val = rows[i].get(c["label"], "") if isinstance(rows[i], dict) else ""
            if val:
                page.insert_text((c["x"][0] + 3, y + rh - 5), str(val),
                                 fontsize=fs, fontname="helv")
    if spill:
        y = rt + cap * rh
        x0, x1 = cols[0]["x"][0], cols[-1]["x"][1]
        txt = table_overflow_row(spec.get("subject", "items"), overflow_no)
        page.insert_text(((x0 + x1 - _w(txt, fs + 0.5)) / 2, y + rh - 5), txt,
                         fontsize=fs + 0.5, fontname="helv", color=(0, 0, 0))
        return rows[cap:]
    return []


def make_entry(field_id: str, question: str, subject: str,
               content) -> dict:
    """Build an addendum entry. `content` is a list (-> numbered items) or a
    string (-> a wrapped paragraph). `question` titles the page; `subject` is
    used in the in-field reference."""
    items = None
    text = None
    if isinstance(content, (list, tuple)):
        items = [str(c).strip() for c in content if str(c).strip()]
    else:
        text = str(content)
    return {"field_id": field_id, "question": question.strip(),
            "subject": subject.strip(), "items": items, "text": text}


def _entry_lines(entry: dict, width: float) -> list[str]:
    """Render an entry to display lines (numbered for lists, wrapped for prose).

    A leading "" before a wrap-continuation is meaningless; a trailing "" marks
    the inter-item gap. Items keep a hanging indent so continuation lines align
    under the text, not the number."""
    if entry.get("items"):
        lines: list[str] = []
        n = len(entry["items"])
        numw = _w(f"{n}. ", BODY_FS)
        for i, item in enumerate(entry["items"], 1):
            wrapped = wrap(item, width - numw, BODY_FS)
            lines.append((f"{i}. {wrapped[0]}", 0.0))
            lines.extend((w, INDENT) for w in wrapped[1:])
            lines.append(("", 0.0))                 # gap between items
        return lines
    return [(w, 0.0) for w in wrap(entry.get("text") or "", width, BODY_FS)]


def _paginate(lines: list, body_h: float) -> list[list]:
    per = max(1, int(body_h // (BODY_FS * LINE_H)))
    return [lines[i:i + per] for i in range(0, len(lines), per)] or [[]]


def _layout(entries: list[dict], page_w: float, page_h: float) -> list[dict]:
    """Compute the per-page line chunks for every entry (header/footer reserved)."""
    width = page_w - 2 * MARGIN
    header_h = TITLE_FS * LINE_H * 2 + RULE_GAP + 24    # label + (wrapped) title + rule
    body_h = page_h - 2 * MARGIN - header_h - FOOT_FS * 2
    plan = []
    for entry in entries:
        chunks = _paginate(_entry_lines(entry, width), body_h)
        plan.append({"entry": entry, "chunks": chunks})
    return plan


def detect_page_scheme(doc: fitz.Document, base_pages: int) -> dict | None:
    """Find the form's printed 'Page N of M' tokens across the base pages.

    Maine forms number themselves 'Page N of M' in a fixed spot (top-right at
    8pt for most, bottom-centre for a few). To continue that scheme we capture
    each token's bbox + size so addendum pages can match position/style and the
    base 'of M' can be rewritten to the new total. Returns None if the form
    carries no such token."""
    spans = {}
    for pi in range(min(base_pages, doc.page_count)):
        for blk in doc[pi].get_text("dict")["blocks"]:
            for ln in blk.get("lines", []):
                for sp in ln["spans"]:
                    if _PAGENUM_RE.search(sp["text"]):
                        spans[pi] = (*sp["bbox"], round(sp["size"], 1))
        # right-edge x of the token, used to right-align the rewrite
    if not spans:
        return None
    sample = next(iter(spans.values()))
    return {"spans": spans, "right": sample[2], "fs": sample[4]}


def _renumber_base(doc: fitz.Document, scheme: dict, total: int) -> None:
    """Rewrite each base page's 'Page N of M' to 'Page N of {total}' in place,
    so the whole packet (form + addenda) reads consistently.

    Forms stack the header lines (id / revision / page number) so tightly that
    they overlap vertically; apply_redactions() would erase part of the line
    above. So paint an opaque white box over just the token bbox (inset at the
    top to spare the overlapping line's descenders) and draw the new text on
    top -- exact, with no collateral damage."""
    for pi, (x0, y0, x1, y1, fs) in scheme["spans"].items():
        pg = doc[pi]
        pg.draw_rect(fitz.Rect(x0 - 0.5, y0 + 0.5, x1 + 1.0, y1 + 0.8),
                     color=(1, 1, 1), fill=(1, 1, 1), width=0)
        txt = f"Page {pi + 1} of {total}"
        pg.insert_text((x1 - _w(txt, fs), y1 - 0.18 * fs), txt,
                       fontsize=fs, fontname="helv", color=(0, 0, 0))


def _draw_page(doc: fitz.Document, entry: dict, lines: list, *,
               no: int, page_no: int, total: int, form_id: str,
               part, page_w: float, page_h: float,
               scheme: dict | None) -> None:
    pg = doc.new_page(width=page_w, height=page_h)
    x0, x1 = MARGIN, page_w - MARGIN
    grey = (0.32, 0.32, 0.32)

    y = MARGIN
    pg.insert_text((x0, y), f"{form_id}  —  Addendum {no}",
                   fontsize=LABEL_FS, fontname="helv", color=grey)
    y += LABEL_FS * LINE_H + 6

    title = entry["question"].rstrip(" :") + " (continued)"
    if part[1] > 1:
        title += f"  [sheet {part[0] + 1} of {part[1]}]"
    for tl in wrap(title, x1 - x0, TITLE_FS, "hebo"):
        pg.insert_text((x0, y + TITLE_FS), tl, fontsize=TITLE_FS,
                       fontname="hebo")
        y += TITLE_FS * LINE_H
    y += 4
    pg.draw_line((x0, y), (x1, y), color=(0.6, 0.6, 0.6), width=0.8)
    y += RULE_GAP

    for text, indent in lines:
        if text:
            pg.insert_text((x0 + indent, y + BODY_FS), text,
                           fontsize=BODY_FS, fontname="helv")
        y += BODY_FS * LINE_H

    # Continue the form's own numbering: match its token spot if it has one,
    # else fall back to a centred footer.
    foot = f"Page {page_no} of {total}"
    if scheme:
        fx0, fy0, fx1, fy1, ffs = next(iter(scheme["spans"].values()))
        pg.insert_text((scheme["right"] - _w(foot, ffs), fy1 - 0.18 * ffs),
                       foot, fontsize=ffs, fontname="helv", color=(0, 0, 0))
    else:
        pg.insert_text(((page_w - _w(foot, FOOT_FS)) / 2,
                        page_h - MARGIN + 30), foot,
                       fontsize=FOOT_FS, fontname="helv", color=grey)


def append_pages(doc: fitz.Document, entries: list[dict], form_id: str,
                 base_pages: int | None = None,
                 page_size=None, renumber_base: bool = True) -> dict:
    """Append addendum pages for `entries`; return numbering metadata.

    `base_pages` is the form's own page count (defaults to the document's
    current count -- capture it BEFORE adding pages). Addendum pages continue
    from base_pages + 1 and read "Page N of TOTAL", matching the form's own
    'Page N of M' spot/style when it has one. With `renumber_base`, the form's
    base pages are rewritten to the new TOTAL so the packet is consistent."""
    if not entries:
        return {"addendum_pages": 0, "entries": 0}
    base = doc.page_count if base_pages is None else base_pages
    if page_size is None:
        ref = doc[0].rect if doc.page_count else fitz.Rect(0, 0, 612, 792)
        page_w, page_h = ref.width, ref.height
    else:
        page_w, page_h = page_size

    scheme = detect_page_scheme(doc, base)
    plan = _layout(entries, page_w, page_h)
    total = base + sum(len(p["chunks"]) for p in plan)
    if scheme and renumber_base:
        _renumber_base(doc, scheme, total)
    page_no = base
    out = []
    for no, p in enumerate(plan, 1):
        sheets = len(p["chunks"])
        for si, chunk in enumerate(p["chunks"]):
            page_no += 1
            _draw_page(doc, p["entry"], chunk, no=no, page_no=page_no,
                       total=total, form_id=form_id, part=(si, sheets),
                       page_w=page_w, page_h=page_h, scheme=scheme)
        out.append({"addendum_no": no, "field_id": p["entry"]["field_id"],
                    "sheets": sheets})
    return {"addendum_pages": total - base, "total_pages": total,
            "base_pages": base, "numbering": "form-scheme" if scheme else "footer",
            "entries": out}


def _selftest() -> int:
    doc = fitz.open()
    doc.new_page(width=612, height=792)            # one "form" page
    doc.new_page(width=612, height=792)
    recipients = [f"Person Number {i}, {i*3} Example Street, Portland, ME 04101"
                  for i in range(1, 41)]
    entries = [
        make_entry("service_recipients",
                   "Names and mailing addresses of all persons served",
                   "the persons served", recipients),
        make_entry("pledged_property_description",
                   "Description of Pledged Personal Property",
                   "the description of pledged personal property",
                   "A 1998 Ford F-150 pickup, a 2019 Bayliner VR5 boat with "
                   "trailer, a John Deere 1025R tractor, and assorted household "
                   "furnishings. " * 20),
    ]
    meta = append_pages(doc, entries, "MISC-101", base_pages=2)
    out = "/tmp/addendum_selftest.pdf"
    doc.save(out)
    assert fits("Portland, ME", [0, 0, 120, 14], 10.0)
    assert not fits("x " * 200, [0, 0, 120, 14], 10.0)
    assert field_reference("the persons served", 3) == \
        "See attached Addendum 3 for the persons served."
    print(f"selftest ok -> {meta}; wrote {out}")
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
