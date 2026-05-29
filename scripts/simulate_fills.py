"""Apply pre-canned fill scenarios to a tree-driven PDF and run validation.

End-to-end test of the apply_tree → validator pipeline. Each scenario:
  1. Opens a fresh copy of the PDF.
  2. Applies a {field_name: value} dict (booleans for checkboxes, strings
     for text/date/currency).
  3. Saves to a scenario-specific output PDF.
  4. Runs a Python port of validateForm() against the simulated state and
     prints what app.alert() would say if the user clicked Validate now.

This lets us confirm that the JS validator catches what we expect WITHOUT
needing a JS runtime — the Python port and gen_validation_js share the
same logic.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys
from collections import defaultdict

import fitz
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gen_validation_js import (  # noqa: E402
    _text_field_names,
    build_when_index,
    is_virtual_gate,
    parse_when,
)


# ─── PDF state writer ────────────────────────────────────────────────────


def _read_field_xrefs(doc: fitz.Document) -> dict[str, int]:
    """Return {field_name: xref} for every named field in /AcroForm/Fields."""
    cat = doc.pdf_catalog()
    typ, val = doc.xref_get_key(cat, "AcroForm")
    if typ == "xref":
        m = re.match(r"(\d+) \d+ R", val)
        if not m:
            return {}
        af = int(m.group(1))
        _, fv = doc.xref_get_key(af, "Fields")
    else:
        m2 = re.search(r"/Fields\s*\[([^\]]*)\]", val)
        fv = "[" + m2.group(1) + "]" if m2 else "[]"
    refs = re.findall(r"(\d+) 0 R", fv)
    out: dict[str, int] = {}
    for r in refs:
        xr = int(r)
        t = doc.xref_get_key(xr, "T")
        if t[0] != "string":
            continue
        out[t[1].strip("()")] = xr
    return out


def _set_checkbox(doc: fitz.Document, field_xref: int, on: bool) -> None:
    v = "/Yes" if on else "/Off"
    doc.xref_set_key(field_xref, "V", v)
    kids_v = doc.xref_get_key(field_xref, "Kids")
    if kids_v[0] == "array":
        for kx in re.findall(r"(\d+) 0 R", kids_v[1]):
            doc.xref_set_key(int(kx), "AS", v)
    else:
        doc.xref_set_key(field_xref, "AS", v)


def _set_text(doc: fitz.Document, field_xref: int, value: str) -> None:
    safe = (str(value).replace("\\", "\\\\")
                       .replace("(", "\\(")
                       .replace(")", "\\)"))
    doc.xref_set_key(field_xref, "V", f"({safe})")


def apply_fills(doc: fitz.Document, fills: dict) -> list[str]:
    """Apply {name: value} fills. Returns list of names not found in form."""
    field_xrefs = _read_field_xrefs(doc)
    missed: list[str] = []
    for name, val in fills.items():
        xref = field_xrefs.get(name)
        if xref is None:
            missed.append(name)
            continue
        if isinstance(val, bool):
            _set_checkbox(doc, xref, val)
        else:
            _set_text(doc, xref, str(val))
    return missed


# ─── Python port of validateForm() ──────────────────────────────────────


def _is_filled(state: dict, name: str) -> bool:
    v = state.get(name)
    if v is None or v is False:
        return False
    s = str(v).strip()
    return bool(s) and s != "Off" and s != "/Off"


def _is_checked(state: dict, name: str) -> bool:
    v = state.get(name)
    if v is None or v is False:
        return False
    return v not in ("Off", "/Off", "")


def validate(tree: dict, state: dict) -> list[str]:
    issues: list[str] = []
    nodes_by_id = {
        n["id"]: n for n in tree.get("nodes", [])
        if isinstance(n, dict) and n.get("id")
    }
    when_index = build_when_index(tree)

    for node in tree.get("nodes", []):
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        ntype = node.get("type")
        if not nid:
            continue

        if ntype == "select_one":
            real_opts = [o for o in node.get("options") or []
                         if isinstance(o, dict) and not o.get("virtual")
                         and (o.get("widget") or o.get("widgets"))]
            virtual_opts = [o for o in node.get("options") or []
                            if isinstance(o, dict) and o.get("virtual")]

            # Mutex.
            if not node.get("virtual") and len(real_opts) >= 2:
                names = [f"{nid}__{o['value']}" for o in real_opts]
                on = [n for n in names if _is_checked(state, n)]
                if len(on) > 1:
                    issues.append(
                        f"More than one option selected for '{nid}': "
                        f"{', '.join(on)}"
                    )

            # Virtual column mutex.
            if node.get("virtual"):
                gated: dict[str, list[str]] = defaultdict(list)
                for opt in node.get("options") or []:
                    if not isinstance(opt, dict):
                        continue
                    val = opt.get("value")
                    if not val:
                        continue
                    for n2 in when_index.get((nid, val), []):
                        gated[val].extend(_text_field_names(n2))
                if len(gated) >= 2:
                    filled = {v: any(_is_filled(state, fn) for fn in gated[v])
                              for v in gated}
                    if sum(1 for v in filled.values() if v) > 1:
                        issues.append(
                            f"Multiple alternatives populated for '{nid}'. "
                            f"Pick one."
                        )

            # OR-with-multi-select.
            if len(real_opts) == 1 and len(virtual_opts) == 1:
                real_name = f"{nid}__{real_opts[0]['value']}"
                vval = virtual_opts[0]["value"]
                for n2 in when_index.get((nid, vval), []):
                    if n2.get("type") != "select_many" or n2.get("id") == nid:
                        continue
                    party_names = [f"{n2['id']}__{o['value']}"
                                   for o in n2.get("options") or []
                                   if isinstance(o, dict) and o.get("value")]
                    if not party_names:
                        continue
                    any_party = any(_is_checked(state, pn) for pn in party_names)
                    if _is_checked(state, real_name) and any_party:
                        issues.append(
                            f"'{real_opts[0]['value']}' is checked but parties "
                            f"are listed in '{n2['id']}'."
                        )

        # when-leak (delegates to shared parse_when)
        when = (node.get("when") or "").strip()
        if when and ntype in ("text", "date", "currency"):
            ref_fields = _text_field_names(node)
            if not ref_fields:
                continue
            parsed = parse_when(when)
            if not parsed:
                continue
            gate_id, gate_names, must_be_on, label = parsed
            if is_virtual_gate(nodes_by_id.get(gate_id)):
                continue
            any_on = any(_is_checked(state, gn) for gn in gate_names)
            gate_satisfied = any_on if must_be_on else not any_on
            if not gate_satisfied:
                if any(_is_filled(state, rf) for rf in ref_fields):
                    issues.append(
                        f"'{nid}' is filled but its gate is not satisfied "
                        f"({label})."
                    )

    return issues


# ─── Scenarios ──────────────────────────────────────────────────────────
# Scenarios are normally loaded from `trees/<form_id>.scenarios.yaml`.
# The `_FALLBACK_SCENARIOS` below is kept for direct invocations against
# PB-007 without a sidecar file, but new forms should ship their own YAML.


_FALLBACK_SCENARIOS: dict[str, dict] = {
    "clean_probate": {
        # Probate-court track, standard appointment, no objections, hourly cap.
        "county_probate": "York",
        "docket_no_probate": "2025-0123",
        "case_title": "In re Jane Doe, a minor child",
        "appointment_level__standard": True,
        "minor_children_names": "Jane Doe (DOB 01/15/2018)",
        "gal_name": "Mary Smith, Esq.",
        "gal_contact_info": "msmith@example.law / 207-555-0100",
        "gal_roster_status__on_roster": True,
        "objection_status__none": True,
        "appointment_end_event__final_judgment": True,
        "report_requirement__none": True,
        "fee_structure__hourly_cap": True,
        "hourly_cap_total": "5000.00",
        "hourly_cap_hours": "40",
        "hourly_rate": "125.00",
        "payment_method__lump_sum": True,
        "lump_sum_deadline": "06/15/2025",
        "petitioner_lump_sum": "2500.00",
        "respondent_lump_sum": "2500.00",
        "judge_date": "05/10/2026",
        "judge_name": "Hon. R. Patterson",
    },
    "clean_district": {
        # District-court track to exercise the other column.
        "district_court_location": "Portland District Court",
        "docket_no_district": "DCD-2025-77",
        "case_title": "In re Carter family matter",
        "appointment_level__limited_purpose": True,
        "limited_duties": "Investigate custody-relevant facts and report.",
        "minor_children_names": "Liam Carter (8), Ava Carter (5)",
        "gal_name": "P. Nguyen, Esq.",
        "gal_contact_info": "pn@nguyenlaw.com",
        "gal_roster_status__on_roster": True,
        "objection_status__none": True,
        "appointment_end_event__final_hearing": True,
        "report_requirement__summary_report": True,
        "summary_report_deadline": "07/01/2025",
        "fee_structure__flat_fee": True,
        "flat_fee_amount": "1500.00",
        "payment_method__periodic_bill": True,
        "billing_frequency__monthly": True,
        "petitioner_percent": "50",
        "respondent_percent": "50",
        "payment_days__35_days": True,
        "judge_date": "05/10/2026",
        "judge_name": "Hon. K. Allard",
    },
    "mutex_violation": {
        # Two appointment levels checked at once.
        "county_probate": "York",
        "case_title": "Mutex test case",
        "appointment_level__standard": True,
        "appointment_level__expanded": True,
    },
    "or_violation": {
        # Both "no objection" AND a listed objecting party.
        "county_probate": "York",
        "case_title": "OR violation test",
        "appointment_level__standard": True,
        "objection_status__none": True,
        "objecting_parties_appointment__petitioner": True,
    },
    "or_violation_fee": {
        # W024 covers BOTH appointment + fee. So checking "no objection"
        # while listing a fee-objecting party should also trigger.
        "county_probate": "York",
        "case_title": "OR violation (fee) test",
        "appointment_level__standard": True,
        "objection_status__none": True,
        "objecting_parties_fee__respondent": True,
    },
    "column_violation": {
        # Both probate-track AND district-track fields populated.
        "county_probate": "York",
        "docket_no_probate": "2025-0123",
        "district_court_location": "Portland District Court",
        "docket_no_district": "DCD-2025-77",
        "case_title": "Column conflict test",
        "appointment_level__standard": True,
    },
    "when_leak": {
        # appointment_level=standard but expanded-only fields are filled.
        "county_probate": "York",
        "case_title": "When-leak test",
        "appointment_level__standard": True,
        "expanded_duties": "Should not be here — gated by expanded.",
    },
    "when_leak_enabler": {
        # Enabler-gated leak: expanded_review_records_1 is OFF but
        # expanded_person_1_name is filled. Exercises the X==true form.
        "county_probate": "York",
        "case_title": "Enabler leak test",
        "appointment_level__expanded": True,
        "expanded_person_1_name": "Dr. K. Reed (should not appear)",
    },
    "when_leak_list": {
        # List-membership leak: report_requirement=summary_report (not in
        # ['full_report_14_days','full_report_date']) but report_issues
        # is filled.
        "county_probate": "York",
        "case_title": "List-membership leak test",
        "appointment_level__standard": True,
        "report_requirement__summary_report": True,
        "report_issues": "Should not appear — only valid for full reports.",
    },
    "enabler_consistent": {
        # Enabler IS on, gated field IS filled — should pass cleanly.
        "county_probate": "York",
        "case_title": "Enabler consistent test",
        "appointment_level__expanded": True,
        "expanded_review_records_1": True,
        "expanded_person_1_name": "Dr. K. Reed",
    },
}


# ─── Driver ─────────────────────────────────────────────────────────────


def run_scenario(name: str, fills: dict, src: pathlib.Path,
                 out_dir: pathlib.Path, tree: dict) -> tuple[list[str], list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{src.stem}__{name}.pdf"
    shutil.copy(src, out)
    doc = fitz.open(out)
    missed = apply_fills(doc, fills)
    doc.save(out, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    issues = validate(tree, fills)
    return issues, missed


def _load_scenarios(tree_yaml: pathlib.Path) -> dict[str, dict]:
    """Load scenarios from `<tree>.scenarios.yaml`, or fall back to PB-007 dict."""
    sidecar = tree_yaml.with_suffix("").with_suffix(".scenarios.yaml")
    if sidecar.exists():
        loaded = yaml.safe_load(sidecar.read_text()) or {}
        if not isinstance(loaded, dict):
            print(f"bad scenarios file: {sidecar}", file=sys.stderr)
            return {}
        return loaded
    return _FALLBACK_SCENARIOS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("tree_yaml", type=pathlib.Path)
    ap.add_argument("--out-dir", type=pathlib.Path, required=True)
    ap.add_argument("--scenarios", type=pathlib.Path,
                    help="Override scenarios YAML (default: <tree>.scenarios.yaml).")
    ap.add_argument("--scenario", help="Run a single scenario (default: all).")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr); return 2
    if not args.tree_yaml.exists():
        print(f"missing: {args.tree_yaml}", file=sys.stderr); return 2

    tree = yaml.safe_load(args.tree_yaml.read_text())
    if args.scenarios:
        scenarios = yaml.safe_load(args.scenarios.read_text()) or {}
    else:
        scenarios = _load_scenarios(args.tree_yaml)
    names = [args.scenario] if args.scenario else list(scenarios.keys())
    bad = 0
    for name in names:
        if name not in scenarios:
            print(f"unknown scenario: {name}", file=sys.stderr); bad += 1; continue
        fills = scenarios[name]
        print(f"\n── {name} ──")
        print(f"  fills: {len(fills)} field(s)")
        issues, missed = run_scenario(name, fills, args.pdf, args.out_dir, tree)
        if missed:
            print(f"  WARN missing fields: {missed}")
        if issues:
            print(f"  validateForm() would alert {len(issues)} issue(s):")
            for i in issues:
                print(f"    • {i}")
        else:
            print(f"  validateForm() would say: all checks passed.")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
