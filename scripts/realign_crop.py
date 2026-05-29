"""Patch D helper — runs FFDetr on a single PNG crop and emits detections.

Invoked as a subprocess from recursive_improvement.py because FFDetr lives in
a separate venv (.venv-commonforms) to keep ML deps isolated from the main
pipeline. Reads one PNG, prints JSON to stdout:

  [{"type": "TextBox|ChoiceButton|Signature",
    "x0": <px>, "y0": <px>, "x1": <px>, "y1": <px>,
    "conf": <float>}, ...]

All coordinates are in image pixels of the input PNG. The caller translates
back to PDF points using the crop origin and DPI it rendered at.

Usage:
  .venv-commonforms/bin/python3 scripts/realign_crop.py <crop.png> [--conf 0.3]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import PIL.Image
from commonforms.utils import Page
from commonforms.inference import FFDetrDetector


_DETECTOR: FFDetrDetector | None = None


def get_detector() -> FFDetrDetector:
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = FFDetrDetector("FFDetr")
    return _DETECTOR


def detect(png_path: Path, conf: float) -> list[dict]:
    img = PIL.Image.open(png_path).convert("RGB")
    page = Page(image=img, width=img.width, height=img.height)
    det = get_detector()
    results = det.extract_widgets([page], confidence=conf, batch_size=1)
    out = []
    widgets = results.get(0, [])
    for w in widgets:
        bb = w.bounding_box
        out.append({
            "type": w.widget_type,
            "x0": bb.x0 * img.width,
            "y0": bb.y0 * img.height,
            "x1": bb.x1 * img.width,
            "y1": bb.y1 * img.height,
            "conf": getattr(w, "confidence", None),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("png", type=Path)
    ap.add_argument("--conf", type=float, default=0.3)
    args = ap.parse_args()
    if not args.png.exists():
        print(json.dumps({"error": f"missing {args.png}"}), file=sys.stderr)
        return 2
    dets = detect(args.png, args.conf)
    print(json.dumps(dets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
