"""Inject a new text-field AcroForm widget into an existing fillable PDF.

Use case: the heuristic field detector misses a label-underline that
should be a fillable widget (e.g. PP-205 Item 5 "Date of birth and
age of the Respondent"). This script adds a new widget at a specified
rect with a specified field name, on a target page.

Idempotent: skips if a widget with the given name already exists on
the target page.

Usage:
  python3 scripts/inject_text_widget.py <pdf> --page 0 \\
      --field-name respondent_dob_age \\
      --rect 72 470 542 485 \\
      [--out <pdf_out>]   # if omitted, overwrites in place
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import fitz


def inject(pdf_path: pathlib.Path, page_idx: int, field_name: str,
           rect: tuple[float, float, float, float],
           out_path: pathlib.Path) -> None:
    doc = fitz.open(pdf_path)
    page = doc[page_idx]

    # Idempotent skip
    for w in page.widgets():
        if w.field_name == field_name:
            print(f"already exists: {field_name} on page {page_idx} "
                  f"(rect={tuple(w.rect)}) — leaving as-is")
            if out_path != pdf_path:
                doc.save(out_path)
            doc.close()
            return

    widget = fitz.Widget()
    widget.field_name = field_name
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(*rect)
    widget.text_font = "Helv"
    widget.text_fontsize = 10
    # text_color is required for the appearance stream to render values.
    widget.text_color = (0, 0, 0)
    widget.border_width = 0
    page.add_widget(widget)

    doc.save(out_path, incremental=(out_path == pdf_path),
             encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    print(f"injected: {field_name} on page {page_idx} "
          f"rect={rect} → {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--field-name", required=True)
    ap.add_argument("--rect", type=float, nargs=4, required=True,
                    metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()
    out = args.out or args.pdf
    inject(args.pdf, args.page, args.field_name,
           tuple(args.rect), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
