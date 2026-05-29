"""Flatten a fused PDF into a structured digest the tree-writer LLM can reason over.

The digest is a per-page sequence of tokens in reading order:
  * TEXT(content)            — text spans from the source PDF
  * WIDGET(id, type, name)   — fillable widget candidates, tagged with a
                               short stable ID (W001, W002, ...) the LLM
                               can cite in the resulting tree.

Reading order is approximated by sorting everything by (page, line-y, x).
Spans on the same line are joined with single spaces. Widgets are inserted
in line position, so a row like
  [W005] Limited-Purpose [W006] Standard [W007] Expanded
preserves the visual structure the form authors intended.

Output formats:
  --format text   — plain text suitable for a chat prompt (default)
  --format json   — machine-readable {pages: [{page, items: [...]}]}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, asdict
from typing import Literal

import fitz


@dataclass
class Item:
    kind: Literal["text", "widget"]
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    # text only
    text: str = ""
    # widget only
    widget_id: str = ""
    widget_type: str = ""
    widget_name: str = ""
    widget_xref: int = 0


def _line_key(it: Item, line_tol: float) -> tuple[int, int]:
    """Key that buckets items by horizontal line band."""
    return (it.page, int(it.y0 / line_tol))


# ─── Layout analysis ────────────────────────────────────────────────────────
# We split each page into vertical "bands" (groups of items separated by
# meaningful vertical whitespace) and then test each band for a consistent
# columnar split. Most form pages are single-column, but headers and the
# occasional mid-form sub-region (sig blocks, "petitioner / respondent"
# alternates) carry parallel columns whose left-to-right interleaving
# confuses an LLM that only sees flat reading order.

BAND_VGAP_PT = 12.0      # vertical gap that closes a band
COLUMN_MIN_GUTTER_PT = 25.0  # minimum vertical empty strip to count as a column gutter


def _split_into_bands(page_items: list[Item]) -> list[list[Item]]:
    """Group items into vertical bands separated by empty rows."""
    items = sorted(page_items, key=lambda it: (it.y0, it.x0))
    bands: list[list[Item]] = []
    cur: list[Item] = []
    cur_y_max = float("-inf")
    for it in items:
        if cur and it.y0 - cur_y_max > BAND_VGAP_PT:
            bands.append(cur)
            cur = []
            cur_y_max = float("-inf")
        cur.append(it)
        cur_y_max = max(cur_y_max, it.y1)
    if cur:
        bands.append(cur)
    return bands


def _detect_columns(band: list[Item]) -> list[list[Item]]:
    """If a band has a consistent gutter, return [left, right]; else [band].

    A "consistent gutter" is a vertical x-range that every line in the band
    leaves empty. We look at every adjacent x-gap on every line, find the
    median gap center, and require:
      * the gap center falls in the middle 60% of the band's x-extent
      * the gap is wider than COLUMN_MIN_GUTTER_PT
      * no item straddles the gap center
    """
    if len(band) < 2:
        return [band]

    # Bucket items into rows by y-band.
    rows: dict[int, list[Item]] = {}
    for it in band:
        rows.setdefault(int(it.y0 / 4.0), []).append(it)

    # Collect every adjacent gap on every row.
    gap_centers: list[float] = []
    for row in rows.values():
        row_sorted = sorted(row, key=lambda it: it.x0)
        for a, b in zip(row_sorted, row_sorted[1:]):
            gap = b.x0 - a.x1
            if gap >= COLUMN_MIN_GUTTER_PT:
                gap_centers.append((a.x1 + b.x0) / 2.0)
    if not gap_centers:
        return [band]

    gap_centers.sort()
    center = gap_centers[len(gap_centers) // 2]

    # Reject if any item straddles the candidate gutter.
    for it in band:
        if it.x0 < center - 1 and it.x1 > center + 1:
            return [band]

    # Must be in the middle of the band.
    band_x0 = min(it.x0 for it in band)
    band_x1 = max(it.x1 for it in band)
    rel = (center - band_x0) / max(band_x1 - band_x0, 1.0)
    if rel < 0.2 or rel > 0.8:
        return [band]

    left = [it for it in band if it.x1 <= center]
    right = [it for it in band if it.x0 >= center]
    if not left or not right:
        return [band]
    return [left, right]


def analyze_layout(items: list[Item]) -> list[tuple[int, list[list[list[Item]]]]]:
    """For each page, return a list of bands; each band is a list of columns;
    each column is a list of items in reading order.
    """
    by_page: dict[int, list[Item]] = {}
    for it in items:
        by_page.setdefault(it.page, []).append(it)

    layout: list[tuple[int, list[list[list[Item]]]]] = []
    for pno in sorted(by_page):
        bands = _split_into_bands(by_page[pno])
        page_bands: list[list[list[Item]]] = []
        for band in bands:
            cols = _detect_columns(band)
            cols_sorted = [
                sorted(col, key=lambda it: (int(it.y0 / 4.0), it.x0))
                for col in cols
            ]
            page_bands.append(cols_sorted)
        layout.append((pno, page_bands))
    return layout


def extract_items(doc: fitz.Document, *,
                  include_pushbuttons: bool = False,
                  include_widget_names: bool = False) -> list[Item]:
    items: list[Item] = []
    for pno in range(doc.page_count):
        page = doc[pno]
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = span.get("text", "").strip()
                    if not txt:
                        continue
                    bbox = span["bbox"]
                    items.append(Item(
                        kind="text", page=pno,
                        x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3],
                        text=txt,
                    ))
        for w in (page.widgets() or []):
            # Skip the "clr" pushbuttons we inject — they aren't user-fillable.
            # field_type_string is "Button" for pushbuttons; checkbox is "CheckBox".
            if not include_pushbuttons and w.field_type_string == "Button":
                continue
            r = w.rect
            items.append(Item(
                kind="widget", page=pno,
                x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1,
                widget_type=w.field_type_string,
                widget_name=(w.field_name or "") if include_widget_names else "",
                widget_xref=w.xref,
            ))
    return items


def assign_widget_ids(items: list[Item]) -> None:
    """Assign W001, W002, ... in reading order so the LLM can cite stable IDs."""
    n = 1
    items_sorted = sort_reading_order(items, line_tol=4.0)
    for it in items_sorted:
        if it.kind == "widget":
            it.widget_id = f"W{n:03d}"
            n += 1


def sort_reading_order(items: list[Item], line_tol: float = 4.0) -> list[Item]:
    """Sort items into reading order (page → line → x)."""
    return sorted(items, key=lambda it: (it.page, _line_key(it, line_tol), it.x0))


def _format_item(it: Item) -> str:
    if it.kind == "text":
        return it.text
    wtype = "CHK" if it.widget_type == "CheckBox" else (
        "RAD" if it.widget_type == "RadioButton" else (
            "TXT" if it.widget_type == "Text" else "BTN"))
    label = (f"[{it.widget_id} {wtype} "
             f"@p{it.page + 1} "
             f"x={it.x0:.0f} y={it.y0:.0f}")
    if it.widget_name:
        label += f" name={it.widget_name}"
    label += "]"
    return label


def _render_column(items: list[Item], indent: str = "  ") -> list[str]:
    """Render a column's items as a sequence of indented lines."""
    out: list[str] = []
    cur_line_key: tuple[int, int] | None = None
    buf: list[str] = []
    for it in items:
        line_key = _line_key(it, 4.0)
        if cur_line_key is not None and line_key != cur_line_key and buf:
            out.append(indent + " ".join(buf).rstrip())
            buf = []
        cur_line_key = line_key
        buf.append(_format_item(it))
    if buf:
        out.append(indent + " ".join(buf).rstrip())
    return out


