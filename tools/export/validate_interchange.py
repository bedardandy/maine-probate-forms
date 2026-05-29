#!/usr/bin/env python3
"""Validate the interchange export (XFDF / CSV / JSON Schema) — no account needed.

Three checks, the cheapest and highest-reuse part of the export layer:

  1. STRUCTURAL  — template.xfdf is well-formed XFDF (correct namespace, <f href>,
                   one <field name=...> per data field).
  2. NAME CONTRACT — the field set is IDENTICAL across all three interchange
                   artifacts (XFDF field names == data_dictionary.csv field_ids ==
                   case_schema.json properties == the model's data fields). This is
                   the drift that silently breaks every downstream paradigm, since
                   they all key on field_id.
  3. ROUND-TRIP  — build an AcroForm PDF whose widgets are named by field_id (what
                   an integrator does with our geometry), import a *populated* XFDF
                   into it by name, reopen, and confirm each value landed on the
                   right field. Proves the XFDF actually drives a fielded PDF.

The round-trip is offline by default (synthetic pages sized from fill_geometry, so
rects stay in bounds); --fetch uses the real blank from source_url instead. Visual
placement on the real form is what the DocuSign sandbox test covers — this proves
the data plumbing.

    python3 -m tools.export.validate_interchange            # all forms + examples
    python3 -m tools.export.validate_interchange --form DE-101 --fetch
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import os
import pathlib
import sys
import urllib.request
import xml.etree.ElementTree as ET

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from tools.export import model as M           # noqa: E402
from tools.export import exporters as X       # noqa: E402
from tools import fill_plan                   # noqa: E402

XFDF_NS = "http://ns.adobe.com/xfdf/"
UA = {"User-Agent": "Mozilla/5.0 (maine-probate-forms-oss interchange validator)"}


def _xfdf_field_names(xfdf_text: str):
    """Parse template.xfdf, return (root_ok, href, [field names]) or raise."""
    root = ET.fromstring(xfdf_text)
    if root.tag != f"{{{XFDF_NS}}}xfdf":
        raise ValueError(f"root is {root.tag}, expected xfdf in {XFDF_NS}")
    href = None
    f = root.find(f"{{{XFDF_NS}}}f")
    if f is not None:
        href = f.get("href")
    names = [fl.get("name") for fl in root.iter(f"{{{XFDF_NS}}}field")]
    return href, names


def _build_populated_xfdf(form, resolved):
    """Mirror the exporter's template, but with resolved values filled in. This is
    the artifact an integrator would POST/import after merging case data."""
    rows = []
    for f in X._data_fields(form):
        fid = f["field_id"]
        val = resolved.get(fid, "")
        val = "" if val is None else str(val)
        # XML-escape the value
        val = (val.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        rows.append(f'    <field name="{fid}"><value>{val}</value></field>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<xfdf xmlns="{XFDF_NS}" xml:space="preserve">\n'
            f'  <f href="{form.form_id}.pdf"/>\n  <fields>\n'
            + "\n".join(rows) + "\n  </fields>\n</xfdf>\n")


def _acroform_from_geometry(form, source_bytes=None):
    """Build a PDF whose AcroForm text widgets are named by field_id at the geometry
    rects — what fill-by-name import (XFDF/FDF) targets. Synthetic pages unless a
    real source PDF (as bytes) is supplied."""
    import fitz
    w, h = (form.page_size + [612, 792])[:2]
    if source_bytes:
        doc = fitz.open(stream=source_bytes, filetype="pdf")
    else:
        doc = fitz.open()
        for _ in range(max(form.n_pages, 1)):
            doc.new_page(width=w, height=h)
    placed = 0
    for f in X._data_fields(form):
        fid = f["field_id"]
        for wdg in f["_widgets"]:
            pno = wdg.get("page", 0)
            if pno >= doc.page_count:
                continue
            x0, y0, x1, y1 = wdg["rect"]
            widget = fitz.Widget()
            widget.field_name = fid
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.rect = fitz.Rect(x0, y0, x1, y1)
            widget.field_value = ""
            doc[pno].add_widget(widget)
            placed += 1
            break  # one widget per field is enough for the data round-trip
    return doc, placed


def _import_xfdf_by_name(doc, xfdf_text):
    """Apply a populated XFDF to the AcroForm by matching field name -> widget."""
    root = ET.fromstring(xfdf_text)
    values = {}
    for fl in root.iter(f"{{{XFDF_NS}}}field"):
        v = fl.find(f"{{{XFDF_NS}}}value")
        values[fl.get("name")] = (v.text or "") if v is not None else ""
    applied = 0
    for page in doc:
        for wdg in page.widgets() or []:
            if wdg.field_name in values:
                wdg.field_value = values[wdg.field_name]
                wdg.update()
                applied += 1
    return applied


def validate_form(form_id, root, do_roundtrip, fetch):
    form = M.load_form(form_id, pathlib.Path(root))
    arts = X.export_interchange(form)
    problems = []

    # --- 1. structural
    href, xfdf_names = _xfdf_field_names(arts["template.xfdf"])
    if href != f"{form_id}.pdf":
        problems.append(f"xfdf <f href> = {href!r}, expected {form_id}.pdf")
    if any(n is None for n in xfdf_names):
        problems.append("xfdf has a <field> with no name")

    # --- 2. name contract. The three artifacts are keyed DELIBERATELY differently;
    # assert each against its own contract, and that they tie together.
    all_ids = [f["field_id"] for f in form.fields]
    xfdf_expected = {f["field_id"] for f in form.fields
                     if f["_binding"]["kind"] != "signature"}        # all but signatures
    dd_ids = [r["field_id"] for r in
              csv.DictReader(io.StringIO(arts["data_dictionary.csv"]))]
    cs = json.loads(arts["case_schema.json"])
    cs_props = list(cs.get("properties", {}).keys())
    # case_schema is keyed by binding token (or field_id when there's no case key),
    # deduped, over the mergeable data fields.
    cs_expected, _seen = [], set()
    for f in X._data_fields(form):
        k = f["_binding"]["token"] or f["field_id"]
        if k not in _seen:
            _seen.add(k); cs_expected.append(k)

    if set(xfdf_names) != xfdf_expected:
        problems.append(f"xfdf names != (fields minus signatures): "
                        f"+{sorted(set(xfdf_names) - xfdf_expected)[:5]} "
                        f"-{sorted(xfdf_expected - set(xfdf_names))[:5]}")
    if set(dd_ids) != set(all_ids):
        problems.append(f"csv field_id column != all fields: "
                        f"+{sorted(set(dd_ids) - set(all_ids))[:5]} "
                        f"-{sorted(set(all_ids) - set(dd_ids))[:5]}")
    if set(cs_props) != set(cs_expected):
        problems.append(f"case_schema properties != data-field tokens: "
                        f"+{sorted(set(cs_props) - set(cs_expected))[:5]} "
                        f"-{sorted(set(cs_expected) - set(cs_props))[:5]}")
    # regression guards
    if "null" in cs_props or None in cs.get("properties", {}):
        problems.append("case_schema has a 'null' property (a field bound to no key)")
    if set(xfdf_names) - set(all_ids):
        problems.append(f"xfdf references non-field ids: "
                        f"{sorted(set(xfdf_names) - set(all_ids))[:5]}")
    # duplicates within an artifact
    for k, lst in {"xfdf": xfdf_names, "csv": dd_ids, "case_schema": cs_props}.items():
        if len(lst) != len(set(lst)):
            problems.append(f"{k} has duplicate field names")
    expected = cs_expected  # for the summary count

    rt = None
    if do_roundtrip:
        rt = roundtrip_form(form, root, fetch)
        if rt and rt.get("error"):
            problems.append(f"round-trip: {rt['error']}")
        elif rt and rt["mismatched"]:
            problems.append(f"round-trip: {len(rt['mismatched'])} value(s) did not "
                            f"survive: {rt['mismatched'][:5]}")
    return {"form": form_id, "n_fields": len(expected),
            "problems": problems, "roundtrip": rt}


def roundtrip_form(form, root, fetch):
    base = pathlib.Path(root) / "repo" / "forms" / form.form_id / "examples"
    case_path = base / "case.example.json"
    if not case_path.exists():
        return None
    case = json.loads(case_path.read_text())
    plan = fill_plan.build_plan(form.form_id, case, pathlib.Path(root))
    if not plan.get("ok", True) and "resolved" not in plan:
        return {"error": plan.get("error", "build_plan failed")}
    resolved = plan["resolved"]
    if not resolved:
        return {"error": "example resolved 0 fields"}

    src_bytes = None
    if fetch:
        try:
            req = urllib.request.Request(form.source_url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                src_bytes = r.read()
        except Exception as e:                       # noqa: BLE001
            return {"error": f"fetch failed ({type(e).__name__}); rerun without --fetch"}

    import fitz
    doc, placed = _acroform_from_geometry(form, source_bytes=src_bytes)
    xfdf = _build_populated_xfdf(form, resolved)
    _import_xfdf_by_name(doc, xfdf)
    blob = doc.tobytes()
    doc.close()

    # reopen, read back
    doc2 = fitz.open(stream=blob, filetype="pdf")
    got = {}
    for page in doc2:
        for wdg in page.widgets() or []:
            got[wdg.field_name] = wdg.field_value
    doc2.close()

    checked = mismatched = 0
    miss = []
    for fid, val in resolved.items():
        if fid not in got:
            continue  # field has no placeable geometry — not an interchange defect
        checked += 1
        want = "" if val is None else str(val)
        if (got[fid] or "") != want:
            mismatched += 1
            miss.append(fid)
    return {"placed": placed, "checked": checked,
            "mismatched": miss, "ok": mismatched == 0 and checked > 0}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", help="single form id (default: all)")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--no-roundtrip", action="store_true",
                    help="structural + name-contract only (no PDF build)")
    ap.add_argument("--fetch", action="store_true",
                    help="round-trip against the real blank from source_url")
    a = ap.parse_args()

    if a.form:
        forms = [a.form]
    else:
        forms = sorted(os.path.basename(os.path.dirname(p))
                       for p in glob.glob(f"{a.root}/repo/forms/*/schema.json"))

    fail = 0
    rt_ran = rt_ok = 0
    for fid in forms:
        r = validate_form(fid, a.root, not a.no_roundtrip, a.fetch)
        rt = r["roundtrip"]
        if rt and not rt.get("error"):
            rt_ran += 1
            if rt.get("ok"):
                rt_ok += 1
        if r["problems"]:
            fail += 1
            print(f"FAIL {fid} ({r['n_fields']} fields)")
            for p in r["problems"]:
                print(f"      - {p}")
        elif a.form:
            tail = ""
            if rt and not rt.get("error"):
                tail = f"  round-trip {rt['checked']}/{rt['checked']} values intact"
            print(f"OK   {fid} ({r['n_fields']} fields){tail}")

    print(f"\n{len(forms)} forms: {len(forms) - fail} clean, {fail} with problems.")
    if rt_ran:
        print(f"round-trip exercised on {rt_ran} example form(s): {rt_ok} fully intact.")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
