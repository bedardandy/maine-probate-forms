#!/usr/bin/env python3
"""Regenerate every form's fill_geometry.json from the detection pipeline.

The proper, repeatable path for when source forms are updated or an alignment is
fixed. Reads the detection pipeline's build outputs (`trees/`, `output_fused/`
under `--pipeline-root`) and writes validated `fill_geometry.json` next to each
`schema.json` in this repo (`--repo`, default: this checkout), plus a
`catalog/fill_geometry_status.json` ledger. Invalid geometry is reported and NOT
written, so a bad alignment can't silently ship.

The detection pipeline (download -> detect -> realign -> fuse -> tree) is a
separate project, not part of this repo; point `--pipeline-root` at your local
checkout of it. Typical flow (see docs/maintenance.md):

    python3 scripts/regen_fill_geometry.py \
        --pipeline-root /path/to/detection-pipeline \
        --repo .
    python3 scripts/verify_fill_geometry.py --repo .
    git add repo/forms catalog && git commit -m "Regenerate fill_geometry"

Use `--forms DE-101,PP-203` to regenerate a subset (after a targeted alignment
fix). `--commit` commits for you (push stays manual).
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import gen_fill_geometry as gen          # noqa: E402
import verify_fill_geometry as ver       # noqa: E402


def _form_ids(repo: pathlib.Path) -> list[str]:
    return sorted(d.name for d in (repo / "repo" / "forms").iterdir()
                  if (d / "schema.json").exists())


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pipeline-root", required=True,
                    help="the detection pipeline checkout holding trees/ + output_fused/")
    ap.add_argument("--repo", default=".", type=pathlib.Path,
                    help="published repo to write into (default: cwd)")
    ap.add_argument("--forms", help="comma-separated subset (default: all)")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="staleness check: regenerate in memory and diff against "
                         "the shipped files; write nothing; exit 1 if any drift")
    a = ap.parse_args()
    repo = a.repo.resolve()

    ids = ([s.strip() for s in a.forms.split(",")] if a.forms
           else _form_ids(repo))

    generated: list[str] = []
    plan_only: dict[str, str] = {}
    errors: list[str] = []
    wrote = 0
    changed: list[str] = []; new: list[str] = []; regressed: list[str] = []
    for fid in ids:
        form_dir = repo / "repo" / "forms" / fid
        shipped_p = form_dir / "fill_geometry.json"
        g = gen.build_geometry(fid, a.pipeline_root)
        is_plan = bool(g.get("_missing") or not g.get("fields"))

        if a.check:                               # diff against shipped, write nothing
            shipped = (json.loads(shipped_p.read_text())
                       if shipped_p.exists() else None)
            if is_plan and shipped is not None:
                regressed.append(fid)             # was fillable, now binds 0 widgets
            elif not is_plan and shipped is None:
                new.append(fid)                   # now fillable, not yet shipped
            elif not is_plan and shipped != g:
                changed.append(fid)               # geometry drifted from shipped
            if is_plan:
                plan_only[fid] = g.get("_missing") or "no fillable widgets bound"
            continue

        if is_plan:
            plan_only[fid] = g.get("_missing") or "no fillable widgets bound"
            continue
        schema = json.loads((form_dir / "schema.json").read_text())
        ferrs = ver.validate_geometry(fid, schema, g)
        if ferrs:
            errors += ferrs
            print(f"  INVALID {fid}: {len(ferrs)} error(s) — not written")
            continue
        (form_dir / "fill_geometry.json").write_text(json.dumps(g, indent=2))
        generated.append(fid); wrote += 1

    if a.check:
        drift = changed + new + regressed
        print(f"check: {len(ids) - len(drift)} in sync | drift {len(drift)} "
              f"(changed {len(changed)}, new {len(new)}, regressed {len(regressed)})")
        for fid in changed:
            print(f"  CHANGED   {fid}: geometry differs from shipped — regenerate")
        for fid in new:
            print(f"  NEW       {fid}: now fillable, no shipped geometry — regenerate")
        for fid in regressed:
            print(f"  REGRESSED {fid}: shipped geometry but now binds 0 widgets — investigate")
        if drift:
            print("\nstale — run `make geometry` (regen) to refresh, then commit."); return 1
        print("\nall fill_geometry up to date."); return 0

    # ledger (only rewrite for a full run; a subset preserves prior entries)
    status_path = repo / "catalog" / "fill_geometry_status.json"
    if not a.forms:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps({
            "generated_at": datetime.date.today().isoformat(),
            "n_generated": len(generated), "n_plan_only": len(plan_only),
            "generated": sorted(generated),
            "plan_only": dict(sorted(plan_only.items())),
        }, indent=2))

    print(f"\nregen: wrote {wrote} | plan-only {len(plan_only)} | "
          f"validation errors {len(errors)}")
    for fid, why in sorted(plan_only.items()):
        print(f"  plan-only {fid}: {why}")
    for e in errors[:20]:
        print(f"  ERROR {e}")

    # changed files in the published repo
    diff = subprocess.run(["git", "-C", str(repo), "status", "--short",
                           "repo/forms", "catalog"],
                          capture_output=True, text=True).stdout.strip()
    print("\nchanged in published repo:\n" + (diff or "  (no changes)"))

    if errors:
        print("\nNOT committing — fix the invalid geometry first."); return 1
    if a.commit:
        subprocess.run(["git", "-C", str(repo), "add", "repo/forms", "catalog"],
                       check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m",
                        f"Regenerate fill_geometry ({wrote} forms)"], check=True)
        print("\ncommitted. Review and push when ready.")
    else:
        print("\nReview, then commit + push (or re-run with --commit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