def render_text(items: list[Item], line_tol: float = 4.0) -> str:
    """Produce a layout-aware text digest with band/column markers.

    For each page, items are split into vertical bands. A band that splits
    into 2+ columns is rendered with explicit COL-A / COL-B markers so the
    LLM sees parallel structure (e.g. probate-court vs district-court
    captions) instead of left-right interleaving.
    """
    layout = analyze_layout(items)
    out: list[str] = []
    for pno, bands in layout:
        out.append(f"\n=== PAGE {pno + 1} ===\n")
        for band_idx, cols in enumerate(bands, start=1):
            if len(cols) == 1:
                out.extend(_render_column(cols[0], indent=""))
            else:
                out.append(f"[BAND {band_idx} — {len(cols)} columns]")
                col_letters = "ABCDEFGH"
                for ci, col in enumerate(cols):
                    out.append(f"  COL-{col_letters[ci]}:")
                    out.extend(_render_column(col, indent="    "))
                out.append("[/BAND]")
            out.append("")  # blank line between bands
    return "\n".join(out).strip() + "\n"


def render_json(items: list[Item]) -> str:
    items = sort_reading_order(items, line_tol=4.0)
    pages: dict[int, list[dict]] = {}
    for it in items:
        pages.setdefault(it.page, []).append(asdict(it))
    payload = {
        "pages": [
            {"page": p, "items": pages[p]} for p in sorted(pages)
        ],
    }
    return json.dumps(payload, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--include-pushbuttons", action="store_true",
                    help="Include /Btn pushbuttons (default: filter out clr/reset buttons)")
    ap.add_argument("--include-widget-names", action="store_true",
                    help="Include existing widget names (default: omit so LLM reasons "
                         "from text+position only)")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr)
        return 2

    doc = fitz.open(args.pdf)
    items = extract_items(
        doc,
        include_pushbuttons=args.include_pushbuttons,
        include_widget_names=args.include_widget_names,
    )
    doc.close()
    assign_widget_ids(items)

    if args.format == "text":
        out = render_text(items)
    else:
        out = render_json(items)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out)
        print(f"wrote {args.out} ({len(out)} bytes)")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
