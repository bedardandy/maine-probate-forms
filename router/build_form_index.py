#!/usr/bin/env python3
"""Build router/form_index.jsonl from repo/forms/*/skill.md frontmatter.

The form router needs structured trigger data for each of the 79 forms.
Every skill.md begins with a YAML frontmatter block that already
encodes:
  - statutes (legal basis)
  - filing_deadline_anchor (the event type that starts the clock)
  - filing_deadline_days (how long until filing is due)
  - filer_role (who files it)
  - parties (who is named in the form)
  - service_required + service_recipients (notice obligations)
  - n_fields, addendum_supported (fill complexity hints)

This script extracts that block and emits one JSON line per form.
Downstream routers can join on `deadline_anchor` to map case events
(e.g. "decedent died on 2026-04-12") to a candidate form list, then
rank by filer_role / parties / statutes against the case primitives.

Usage:
  python3 router/build_form_index.py
  python3 router/build_form_index.py --check     # exit nonzero on
                                                  # missing keys
"""
import argparse
import json
import pathlib
import re
import sys
import yaml


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FORMS_DIR = REPO_ROOT / "repo" / "forms"
OUT_PATH = REPO_ROOT / "router" / "form_index.jsonl"
OVERRIDES_PATH = REPO_ROOT / "router" / "anchor_overrides.json"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

REQUIRED_KEYS = ["form_id", "form_title", "filer_role"]
TRIGGER_KEYS = [
    "form_id", "form_title", "form_revision", "jurisdiction", "court",
    "filer_role", "statutes", "filing_deadline_days",
    "filing_deadline_anchor", "service_required", "service_recipients",
    "parties", "n_fields", "addendum_supported", "slot_groups",
    "legal_choices", "addendum_target_fields",
]


def parse(path: pathlib.Path) -> dict | None:
    text = path.read_text()
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        print(f"  {path.parent.name}: YAML parse failed — {e}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Exit nonzero if any form is missing required keys.")
    args = ap.parse_args()

    skill_files = sorted(FORMS_DIR.glob("*/skill.md"))
    if not skill_files:
        sys.exit(f"no skill.md under {FORMS_DIR}")

    overrides: dict[str, str] = {}
    if OVERRIDES_PATH.exists():
        raw = json.loads(OVERRIDES_PATH.read_text())
        overrides = {k: v for k, v in raw.items() if not k.startswith("_")}

    rows: list[dict] = []
    missing_required: list[tuple[str, list[str]]] = []
    no_frontmatter: list[str] = []

    for sp in skill_files:
        fid = sp.parent.name
        fm = parse(sp)
        if fm is None:
            no_frontmatter.append(fid)
            continue
        # Force form_id to match directory name so the index is
        # authoritative even if the skill.md is stale.
        fm["form_id"] = fid

        miss = [k for k in REQUIRED_KEYS if not fm.get(k)]
        if miss:
            missing_required.append((fid, miss))

        # Anchor override: force the anchor for forms listed in
        # router/anchor_overrides.json. Used for two cases:
        #   (a) backfill an anchor for companion forms whose skill.md
        #       has none (e.g. DE-104 PR Acceptance → decedent_death_date)
        #   (b) move a niche-path form onto its own dedicated sub-event
        #       (e.g. DE-407 renunciation off shared decedent_death_date
        #       onto renunciation_filing) so it doesn't compete with
        #       the primary application form at the same date.
        if fid in overrides:
            fm["filing_deadline_anchor"] = overrides[fid]

        row = {k: fm.get(k) for k in TRIGGER_KEYS}
        rows.append(row)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)} ({len(rows)} forms)")
    if no_frontmatter:
        print(f"  no frontmatter: {', '.join(no_frontmatter)}")
    if missing_required:
        print(f"  forms missing required keys:")
        for fid, miss in missing_required:
            print(f"    {fid}: missing {miss}")

    if args.check and (no_frontmatter or missing_required):
        sys.exit(1)


if __name__ == "__main__":
    main()
