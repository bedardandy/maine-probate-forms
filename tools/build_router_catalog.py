#!/usr/bin/env python3
"""Generate catalog/router_catalog.json for tools/route_form.py.

Two catalog flavors are emitted:
  * cat_title    — "form_id | category | title" for all forms (compact, ~1.1k tok)
  * cat_surgical — cat_title PLUS a curated one-line disambiguation hint on ONLY
                   the ~confusable clusters (estate-vs-conservatorship parallels,
                   petition-vs-acceptance, adult-vs-minor, petition-vs-affidavit,
                   the notice family, special-administrator-vs-emergency).

A/B evaluation (see docs/router-eval.md) showed blanket enrichment of every form
costs ~2.3x the prompt tokens for no net accuracy gain on capable models, while
surgical hints on just the confusable forms recover the only real benefit at
~title-only cost. Regenerate after adding/removing forms.

    python3 tools/build_router_catalog.py
"""
from __future__ import annotations

import glob
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Curated "vs" hints — only for forms that an LLM router actually confuses.
# Keep each terse; the discriminating distinction is what matters.
DISAMBIG = {
    # estate (decedent) vs conservatorship (living protected person) parallels
    "DE-403": "bond for a deceased person's ESTATE (personal representative); not a conservator",
    "PP-405": "bond for a CONSERVATOR of a living protected person; not an estate PR",
    "DE-405": "inventory of a DECEASED person's estate; not a conservatorship",
    "PP-406": "inventory of a living ward's CONSERVATORSHIP; not a decedent's estate",
    "DE-406": "financial account for a DECEASED person's estate",
    "PP-407": "financial account for a living ward's CONSERVATORSHIP",
    "DE-503": "claim against a DECEASED person's estate",
    "PP-408": "claim against a living protected person's CONSERVATORSHIP",
    "DE-504": "resolve a disputed claim in a DECEASED person's estate",
    "PP-409": "resolve a disputed claim in a CONSERVATORSHIP",
    # petition (to appoint) vs acceptance (by the appointee) — adult GC
    "PP-201": "PETITION to appoint a guardian for an adult",
    "PP-203": "the appointee ACCEPTS being guardian (adult); not the petition",
    "PP-401": "PETITION to appoint a conservator for an adult",
    "PP-402": "the appointee ACCEPTS being conservator (adult); not the petition",
    "PP-205": "single PETITION for BOTH guardian and conservator (adult)",
    "PP-207": "the appointee ACCEPTS both guardian and conservator roles",
    # minor vs adult
    "PP-107": "appoint a CONSERVATOR for a MINOR's property",
    "PP-108": "the appointee ACCEPTS conservator of a MINOR",
    "GS-008": "the appointee ACCEPTS guardian of a MINOR",
    "GS-014": "guardian's status report for a MINOR",
    "PP-209": "guardian's periodic report for an ADULT",
    # petition vs affidavit — name change
    "CN-1":   "PETITION asking the court to change an adult's name (court order)",
    "AF-103": "sworn AFFIDAVIT documenting an already-changed adult name (e.g. via marriage); not a petition",
    "NC-001": "petition to change a MINOR's name",
    # notice family
    "N-105":  "an interested person DEMANDS to receive notice",
    "N-115":  "notice that a PERSONAL REPRESENTATIVE was appointed (estate)",
    "N-117":  "notice that a GUARDIAN/CONSERVATOR was appointed",
    "N-107":  "WAIVING the right to notice",
    # special administrator (decedent) vs emergency GC (living person)
    "DE-301": "urgent interim administration of a DECEDENT's estate before full probate",
    "PP-507": "EMERGENCY guardian/conservator for a LIVING person in crisis",
}


def main() -> int:
    rows = []
    for mp in sorted(glob.glob(str(ROOT / "repo" / "forms" / "*" / "metadata.json"))):
        m = json.loads(pathlib.Path(mp).read_text())
        rows.append({"form_id": m["form_id"], "category": m.get("category", ""),
                     "title": m.get("title", ""), "hint": DISAMBIG.get(m["form_id"], "")})
    rows.sort(key=lambda r: r["form_id"])

    cat_title = "\n".join(f"{r['form_id']} | {r['category']} | {r['title']}" for r in rows)
    cat_surgical = "\n".join(
        f"{r['form_id']} | {r['category']} | {r['title']}"
        + (f" — {r['hint']}" if r["hint"] else "") for r in rows)

    out = {
        "_generated_by": "tools/build_router_catalog.py",
        "n_forms": len(rows),
        "form_ids": [r["form_id"] for r in rows],
        "cat_title": cat_title,
        "cat_surgical": cat_surgical,
        "disambiguated": sorted(DISAMBIG),
    }
    dest = ROOT / "catalog" / "router_catalog.json"
    dest.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {dest.relative_to(ROOT)}: {len(rows)} forms, "
          f"{len(DISAMBIG)} disambiguation hints")
    print(f"  cat_title    ~{len(cat_title)//4} tok")
    print(f"  cat_surgical ~{len(cat_surgical)//4} tok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
