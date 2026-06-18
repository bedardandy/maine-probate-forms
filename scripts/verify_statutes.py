#!/usr/bin/env python3
"""Validate the per-form statute-consideration layer.

Checks, across all forms:
  1. every form has a statutes.json sidecar;
  2. every cite (governing / per_question / cross_refs) resolves to the trusted
     index (docs/statute-reference/_index/18c-sections.json) or the cross-ref table;
  3. every per_question.field_id exists in that form's schema.json;
  4. the curated source (scripts/author_statutes.py) re-validates and is in sync.

Exit non-zero on any failure. Run via `make statutes-check`.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
FORMS_DIR = REPO / "repo" / "forms"
IDX = REPO / "docs" / "statute-reference" / "_index"


def main() -> int:
    sec = json.loads((IDX / "18c-sections.json").read_text(encoding="utf-8"))["sections"]
    xref = json.loads((IDX / "cross-refs.json").read_text(encoding="utf-8"))["cross_refs"]
    caselaw = json.loads((IDX / "caselaw.json").read_text(encoding="utf-8"))["cases"]
    case_cites = {c["cite"] for c in caselaw.values()}

    def resolves(cite: str) -> bool:
        if cite in xref:
            return True
        if cite.startswith("18-C §"):
            return cite[len("18-C §"):] in sec
        return False

    forms = sorted([d.name for d in FORMS_DIR.iterdir() if d.is_dir()])
    errors: list[str] = []
    n_sidecars = 0

    for form_id in forms:
        sc = FORMS_DIR / form_id / "statutes.json"
        if not sc.exists():
            errors.append(f"{form_id}: missing statutes.json sidecar")
            continue
        n_sidecars += 1
        sidecar = json.loads(sc.read_text(encoding="utf-8"))
        field_ids = {
            f.get("field_id") or f.get("id")
            for f in json.loads(
                (FORMS_DIR / form_id / "schema.json").read_text(encoding="utf-8")
            ).get("fields", [])
        }
        for g in sidecar.get("governing", []):
            if not resolves(g["cite"]):
                errors.append(f"{form_id}: governing cite does not resolve: {g['cite']}")
        for pq in sidecar.get("per_question", []):
            if pq["field_id"] not in field_ids:
                errors.append(f"{form_id}: per_question field_id not in schema: {pq['field_id']}")
            for c in pq.get("considerations", []):
                if c.get("cite") and not resolves(c["cite"]):
                    errors.append(f"{form_id}: per_question cite does not resolve: {c['cite']} ({pq['field_id']})")
        for x in sidecar.get("cross_refs", []):
            if not resolves(x["cite"]):
                errors.append(f"{form_id}: cross_ref cite does not resolve: {x['cite']}")
        for c in sidecar.get("caselaw", []):
            if c["cite"] not in case_cites:
                errors.append(f"{form_id}: caselaw cite not in caselaw.json: {c['cite']}")
            for v in c.get("via", []):
                if not resolves(v):
                    errors.append(f"{form_id}: caselaw 'via' statute does not resolve: {v} ({c['cite']})")

        # Operational form metadata must cite current law. Former Title 18-A is
        # retained only in explicit transition analysis and historical case-law,
        # never as the statute a current skill tells a filler to apply.
        operational_files = [
            FORMS_DIR / form_id / "skill.md",
            FORMS_DIR / form_id / "classifications.yaml",
            FORMS_DIR / form_id / "schema.json",
        ]
        former_code = re.compile(r"\b(?:Title\s+)?18-A\b|18-A\s+M\.R\.S", re.I)
        for operational in operational_files:
            if not operational.exists():
                continue
            for lineno, line in enumerate(
                operational.read_text(encoding="utf-8").splitlines(), 1
            ):
                if former_code.search(line):
                    errors.append(
                        f"{form_id}: former Title 18-A citation in operational "
                        f"metadata {operational.name}:{lineno}; use current "
                        "Title 18-C and keep former law only in an explicit "
                        "pre-2019 transition note"
                    )

    # Every statute a case is tied to must resolve to the index.
    for case_id, case in caselaw.items():
        for cite in case.get("statutes", []):
            if not resolves(cite):
                errors.append(f"caselaw {case_id}: statute cite does not resolve: {cite}")

    # The curated source must also re-validate (catches drift between source and sidecars).
    author = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "author_statutes.py"), "--check"],
        capture_output=True, text=True,
    )
    if author.returncode != 0:
        errors.append("author_statutes.py --check failed:\n" + author.stdout + author.stderr)

    if errors:
        print(f"FAIL — {len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    print(f"OK — {n_sidecars}/{len(forms)} forms have valid statute sidecars; "
          f"all cites resolve; all field_ids exist; curated source in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
