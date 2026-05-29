"""Inspect the two 'GAL's contact information' underline rows on PB-007.

For each text span containing underscores near that label, dump:
  - span bbox (raw)
  - text + per-char x positions reconstructed by find_text_chars
  - where _extract_text_underscore_lines thinks the underscore run starts/ends
  - where the underscore run REALLY starts/ends (per-char x from PyMuPDF)

This isolates whether the proportional cw estimate is biased on spans with
leading whitespace.
"""
from __future__ import annotations

import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PDF = ROOT / "output_fused/guardian_minor/PB-007 GAL Joint Appt. Order 3.4.20_fused.pdf"


def main() -> int:
    d = fitz.open(PDF)
    for pno in range(d.page_count):
        page = d[pno]
        td = page.get_text("rawdict")
        for block in td["blocks"]:
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    chars = span.get("chars") or []
                    text = "".join(c["c"] for c in chars)
                    if "_" not in text:
                        continue
                    # Only show spans on/around the GAL contact rows.
                    bbox = span["bbox"]
                    # Reconstruct per-char x left edges:
                    char_xs = [(c["c"], c["bbox"][0], c["bbox"][2]) for c in chars]
                    # Find where the underscore run starts/ends
                    first_us = next((i for i, c in enumerate(text) if c == "_"), None)
                    last_us = max((i for i, c in enumerate(text) if c == "_"), default=None)
                    if first_us is None:
                        continue
                    # Proportional estimate (what our snap code does today):
                    n = len(text)
                    cw = (bbox[2] - bbox[0]) / n if n else 0
                    est_first_x = bbox[0] + first_us * cw
                    est_last_x = bbox[0] + (last_us + 1) * cw
                    # Truth from per-char bboxes:
                    true_first_x = char_xs[first_us][1]
                    true_last_x = char_xs[last_us][2]
                    # Filter to lines around y=560..640 (where GAL contact is)
                    # but show all underscore-bearing spans on first 4 pages
                    # so we can find the exact ones.
                    print(f"p{pno} y={bbox[1]:.1f}..{bbox[3]:.1f}")
                    print(f"   span x={bbox[0]:.1f}..{bbox[2]:.1f}  text[:60]={text[:60]!r}")
                    print(f"   leading-ws chars: {len(text) - len(text.lstrip())}, "
                          f"first '_' at index {first_us}, last at {last_us}, len={n}")
                    print(f"   proportional est:  {est_first_x:.1f} .. {est_last_x:.1f}")
                    print(f"   true (per-char):   {true_first_x:.1f} .. {true_last_x:.1f}")
                    dx0 = est_first_x - true_first_x
                    dx1 = est_last_x - true_last_x
                    print(f"   error:             dx0={dx0:+.1f}  dx1={dx1:+.1f}")
                    print()
        # Only first 2 pages
        if pno >= 1:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
