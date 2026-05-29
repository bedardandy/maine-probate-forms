#!/usr/bin/env python3
"""Export a Maine probate form package into templating-system import artifacts.

    python3 tools/export/export_form.py --form DE-101 --target all --out out/DE-101
    python3 tools/export/export_form.py --form DE-101 --target esign --out out/

Targets (paradigms):
  interchange   XFDF template + CSV data dictionary + JSON Schema (vendor-neutral)
  esign         DocuSign template + PandaDoc fields (coordinate placement)
  docassembly   variable manifest + Clio/MyCase/HotDocs token map + logic
  gavel         Gavel/Documate variable + interview manifest
  all           every target

Run from anywhere; --root defaults to the repo root (two levels up). Not legal
advice — output is a draft mapping to verify against the official form + your
system's current import format.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

# Allow running as a script (python3 tools/export/export_form.py) or as a module.
if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from tools.export import model as M
    from tools.export import exporters as E
else:
    from . import model as M
    from . import exporters as E


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True, help="form id, e.g. DE-101")
    ap.add_argument("--target", default="all",
                    choices=["all", *E.PARADIGMS])
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[2]),
                    help="repo root (contains repo/forms/)")
    a = ap.parse_args()

    root = pathlib.Path(a.root)
    if not (root / "repo" / "forms" / a.form / "schema.json").exists():
        print(f"no schema for {a.form} under {root}/repo/forms/", file=sys.stderr)
        return 2
    form = M.load_form(a.form, root)
    targets = list(E.PARADIGMS) if a.target == "all" else [a.target]

    out_dir = pathlib.Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t in targets:
        sub = out_dir / t if a.target == "all" else out_dir
        sub.mkdir(parents=True, exist_ok=True)
        for name, text in E.PARADIGMS[t](form).items():
            (sub / name).write_text(text)
            written.append(str((sub / name).relative_to(out_dir)))
    print(f"{form.form_id}: {len(written)} artifact(s) -> {out_dir}")
    for wpath in written:
        print(f"  {wpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
