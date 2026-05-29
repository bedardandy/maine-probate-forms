"""One-shot rect-fix for currency-typed AcroForm widgets.

Problem (confirmed across 14/47 forms with currency widgets, 91 widgets total):

  Currency widget rect is positioned with x0 at the left edge of the "$ "
  glyph (typically x=360.0) and extends to x=499.0. The glyph itself
  occupies x=[360.0, 367.5], so when the user starts typing, their
  cursor overlays the leading dollar sign. The widget is also ~23pt
  tall while the surrounding form text is 10pt, so the typed value
  floats high above the printed underline.

Fix per affected widget:

  1. Shift the left edge past the "$" glyph (new x0 = glyph.x1 + 1pt).
  2. Trim the height so the typed text sits just above the underline.
     Strategy: find the horizontal underline rect/line whose y is
     close to the existing widget.y1, and set new_height = 12pt with
     y1 fixed at underline (so y0 = underline_y - 12).

Detection heuristic:
  - Field name (lowercased) contains any of CURRENCY_KEYWORDS.
  - On the same page, there exists a "$" glyph whose bbox vertically
    overlaps the widget rect.

Usage:
    # Dry-run: report what would change, write nothing.
    python3 scripts/fix_currency_rects.py --dry-run

    # Apply in place (over output_fused/**/*_fused.pdf).
    python3 scripts/fix_currency_rects.py --apply

    # Single form:
    python3 scripts/fix_currency_rects.py --apply \
        --pdf 'output_fused/estates/DE-406 Probate Account (Rev. 7-1-19)_fused.pdf'

Writes a TSV report at scripts/fix_currency_rects.tsv summarising every
adjusted widget so the change is auditable.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass

import fitz


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_GLOB = "output_fused/**/*_fused.pdf"
REPORT_PATH = REPO_ROOT / "scripts" / "fix_currency_rects.tsv"

CURRENCY_KEYWORDS = (
    "amount", "total", "value", "balance", "expense", "income",
    "receipt", "disburs", "asset", "liab", "principal", "interest",
    "dist", "gain", "loss", "remain", "sum", "cost", "fee", "tax",
    "compensation", "rent", "debt", "credit", "cash",
)

# How far past the right edge of the "$ " glyph to start the widget.
# 1pt was too tight: at 200 DPI render that's only ~2.8px, and vision
# audit read printed-$ + typed-value as a fused "$N,NNN.NN" token.
# 2.5pt = ~7px gap is unambiguously a space.
GLYPH_RIGHT_PAD = 2.5
# Target widget height (close to the glyph font size, ~10pt) so typed
# text sits flush above the underline.
TARGET_HEIGHT = 12.0


@dataclass
class GlyphHit:
    x0: float
    x1: float
    y0: float
    y1: float


@dataclass
class UnderlineHit:
    y: float
    x0: float
    x1: float


def _is_currency_name(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in CURRENCY_KEYWORDS)


def _collect_glyphs(page: fitz.Page) -> list[GlyphHit]:
    """Use page.search_for to get per-character bboxes of '$', because
    span bboxes can span an entire long-form line (e.g. "$_____ dollars,
    to be paid …") and we'd get a 470pt-wide bbox that matches every
    widget on the line."""
    out: list[GlyphHit] = []
    for rect in page.search_for("$"):
        out.append(GlyphHit(
            x0=rect.x0, x1=rect.x1, y0=rect.y0, y1=rect.y1))
    return out


def _collect_underlines(page: fitz.Page) -> list[UnderlineHit]:
    out: list[UnderlineHit] = []
    for d in page.get_drawings():
        for item in d.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 0.5:
                    out.append(UnderlineHit(
                        y=p1.y,
                        x0=min(p1.x, p2.x), x1=max(p1.x, p2.x)))
            elif item[0] == "re":
                rect = item[1]
                if rect.height < 2.0:
                    out.append(UnderlineHit(
                        y=(rect.y0 + rect.y1) / 2,
                        x0=rect.x0, x1=rect.x1))
    return out


def _find_overlapping_glyph(widget_rect: fitz.Rect,
                            glyphs: list[GlyphHit]) -> GlyphHit | None:
    """A glyph 'overlaps' if its bbox horizontally intersects the widget's
    left edge AND vertically overlaps the widget rect."""
    for g in glyphs:
        # Widget left edge falls inside the glyph horizontal range.
        if not (g.x0 - 1.0 <= widget_rect.x0 <= g.x1 + 1.0):
            continue
        # Vertical overlap.
        if g.y1 < widget_rect.y0 or g.y0 > widget_rect.y1:
            continue
        return g
    return None


def _find_underline_y(widget_rect: fitz.Rect,
                      underlines: list[UnderlineHit]) -> float | None:
    """Pick the horizontal line closest to (but within 3pt of) the widget's
    bottom edge."""
    candidates = []
    for u in underlines:
        if abs(u.y - widget_rect.y1) <= 3.0:
            # Underline horizontal extent must overlap the widget.
            if u.x1 < widget_rect.x0 or u.x0 > widget_rect.x1:
                continue
            candidates.append(u)
    if not candidates:
        return None
    candidates.sort(key=lambda u: abs(u.y - widget_rect.y1))
    return candidates[0].y


# Width below which a currency widget is unusable for a $ amount —
# these are presumed already-broken from an earlier buggy run and get
# restored to a default-width text box instead of further shrunk.
MIN_USABLE_WIDTH = 40.0
DEFAULT_RESTORE_WIDTH = 120.0


def _process_pdf(pdf_path: pathlib.Path, *, apply: bool) -> list[dict]:
    rows: list[dict] = []
    doc = fitz.open(pdf_path)
    changed = False
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        glyphs = _collect_glyphs(page)
        underlines = _collect_underlines(page)
        widgets = list(page.widgets() or [])
        for w in widgets:
            name = w.field_name or ""
            if not _is_currency_name(name):
                continue
            # Skip non-text widgets (checkboxes etc. occasionally have
            # currency-flavored names like 'findings_augmented_estate_value').
            if w.field_type != fitz.PDF_WIDGET_TYPE_TEXT:
                continue
            wr = fitz.Rect(w.rect)

            # Rollback path: widget left over from a buggy previous run
            # (current width < usable threshold). Restore to a default
            # width so it's at least fillable; the position will follow
            # the same glyph-anchored rule below if a glyph is found.
            if wr.width < MIN_USABLE_WIDTH:
                glyph = _find_overlapping_glyph(
                    fitz.Rect(wr.x1 - DEFAULT_RESTORE_WIDTH, wr.y0,
                              wr.x1, wr.y1), glyphs)
                if glyph is not None:
                    new_x0 = glyph.x1 + GLYPH_RIGHT_PAD
                else:
                    new_x0 = wr.x1 - DEFAULT_RESTORE_WIDTH
                ul_y = _find_underline_y(wr, underlines)
                new_y1 = ul_y if ul_y is not None else wr.y1
                new_y0 = new_y1 - TARGET_HEIGHT
                new_rect = fitz.Rect(new_x0, new_y0, wr.x1, new_y1)
                rows.append({
                    "pdf": str(pdf_path.relative_to(REPO_ROOT)),
                    "page": page_idx, "field": name,
                    "old_rect": f"[{wr.x0:.1f},{wr.y0:.1f},"
                                f"{wr.x1:.1f},{wr.y1:.1f}]",
                    "new_rect": f"[{new_rect.x0:.1f},{new_rect.y0:.1f},"
                                f"{new_rect.x1:.1f},{new_rect.y1:.1f}]",
                    "underline_y": "rollback",
                })
                if apply:
                    w.rect = new_rect
                    w.update()
                    changed = True
                continue

            # Skip widgets that already look post-fix: short height AND
            # x0 sits at approximately the post-fix anchor position
            # (glyph.x1 + GLYPH_RIGHT_PAD ± 0.5pt).
            if wr.height <= TARGET_HEIGHT + 1.0:
                near = any(
                    abs(wr.x0 - (g.x1 + GLYPH_RIGHT_PAD)) < 0.5
                    and g.y0 <= wr.y1 and g.y1 >= wr.y0
                    for g in glyphs)
                if near:
                    continue

            glyph = _find_overlapping_glyph(wr, glyphs)
            if glyph is None:
                continue
            new_x0 = glyph.x1 + GLYPH_RIGHT_PAD
            ul_y = _find_underline_y(wr, underlines)
            new_y1 = ul_y if ul_y is not None else wr.y1
            new_y0 = new_y1 - TARGET_HEIGHT
            new_rect = fitz.Rect(new_x0, new_y0, wr.x1, new_y1)
            rows.append({
                "pdf": str(pdf_path.relative_to(REPO_ROOT)),
                "page": page_idx,
                "field": name,
                "old_rect": f"[{wr.x0:.1f},{wr.y0:.1f},{wr.x1:.1f},{wr.y1:.1f}]",
                "new_rect": f"[{new_rect.x0:.1f},{new_rect.y0:.1f},"
                            f"{new_rect.x1:.1f},{new_rect.y1:.1f}]",
                "underline_y": f"{ul_y:.1f}" if ul_y is not None else "n/a",
            })
            if apply:
                w.rect = new_rect
                w.update()
                changed = True
    if apply and changed:
        doc.saveIncr()
    doc.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Write changes back (incremental save).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Default: report what would change.")
    ap.add_argument("--pdf", type=pathlib.Path,
                    help="Single PDF instead of the full glob.")
    ap.add_argument("--glob", type=str, default=DEFAULT_GLOB,
                    help="Override default glob (relative to repo root).")
    ap.add_argument("--report", type=pathlib.Path, default=REPORT_PATH)
    args = ap.parse_args()

    if not args.apply:
        args.dry_run = True

    if args.pdf:
        targets = [args.pdf if args.pdf.is_absolute() else REPO_ROOT / args.pdf]
    else:
        targets = sorted((REPO_ROOT).glob(args.glob))

    print(f"{'[apply]' if args.apply else '[dry-run]'} "
          f"scanning {len(targets)} pdf(s)")
    all_rows: list[dict] = []
    for p in targets:
        try:
            rows = _process_pdf(p, apply=args.apply)
        except Exception as e:
            print(f"  ! {p.name}: {e}", file=sys.stderr)
            continue
        if rows:
            print(f"  {p.relative_to(REPO_ROOT)}  ({len(rows)} fixed)")
            all_rows.extend(rows)

    if all_rows:
        cols = ["pdf", "page", "field", "old_rect", "new_rect", "underline_y"]
        args.report.write_text(
            "\t".join(cols) + "\n"
            + "\n".join("\t".join(str(r[c]) for c in cols) for r in all_rows)
            + "\n")
        print(f"\nReport: {args.report.relative_to(REPO_ROOT)} "
              f"({len(all_rows)} widget adjustments)")
    else:
        print("\nNo widgets needed adjustment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
