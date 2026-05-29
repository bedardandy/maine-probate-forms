#!/usr/bin/env python3
"""Validate the shipped fill_geometry.json against the schemas (CI-friendly).

Pure stdlib — no PyMuPDF, no build artifacts. Run it on the published repo to
catch drift or breakage after a regenerate (`scripts/regen_fill_geometry.py`),
or as a CI gate. Checks, per form:

  * every field_id in fill_geometry.json exists in schema.json
  * every rect is well-formed (x0<x1, y0<y1) and within the page bounds
  * page index is within n_pages
  * completeness: a form that is not listed plan-only in
    catalog/fill_geometry_status.json must ship a fill_geometry.json
  * (warning) one widget rect bound to multiple field/option targets: usually
    an intentional shared radio, occasionally a copy-paste alignment slip

Exit code is non-zero if any *errors* are found (warnings don't fail CI).

    python3 scripts/verify_fill_geometry.py            # repo = cwd
    python3 scripts/verify_fill_geometry.py --repo /path/to/checkout
"""
from __future__ import annotations

import argparse
import json
import pathlib

PAGE_TOL = 2.0  # points of slack on page bounds


def _rects(spec: dict):
    if spec.get("widgets"):
        return [(w.get("page"), w.get("rect")) for w in spec["widgets"]]
    if spec.get("options"):
        return [(o.get("page"), o.get("rect")) for o in spec["options"]]
    return []


def shared_rect_warnings(form_id: str, geom: dict) -> list[str]:
    """One physical widget rect bound to multiple targets: usually an
    intentional shared radio / branch-enabler, occasionally a copy-paste
    alignment slip. Warn (don't fail) so a human can eyeball it."""
    seen: dict = {}
    for fid, spec in geom.get("fields", {}).items():
        for w in spec.get("widgets", []):
            seen.setdefault((w.get("page"), tuple(w.get("rect") or [])),
                            []).append(fid)
        for o in spec.get("options", []):
            seen.setdefault((o.get("page"), tuple(o.get("rect") or [])),
                            []).append(f"{fid}:{o.get('value')}")
    return [f"{form_id}: rect {list(rect)} on p{pg} bound to "
            f"{len(tgts)} targets {tgts}; verify it's an intentional shared widget"
            for (pg, rect), tgts in seen.items() if len(tgts) > 1]


def validate_geometry(form_id: str, schema: dict, geom: dict) -> list[str]:
    """Return a list of error strings (empty == valid)."""
    errs: list[str] = []
    sfids = {f["field_id"] for f in schema.get("fields", [])}
    psize = geom.get("page_size") or [612, 792]
    npages = geom.get("n_pages")
    for fid, spec in geom.get("fields", {}).items():
        if fid not in sfids:
            errs.append(f"{form_id}:{fid} not in schema")
        if not spec.get("widgets") and not spec.get("options"):
            errs.append(f"{form_id}:{fid} has neither widgets nor options")
        for pg, rect in _rects(spec):
            if npages is not None and not (isinstance(pg, int) and 0 <= pg < npages):
                errs.append(f"{form_id}:{fid} page {pg} out of range (n={npages})")
            if not (isinstance(rect, list) and len(rect) == 4):
                errs.append(f"{form_id}:{fid} bad rect {rect}"); continue
            x0, y0, x1, y1 = rect
            if not (x1 > x0 and y1 > y0):
                errs.append(f"{form_id}:{fid} degenerate rect {rect}")
            if (x0 < -PAGE_TOL or y0 < -PAGE_TOL
                    or x1 > psize[0] + PAGE_TOL or y1 > psize[1] + PAGE_TOL):
                errs.append(f"{form_id}:{fid} rect {rect} outside page {psize}")
    return errs


def verify_repo(root: pathlib.Path) -> tuple[list[str], list[str], dict]:
    forms_dir = root / "repo" / "forms"
    status_path = root / "catalog" / "fill_geometry_status.json"
    plan_only = set()
    if status_path.exists():
        plan_only = set(json.loads(status_path.read_text()).get("plan_only", {}))

    errors: list[str] = []
    warnings: list[str] = []
    n_geo = n_plan = 0
    for d in sorted(forms_dir.iterdir()):
        schema_p = d / "schema.json"
        if not schema_p.exists():
            continue
        fid = d.name
        geo_p = d / "fill_geometry.json"
        if geo_p.exists():
            n_geo += 1
            geom = json.loads(geo_p.read_text())
            errors += validate_geometry(fid, json.loads(schema_p.read_text()),
                                        geom)
            warnings += shared_rect_warnings(fid, geom)
        elif fid in plan_only:
            n_plan += 1
        else:
            warnings.append(f"{fid}: no fill_geometry.json and not marked "
                            "plan_only in catalog/fill_geometry_status.json")
    return errors, warnings, {"with_geometry": n_geo, "plan_only": n_plan}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", type=pathlib.Path)
    a = ap.parse_args()
    errors, warnings, stats = verify_repo(a.repo.resolve())
    print(f"fill_geometry: {stats['with_geometry']} forms with geometry, "
          f"{stats['plan_only']} plan-only")
    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  ERROR {e}")
    if errors:
        print(f"FAIL: {len(errors)} error(s)"); return 1
    print(f"OK ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
