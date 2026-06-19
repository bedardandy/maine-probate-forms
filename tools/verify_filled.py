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
import re
import sys

import fitz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from canonical_adapter import to_case_object        # noqa: E402
from fill_plan import build_plan                     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
_CHECKED = {"yes", "on", "true", "1"}
_OVERFLOW_RE = re.compile(r"^See attached Addendum \d+ for ", re.I)


def _widget_map(filled_pdf) -> tuple[dict, str]:
    """({widget_name: {value, page, type}}, full_document_text) from the output."""
    out: dict[str, dict] = {}
    text_parts = []
    with fitz.open(str(filled_pdf)) as doc:
        for pno, page in enumerate(doc):
            for w in page.widgets() or []:
                out[w.field_name] = {"value": w.field_value, "page": pno,
                                     "type": w.field_type}
            text_parts.append(page.get_text())
    return out, "\n".join(text_parts)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _text_match(expected, actual, field_id: str) -> bool:
    """Exact match, tolerating the fill path's county upper-casing transform.

    fill_pdf._value_for_printed_context upper-cases a county value sitting before
    a printed COUNTY label, so an exact string compare would flag a normal value
    like 'Cumberland' (written 'CUMBERLAND') as a mismatch. County names are
    case-insensitive, so accept a case-insensitive match for county fields.
    """
    e, a = str(expected or "").strip(), str(actual or "").strip()
    if e == a:
        return True
    if "county" in field_id.lower() and e.upper() == a.upper():
        return True
    return False


def verify_filled(form_id: str, case: dict, filled_pdf,
                  root: pathlib.Path = ROOT) -> dict:
    plan = build_plan(form_id, case, root=root)
    if not plan.get("ok"):
        return plan
    geom_path = root / "repo" / "forms" / form_id / "fill_geometry.json"
    geom = (json.loads(geom_path.read_text())["fields"]
            if geom_path.exists() else {})
    widgets, doc_text = _widget_map(filled_pdf)
    doc_text_n = _norm(doc_text)

    fields: dict[str, dict] = {}
    placed = mismatched = missing = overflowed = 0
    for fid, expected in plan["resolved"].items():
        spec = geom.get(fid) or {}
        if spec.get("type") == "enabler" and spec.get("widgets"):
            # fill_pdf writes a checkbox (not text) for a truthy enabler; mirror
            # that here so a checked enabler isn't read as a text mismatch.
            wants_checked = str(expected).strip().lower() in _CHECKED
            w = widgets.get(fid)
            is_checked = bool(w and str(w["value"]).strip().lower() in _CHECKED)
            entry = {"placed": is_checked == wants_checked,
                     "expected": expected,
                     "actual": "checked" if is_checked else "unchecked",
                     "page": (w or {}).get("page"), "kind": "enabler"}
            fields[fid] = entry
            entry["provenance"] = plan.get("provenance", {}).get(fid)
            if entry["placed"]:
                placed += 1
            elif w is None:
                missing += 1
            else:
                mismatched += 1
            continue
        if spec.get("options"):                      # choice field
            # mirror fill_pdf: a select_many list arrives rendered as "a; b"
            vals = (expected if isinstance(expected, list)
                    else re.split(r";\s*", str(expected)))
            wants = {str(v).strip().lower() for v in vals}
            single = len(spec["options"]) == 1
            checked_opts, pages = [], []
            for j, o in enumerate(spec["options"]):
                ov = str(o.get("value") or "").lower()
                name = f"{fid}__{o.get('value') or j}"
                w = widgets.get(name)
                if w and str(w["value"]).strip().lower() in _CHECKED and (
                        ov in wants or single):
                    checked_opts.append(o.get("value"))
                    pages.append(w["page"])
            want_n = 1 if single else len(wants & {
                str(o.get("value") or "").lower() for o in spec["options"]})
            choice_ok = len(checked_opts) >= max(want_n, 1)
            entry = {"placed": choice_ok, "expected": expected,
                     "actual": ("; ".join(str(c) for c in checked_opts)
                                or None),
                     "page": pages[0] if pages else None, "kind": "choice"}
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
            ok = w is not None and _text_match(expected, actual, fid)
            kind = "text"
            # Overflow: the box-below value did not fit, so fill_pdf wrote a
            # "See attached Addendum N for ..." reference and moved the full
            # value to a continuation page. That is a PASS, provided the value
            # really landed on the addendum -- probe a representative chunk.
            if (not ok and w is not None
                    and _OVERFLOW_RE.match(str(actual or "").strip())):
                exp = str(expected or "")
                probe = (exp.split(";")[0] if ";" in exp else exp[:60]).strip()
                if probe and _norm(probe) in doc_text_n:
                    ok, kind = True, "text-overflow"
            entry = {"placed": ok, "expected": expected, "actual": actual,
                     "page": (w or {}).get("page"), "kind": kind}
            if kind == "text-overflow":
                entry["note"] = "value moved to an addendum continuation page"
            if w is None:
                entry["note"] = "no widget with this field_id in the output"
        fields[fid] = entry
        entry["provenance"] = plan.get("provenance", {}).get(fid)
        if entry["placed"]:
            placed += 1
            if entry.get("kind") == "text-overflow":
                overflowed += 1
        elif (entry["kind"] == "text" and entry["page"] is None) or (
                entry["kind"] == "choice" and not spec):
            missing += 1
        else:
            mismatched += 1

    return {
        "ok": True, "form_id": form_id, "filled": str(filled_pdf),
        "fields": fields,
        "summary": {"expected": len(plan["resolved"]), "placed": placed,
                    "mismatched": mismatched, "missing_widget": missing,
                    "overflowed_to_addendum": overflowed},
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
