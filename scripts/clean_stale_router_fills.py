"""Move aside stale router fills that current routing would reject.

Background: as the router's PREFIX_CASE_TYPES filter evolved (e.g. adding
`"GS": {"guardianship_minor"}` to keep adult cases out of minor-only
forms), pre-existing `filled_router.*.fixed.json` artifacts on disk
became stale — the vision audit still picks them up and flags every
minor-specific blank as a major.

This script does NOT re-run the router or re-fill anything. It only
identifies files whose form_id is incompatible with the case's
case_type per the CURRENT router rules and moves them (and their
sibling pipeline artifacts) into `intermediate/router/_stale_<ts>/`.

Definitive-stale only: we move a file iff the router's PREFIX_CASE_TYPES
filter would reject the form for the case's case_type. Threshold-driven
or boost-driven rejections are NOT touched — those are ambiguous
(historical fills may have used a different threshold).

Usage:
  python3 scripts/clean_stale_router_fills.py            # dry run
  python3 scripts/clean_stale_router_fills.py --apply    # actually move
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from router.router import PREFIX_CASE_TYPES  # noqa: E402

CASES_PATH = REPO_ROOT / "router" / "synthetic_cases.jsonl"
ROUTER_DIR = REPO_ROOT / "intermediate" / "router"

# Matches: filled_router.<event_tag>.<FORM-ID>.<suffix>.json
# event_tag has dots/underscores; form_id is uppercase prefix + dash + digits.
FILE_RE = re.compile(
    r"^filled_router\.(?P<event>e\d+_[a-z_]+)\."
    r"(?P<form>[A-Z]+-\d+[A-Za-z0-9._-]*?)\."
    r"(?P<suffix>[a-z]+)\.json$"
)


def _form_prefix(form_id: str) -> str:
    m = re.match(r"^([A-Z]+)", form_id)
    return m.group(1) if m else ""


def _is_rejected(case_type: str, form_id: str) -> bool:
    compat = PREFIX_CASE_TYPES.get(_form_prefix(form_id))
    if compat is None:
        return False
    return case_type not in compat


def _load_case_types() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in CASES_PATH.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        cid = d.get("id")
        ct = (d.get("case") or {}).get("case_type")
        if cid and ct:
            out[cid] = ct
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually move files. Without this, dry-run.")
    args = ap.parse_args()

    case_types = _load_case_types()
    if not case_types:
        print("no cases loaded", file=sys.stderr)
        return 2

    stale_root = ROUTER_DIR / f"_stale_{dt.datetime.now():%Y%m%d_%H%M%S}"

    rejected_total = 0
    by_form: dict[str, int] = {}
    by_case_type: dict[str, int] = {}
    moved_files = 0

    for case_dir in sorted(ROUTER_DIR.iterdir()):
        if not case_dir.is_dir() or case_dir.name.startswith("_"):
            continue
        ct = case_types.get(case_dir.name)
        if ct is None:
            continue
        for f in case_dir.iterdir():
            m = FILE_RE.match(f.name)
            if not m:
                continue
            form = m.group("form")
            if not _is_rejected(ct, form):
                continue
            rejected_total += 1 if m.group("suffix") == "fixed" else 0
            if m.group("suffix") == "fixed":
                by_form[form] = by_form.get(form, 0) + 1
                by_case_type[ct] = by_case_type.get(ct, 0) + 1
            if args.apply:
                dest_dir = stale_root / case_dir.name
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), dest_dir / f.name)
                moved_files += 1

    mode = "MOVED" if args.apply else "WOULD MOVE"
    print(f"=== {mode} ===")
    print(f"Stale (case×form) tuples: {rejected_total}")
    if args.apply:
        print(f"Total files moved: {moved_files}")
        print(f"Stale tree: {stale_root.relative_to(REPO_ROOT)}")
    print()
    print("By form:")
    for form, n in sorted(by_form.items(), key=lambda x: -x[1]):
        print(f"  {form:10s}  {n}")
    print()
    print("By case_type:")
    for ct, n in sorted(by_case_type.items(), key=lambda x: -x[1]):
        print(f"  {ct:25s}  {n}")

    if not args.apply:
        print()
        print("Dry-run only. Re-run with --apply to actually move.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
