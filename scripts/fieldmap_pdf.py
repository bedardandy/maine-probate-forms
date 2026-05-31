#!/usr/bin/env python3
"""Build a single review PDF that stamps every field with its own name.

For each form: fetch the flat source PDF (from metadata.json source_url, cached),
remove any existing form widgets, draw each field's widget rectangle and fill it
with the field_id repeated to fill the box, add a top-right page identifier
(FORM-ID p#/#), rasterize each page (truly flattened), and combine everything
into one PDF for visual review of the field layout.

Usage:
    python3 scripts/fieldmap_pdf.py [--forms DE-101,AF-102] [--dpi 120] \
        [--out fieldmap_review.pdf]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import fitz  # PyMuPDF

REPO = pathlib.Path(__file__).resolve().parent.parent
FORMS_DIR = REPO / "repo" / "forms"
CACHE = pathlib.Path("/tmp/fieldmap_cache")

RED = (0.85, 0, 0)
BOX = (0.85, 0.2, 0.2)


def source_url(form_id: str) -> str | None:
    meta = json.loads((FORMS_DIR / form_id / "metadata.json").read_text())
    return meta.get("source_url") or meta.get("source_pdf")


def fetch(form_id: str) -> pathlib.Path | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = CACHE / f"{form_id}.pdf"
    if dst.exists() and dst.stat().st_size > 800:
        return dst
    url = source_url(form_id)
    if not url:
        return None
    r = subprocess.run(["curl", "-sS", "-m", "60", "-o", str(dst), url],
                       capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 800:
        return None
    # sanity: is it a PDF?
    if dst.read_bytes()[:5] != b"%PDF-":
        dst.unlink(missing_ok=True)
        return None
    return dst


def stamp_form(form_id: str, src: pathlib.Path, out: fitz.Document, dpi: int) -> int:
    geo = json.loads((FORMS_DIR / form_id / "fill_geometry.json").read_text())
    fields = geo.get("fields", {})
    # page -> list of (field_id, rect)
    by_page: dict[int, list[tuple[str, list[float]]]] = {}
    for fid, fdef in fields.items():
        for w in fdef.get("widgets", []):
            by_page.setdefault(int(w.get("page", 0)), []).append((fid, w["rect"]))

    doc = fitz.open(src)
    n = doc.page_count
    pages_added = 0
    for i, page in enumerate(doc):
        # Remove existing form widgets so we render a clean background.
        try:
            for wdg in list(page.widgets() or []):
                page.delete_widget(wdg)
        except Exception:
            pass

        pw, ph = page.rect.width, page.rect.height
        for fid, rect in by_page.get(i, []):
            r = fitz.Rect(*rect) & page.rect  # clip to page
            if r.is_empty or r.width < 2 or r.height < 2:
                continue
            page.draw_rect(r, color=BOX, width=0.5)
            rr = r + (1, 1, -1, -1)
            if rr.is_empty:
                rr = r
            fontsize = max(4.0, min(7.5, r.height - 3))
            # Repeat the name to fill the box. insert_textbox writes NOTHING if
            # the text overflows, so estimate capacity then shrink until it fits.
            cpl = max(1, int(rr.width / (fontsize * 0.5)))
            lines = max(1, int(rr.height / (fontsize * 1.15)))
            reps = max(1, (cpl * lines) // (len(fid) + 2))
            placed = False
            while reps >= 1:
                rc = page.insert_textbox(rr, (fid + "  ") * reps, fontsize=fontsize,
                                         color=RED, fontname="helv", align=0)
                if rc >= 0:
                    placed = True
                    break
                reps //= 2
            if not placed:  # even one copy overflows — shrink font, single label
                page.insert_textbox(rr, fid, fontsize=max(3.0, fontsize - 2.5),
                                    color=RED, fontname="helv", align=0)

        # Top-right page identifier (baked in before raster).
        label = f"{form_id}   p{i + 1}/{n}"
        lw = 9 + len(label) * 5.4
        lab = fitz.Rect(pw - lw - 6, 4, pw - 6, 20)
        page.draw_rect(lab, color=(0.1, 0.1, 0.1), fill=(1, 1, 0.55), width=0.6)
        page.insert_textbox(lab, label, fontsize=8.5, color=(0, 0, 0),
                            fontname="hebo", align=1)

        # Rasterize this page (flatten everything to an image).
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        np = out.new_page(width=pw, height=ph)
        np.insert_image(np.rect, pixmap=pix)
        pages_added += 1
    doc.close()
    return pages_added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", help="comma-separated subset (default: all)")
    ap.add_argument("--dpi", type=int, default=120)
    ap.add_argument("--out", default="fieldmap_review.pdf")
    args = ap.parse_args()

    forms = (args.forms.split(",") if args.forms
             else sorted(d.name for d in FORMS_DIR.iterdir() if d.is_dir()))

    out = fitz.open()
    ok, failed, total_pages = [], [], 0
    for form_id in forms:
        src = fetch(form_id)
        if src is None:
            failed.append(form_id)
            print(f"  ! {form_id}: source fetch failed", file=sys.stderr)
            continue
        try:
            total_pages += stamp_form(form_id, src, out, args.dpi)
            ok.append(form_id)
            print(f"  ✓ {form_id}")
        except Exception as e:
            failed.append(form_id)
            print(f"  ! {form_id}: {e}", file=sys.stderr)

    if out.page_count:
        out.save(args.out, deflate=True, garbage=4)
    size_mb = pathlib.Path(args.out).stat().st_size / 1e6 if out.page_count else 0
    print(f"\nwrote {args.out} — {len(ok)} forms, {total_pages} pages, {size_mb:.1f} MB")
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
