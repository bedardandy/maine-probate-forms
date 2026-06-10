#!/usr/bin/env python3
"""Post-fill verification: reopen a filled PDF and diff it against the plan.

`tools/fill_pdf.py` reports what it *wrote*; this checks what actually *landed*.
It re-derives the fill plan from the case, extracts every widget value from the
filled output, and compares per field:

  * text fields    — the widget named `<field_id>` must carry the resolved value
                     (continuation widgets `<field_id>__N` are part of the chain).
  * choice fields  — at least one `<field_id>__<option>` checkbox matching the
                     resolved value(s) must be checked.

Machine-readable result: per-field {placed, expected, actual, page} plus a
summary {expected, placed, mismatched, missing_widget}. Exit 0 only when every
resolved field placed. Run it as the final step of any fill (see
docs/agent-workflow.md).

    python3 tools/verify_filled.py --form DE-101 --case case.json \
        --filled /tmp/DE-101.filled.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import fitz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from canonical_adapter import to_case_object        # noqa: E402
from fill_plan import build_plan                     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
_CHECKED = {"yes", "on", "true", "1"}


def _widget_map(filled_pdf) -> dict:
    """{widget_name: {"value": str, "page": int, "type": int}} for every widget."""
    out: dict[str, dict] = {}
    with fitz.open(str(filled_pdf)) as doc:
        for pno, page in enumerate(doc):
            for w in page.widgets() or []:
                out[w.field_name] = {"value": w.field_value, "page": pno,
                                     "type": w.field_type}
    return out


def verify_filled(form_id: str, case: dict, filled_pdf,
                  root: pathlib.Path = ROOT) -> dict:
    plan = build_plan(form_id, case, root=root)
    if not plan.get("ok"):
        return plan
    geom_path = root / "repo" / "forms" / form_id / "fill_geometry.json"
    geom = (json.loads(geom_path.read_text())["fields"]
            if geom_path.exists() else {})
    widgets = _widget_map(filled_pdf)

    fields: dict[str, dict] = {}
    placed = mismatched = missing = 0
    for fid, expected in plan["resolved"].items():
        spec = geom.get(fid) or {}
        if spec.get("options"):                      # choice field
            wants = {str(v).lower() for v in (
                expected if isinstance(expected, list) else [expected])}
            single = len(spec["options"]) == 1
            hit = None
            for j, o in enumerate(spec["options"]):
                ov = str(o.get("value") or "").lower()
                name = f"{fid}__{o.get('value') or j}"
                w = widgets.get(name)
                if w and str(w["value"]).strip().lower() in _CHECKED and (
                        ov in wants or single):
                    hit = {"value": o.get("value"), "page": w["page"]}
                    break
            entry = {"placed": hit is not None, "expected": expected,
                     "actual": hit["value"] if hit else None,
                     "page": hit["page"] if hit else None, "kind": "choice"}
        else:                                        # text field
            w = widgets.get(fid)
            actual = (w or {}).get("value")
            # a value split across a continuation chain re-joins for comparison
            i = 1
            while f"{fid}__{i}" in widgets:
                nxt = widgets[f"{fid}__{i}"].get("value") or ""
                if nxt:
                    actual = f"{actual or ''} {nxt}".strip()
                i += 1
            ok = (w is not None and
                  str(actual or "").strip() == str(expected or "").strip())
            entry = {"placed": ok, "expected": expected, "actual": actual,
                     "page": (w or {}).get("page"), "kind": "text"}
            if w is None:
                entry["note"] = "no widget with this field_id in the output"
        fields[fid] = entry
        if entry["placed"]:
            placed += 1
        elif (entry["kind"] == "text" and entry["page"] is None) or (
                entry["kind"] == "choice" and not spec):
            missing += 1
        else:
            mismatched += 1

    return {
        "ok": True, "form_id": form_id, "filled": str(filled_pdf),
        "fields": fields,
        "summary": {"expected": len(plan["resolved"]), "placed": placed,
                    "mismatched": mismatched, "missing_widget": missing},
        "all_placed": placed == len(plan["resolved"]),
        "note": "Verifies plan.resolved against the output's widget values. "
                "Narrative/blank/unresolved buckets are out of scope; visual "
                "placement still needs human review. Not legal advice.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True)
    ap.add_argument("--case", required=True, help="canonical or native case JSON")
    ap.add_argument("--filled", required=True, help="the filled output PDF")
    ap.add_argument("--full", action="store_true",
                    help="print per-field detail (default: summary + failures)")
    a = ap.parse_args()
    case = to_case_object(json.loads(pathlib.Path(a.case).read_text()))
    res = verify_filled(a.form, case, a.filled)
    if not res.get("ok"):
        print(json.dumps(res, indent=2)); return 1
    if a.full:
        print(json.dumps(res, indent=2))
    else:
        s = res["summary"]
        print(f"{res['form_id']}: {s['placed']}/{s['expected']} resolved fields "
              f"placed ({s['mismatched']} mismatched, "
              f"{s['missing_widget']} missing widget)")
        for fid, e in res["fields"].items():
            if not e["placed"]:
                print(f"  FAIL {fid}: expected {e['expected']!r}, "
                      f"got {e['actual']!r}")
    return 0 if res["all_placed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
