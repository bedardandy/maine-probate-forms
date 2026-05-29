"""Run validate_form against the local vLLM (Qwen3.6-27B-FP8 on :8088).

The current config.py defaults point at the llama-router on :8083, which
isn't running on this host. The Qwen3.6-27B-FP8 vLLM service IS running
on :8088 and has vision (image_token_id is set, language_model_only=false).
This script overrides the endpoints and runs validation for one form,
emitting a fresh validation JSON with the new group_* annotations.

Usage:
  scripts/run_vlm_validation.py PB-007
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# Override BEFORE importing any module that reads config at import time.
config.VLM_API_BASE = os.environ.setdefault("VLM_API_BASE", "http://localhost:8088/v1")
config.VLM_MODEL = "Qwen3.6-27B-FP8"
# Naming mode keeps every candidate and adds the snake_case name + group fields,
# which is what we want here — gating mode would also reject candidates and we
# want full coverage for downstream radio-group inference.
os.environ.setdefault("VLM_MODE", "naming")

from modules import vlm_validator  # noqa: E402
from modules.field_detector import load_detection  # noqa: E402
from download import list_downloaded_forms  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("form_id", help="e.g. PB-007")
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()

    detection = load_detection(args.form_id)
    if detection is None:
        print(f"no detection JSON for {args.form_id}", file=sys.stderr)
        return 2

    pdf_path = None
    for f in list_downloaded_forms():
        if f["form_id"] == args.form_id:
            pdf_path = f["path"]
            break
    if pdf_path is None:
        print(f"no source PDF for {args.form_id}", file=sys.stderr)
        return 2

    print(f"validating {args.form_id} via {config.VLM_API_BASE} model={config.VLM_MODEL}")
    result = vlm_validator.validate_form(detection, pdf_path)
    out = args.out or (config.VALIDATION_DIR / f"{args.form_id}.json")
    out.write_text(result.model_dump_json(indent=2))
    print(f"\nwrote {out}")
    print(result.review_summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
