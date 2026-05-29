"""Apply Patch E to every widget in a fillable PDF, save a snapped copy.

Use this when you've already written an AcroForm via the standard pipeline
but want widget rects realigned to the visible underline/box anchors —
without going through the full recursive_improvement loop. Useful for
quick verification of writer changes (e.g. radio groups) on top of the
geometric snap.

Usage:
  scripts/snap_only.py <input.pdf> [--out <output.pdf>]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.geometric_snap import snap_widget_rect  # noqa: E402


def snap_pdf(in_path: pathlib.Path, out_path: pathlib.Path) -> tuple[int, int]:
    snapped = 0
    total = 0
    d = fitz.open(in_path)
    for pno in range(d.page_count):
        page = d[pno]
        for w in (page.widgets() or []):
            total += 1
            new_rect = snap_widget_rect(
                in_path, pno, w.field_name,
                widget_rect=w.rect, widget_type=w.field_type,
            )
            if new_rect is None:
                continue
            try:
                w.rect = fitz.Rect(*new_rect)
                w.update()
                snapped += 1
            except Exception:
                pass
    out_path.parent.mkdir(parents=True, exist_ok=True)
    d.save(out_path, deflate=True)
    d.close()
    return snapped, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()

    in_path = args.input
    if not in_path.exists():
        print(f"missing: {in_path}", file=sys.stderr)
        return 2
    out_path = args.out or in_path.with_name(in_path.stem + "_snapped.pdf")

    snapped, total = snap_pdf(in_path, out_path)
    print(f"snapped {snapped}/{total} widgets")
    print(f"output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
