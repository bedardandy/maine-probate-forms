#!/usr/bin/env python3
"""Apply conservative source-aware cleanup to shipped fill geometry.

Dry-run by default. Manual/override rectangles carrying ``locked: true`` or a
manual geometry source are never changed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from geometry_optimizer import optimize_geometry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forms", required=True, help="comma-separated form IDs")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    total = 0
    for form_id in (item.strip() for item in args.forms.split(",")):
        package = ROOT / "repo" / "forms" / form_id
        geometry_path = package / "fill_geometry.json"
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        schema = json.loads((package / "schema.json").read_text(encoding="utf-8"))
        with fitz.open(str(fetch_source(form_id))) as doc:
            optimized, changes = optimize_geometry(geometry, schema, doc)
        total += len(changes)
        print(f"{form_id}: {len(changes)} change(s)")
        for change in changes:
            print("  " + json.dumps(change, sort_keys=True))
        if args.apply and changes:
            optimized["optimizer_changes"] = changes
            geometry_path.write_text(
                json.dumps(optimized, indent=2) + "\n", encoding="utf-8"
            )
    print(f"{'applied' if args.apply else 'would apply'}: {total} change(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
