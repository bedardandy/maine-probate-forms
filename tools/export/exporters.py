#!/usr/bin/env python3
"""Reference exporters: render a normalized Form (model.py) into the import
artifacts of each templating paradigm. Each function returns {filename: text}.

The core stays vendor-neutral; per-vendor specifics are TYPE TABLES, not logic,
so adding a system is a table edit. Coordinate-placement payloads (DocuSign,
PandaDoc) are shaped for those APIs but assume a top-left point origin — verify
against the current API version before production use. Not legal advice.
"""
from __future__ import annotations

import csv
import io
import json

from . import model as M

# --- per-paradigm type tables, keyed on the canonical types in model.py --------
_JSONSCHEMA = {
    M.STRING: {"type": "string"}, M.PERSON: {"type": "string"},
    M.ADDRESS: {"type": "string"}, M.DATE: {"type": "string", "format": "date"},
    M.EMAIL: {"type": "string", "format": "email"}, M.PHONE: {"type": "string"},
    M.CURRENCY: {"type": "number"}, M.NUMBER: {"type": "number"},
    M.BOOLEAN: {"type": "boolean"},
}
_DOCUSIGN_TAB = {M.SIGNATURE: "signHereTabs", M.BOOLEAN: "checkboxTabs",
                 M.DATE: "dateTabs"}  # everything else -> textTabs
_PANDADOC = {M.SIGNATURE: "signature", M.BOOLEAN: "checkbox", M.DATE: "date",
             M.CHOICE: "dropdown", M.CHOICE_MULTI: "dropdown"}  # else "text"
_HOTDOCS = {M.STRING: "Text", M.PERSON: "Text", M.ADDRESS: "Text",
            M.DATE: "Date", M.EMAIL: "Text", M.PHONE: "Text",
            M.CURRENCY: "Number", M.NUMBER: "Number", M.BOOLEAN: "True/False",
            M.CHOICE: "Multiple Choice", M.CHOICE_MULTI: "Multiple Choice",
            M.SIGNATURE: "Text"}
_GAVEL = {M.STRING: "text", M.PERSON: "text", M.ADDRESS: "text", M.DATE: "date",
          M.EMAIL: "text", M.PHONE: "text", M.CURRENCY: "number",
          M.NUMBER: "number", M.BOOLEAN: "checkbox", M.CHOICE: "multiple choice",
          M.CHOICE_MULTI: "multiple choice", M.SIGNATURE: "text"}
_CLIO = {M.STRING: "text_line", M.PERSON: "text_line", M.ADDRESS: "text_area",
         M.DATE: "date", M.EMAIL: "text_line", M.PHONE: "text_line",
         M.CURRENCY: "currency", M.NUMBER: "numeric", M.BOOLEAN: "checkbox",
         M.CHOICE: "picklist", M.CHOICE_MULTI: "picklist", M.SIGNATURE: "text_line"}


def _data_fields(form):
    """Fields that carry mergeable data (exclude pure signatures/manual)."""
    return [f for f in form.fields if f["_binding"]["kind"] in
            ("data", "computed", "narrative")]


# =================================================================== interchange
def export_interchange(form: M.Form) -> dict:
    out = {}

    # 1. XFDF form-data template (keyed by field_id; matches the AcroForm that
    #    fill_pdf names by field_id). Empty values = a fillable template.
    rows = "\n".join(
        f'    <field name="{f["field_id"]}"><value></value></field>'
        for f in form.fields if f["_binding"]["kind"] != "signature")
    out["template.xfdf"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">\n'
        f'  <f href="{form.form_id}.pdf"/>\n  <fields>\n{rows}\n  </fields>\n</xfdf>\n')

    # 2. CSV data dictionary (the integration contract, flattened).
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["field_id", "label", "canonical_type", "data_type", "role",
                "case_key", "merge_token", "binding_kind", "required_when",
                "writable_when", "formula", "choice_group", "risk_tier",
                "fill_source"])
    for f in form.fields:
        b = f["_binding"]
        w.writerow([f["field_id"], f.get("label", ""), f["_ctype"],
                    f.get("data_type", ""), b["role"] or "", b["key"] or "",
                    b["token"] or "", b["kind"], f.get("_required_expr") or "",
                    f.get("_writable_expr") or "", f.get("_formula_expr") or "",
                    f.get("choice_group") or "", f.get("risk_tier", ""),
                    (f.get("fill_strategy") or {}).get("source", "")])
    out["data_dictionary.csv"] = buf.getvalue()

    # 3. JSON Schema for the case-data object (validate inbound data / ETL).
    groups = form.choice_groups()
    props, required, allof = {}, [], []
    seen = set()
    for f in _data_fields(form):
        b = f["_binding"]
        # data fields carry a case-object token; narrative/computed fields have no
        # case key (token is None) -> key them by field_id so they appear as real,
        # distinct properties instead of collapsing into one "null" property.
        key = b["token"] or f["field_id"]
        if key in seen:
            continue
        seen.add(key)
        ct = f["_ctype"]
        if ct in (M.CHOICE, M.CHOICE_MULTI):
            opts = [o["value"] or o["label"] for o in groups.get(f.get("choice_group"), [])]
            opts = [o for o in opts if o is not None] or None
            base = {"type": "string"}
            if opts:
                base = {"type": "string", "enum": opts}
            if ct == M.CHOICE_MULTI:
                base = {"type": "array", "items": base}
            props[key] = base
        else:
            props[key] = dict(_JSONSCHEMA.get(ct, {"type": "string"}))
        if f.get("label"):
            props[key]["description"] = f["label"]
        # computed fields are derived (formula), not inbound — flag, don't require.
        if b["kind"] == "computed":
            props[key]["readOnly"] = True
        # unconditional, non-narrative data fields are required inputs.
        if not f.get("_required_expr") and not f.get("_writable_expr") \
                and b["kind"] == "data":
            required.append(key)
        # conditional requirement -> JSON Schema if/then.
        rw = f.get("required_when")
        if isinstance(rw, dict) and "all_of" in rw:
            cond = {c["field"]: {"const": c.get("equals")}
                    for c in rw["all_of"] if "field" in c}
            if cond and key:
                allof.append({"if": {"properties": cond,
                                     "required": list(cond)},
                              "then": {"required": [key]}})
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "title": f"{form.form_id} — {form.title}", "type": "object",
              "properties": props}
    if required:
        schema["required"] = sorted(set(required))
    if allof:
        schema["allOf"] = allof
    out["case_schema.json"] = json.dumps(schema, indent=2) + "\n"
    return out


# ========================================================= coordinate placement
def _rect_to_box(rect, page_h):
    # schema rects are [x0,y0,x1,y1] in points, top-left origin (y down).
    x0, y0, x1, y1 = rect
    return {"x": round(x0), "y": round(y0),
            "w": round(x1 - x0), "h": round(abs(y1 - y0))}


def export_esign(form: M.Form) -> dict:
    out = {}
    page_h = form.page_size[1] if len(form.page_size) > 1 else 792

    # DocuSign template (one filer signer; tabs placed by page+xy).
    tabs: dict = {}
    for f in form.fields:
        for wdg in f["_widgets"]:
            box = _rect_to_box(wdg["rect"], page_h)
            tab_type = _DOCUSIGN_TAB.get(f["_ctype"], "textTabs")
            t = {"documentId": "1", "pageNumber": wdg.get("page", 0) + 1,
                 "xPosition": str(box["x"]), "yPosition": str(box["y"]),
                 "tabLabel": f["field_id"], "name": f.get("label", "")}
            if tab_type == "textTabs":
                t["width"] = box["w"]; t["height"] = box["h"]
                t["locked"] = "false" if f["_binding"]["kind"] != "computed" else "true"
            tabs.setdefault(tab_type, []).append(t)
    docusign = {"emailSubject": f"Please complete {form.form_id} — {form.title}",
                "documents": [{"documentId": "1", "name": f"{form.form_id}.pdf",
                               "fileExtension": "pdf"}],
                "recipients": {"signers": [{"recipientId": "1", "roleName": "Filer",
                                            "routingOrder": "1", "tabs": tabs}]},
                "status": "created"}
    out["docusign_template.json"] = json.dumps(docusign, indent=2) + "\n"

    # PandaDoc fields payload (uploaded-PDF field mapping; merge_field=field_id).
    pd_fields = []
    for f in form.fields:
        ptype = _PANDADOC.get(f["_ctype"], "text")
        role = "signer" if f["_binding"]["kind"] == "signature" else "filer"
        for wdg in f["_widgets"]:
            box = _rect_to_box(wdg["rect"], page_h)
            pd_fields.append({"uuid": f["field_id"], "name": f["field_id"],
                              "title": f.get("label", ""), "type": ptype,
                              "assigned_to": {"role": role},
                              "merge_field": f["field_id"],
                              "page": wdg.get("page", 0),
                              "x": box["x"], "y": box["y"],
                              "width": box["w"], "height": box["h"]})
    out["pandadoc_fields.json"] = json.dumps(
        {"name": f"{form.form_id} — {form.title}",
         "roles": [{"name": "filer"}, {"name": "signer"}],
         "fields": pd_fields}, indent=2) + "\n"
    return out


# ============================================================= doc assembly =====
def _variable_manifest(form: M.Form) -> list:
    groups = form.choice_groups()
    seen, vars_ = set(), []
    for f in form.fields:
        b = f["_binding"]
        if b["kind"] in ("manual", "signature", "routing"):
            continue
        token = b["token"] or f["field_id"]
        if token in seen:
            continue
        seen.add(token)
        ct = f["_ctype"]
        v = {"name": token, "label": f.get("label", ""), "type": ct,
             "role": b["role"], "field_id": f["field_id"],
             "required": not f.get("_required_expr") and not f.get("_writable_expr")
                          and b["kind"] == "data",
             "show_when": f.get("_writable_expr"),
             "required_when": f.get("_required_expr"),
             "computed": b["kind"] == "computed",
             "formula": f.get("_formula_expr"),
             "narrative": b["kind"] == "narrative"}
        if ct in (M.CHOICE, M.CHOICE_MULTI):
            v["options"] = [o["value"] or o["label"]
                            for o in groups.get(f.get("choice_group"), [])
                            if (o["value"] or o["label"])]
        vars_.append(v)
    return vars_


def export_docassembly(form: M.Form) -> dict:
    out = {}
    vars_ = _variable_manifest(form)
    out["variables.json"] = json.dumps(
        {"form_id": form.form_id, "title": form.title, "variables": vars_},
        indent=2) + "\n"

    # Per-vendor merge-token / variable-type map (Clio, MyCase, HotDocs, Gavel).
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["variable", "label", "canonical_type", "clio_custom_field_type",
                "clio_merge_token", "mycase_merge_token", "hotdocs_var",
                "hotdocs_type", "gavel_type", "computed", "show_when"])
    for v in vars_:
        ct = v["type"]
        token = v["name"]
        # Suggested merge-token syntax (verify exact namespace in your matter).
        clio = "{{Matter.Custom." + token + "}}"
        mycase = "[[" + token + "]]"
        w.writerow([token, v["label"], ct, _CLIO.get(ct, "text_line"), clio,
                    mycase, token, _HOTDOCS.get(ct, "Text"), _GAVEL.get(ct, "text"),
                    "yes" if v["computed"] else "", v["show_when"] or ""])
    out["merge_tokens.csv"] = buf.getvalue()

    # Human-readable logic (conditional visibility + computed fields) — the part
    # doc-assembly engines can actually enforce.
    lines = [f"# {form.form_id} — conditional & computed logic", ""]
    shows = [v for v in vars_ if v["show_when"]]
    reqs = [v for v in vars_ if v["required_when"]]
    comps = [v for v in vars_ if v["computed"] and v["formula"]]
    lines.append("## Show / enable only when")
    lines += [f"- `{v['name']}`  ⟸  {v['show_when']}" for v in shows] or ["- (none)"]
    lines += ["", "## Required only when"]
    lines += [f"- `{v['name']}`  ⟸  {v['required_when']}" for v in reqs] or ["- (none)"]
    lines += ["", "## Computed"]
    lines += [f"- `{v['name']}` = {v['formula']}" for v in comps] or ["- (none)"]
    out["logic.md"] = "\n".join(lines) + "\n"
    return out


# ============================================================ Gavel / Documate ==
def export_gavel(form: M.Form) -> dict:
    """Gavel (formerly Documate/Afterpattern) variable manifest: typed variables
    + show-if logic + computations, in a Gavel-shaped JSON."""
    vars_ = _variable_manifest(form)
    gv = []
    for v in vars_:
        entry = {"name": v["name"], "label": v["label"],
                 "type": _GAVEL.get(v["type"], "text")}
        if v.get("options"):
            entry["choices"] = v["options"]
        if v["show_when"]:
            entry["show_if"] = v["show_when"]
        if v["computed"] and v["formula"]:
            entry["calculation"] = v["formula"]
        if v["required"]:
            entry["required"] = True
        if v["narrative"]:
            entry["multiline"] = True
        gv.append(entry)
    payload = {"interview": {"name": f"{form.form_id} — {form.title}",
                             "source_form": form.source_url},
               "variables": gv}
    return {"gavel_variables.json": json.dumps(payload, indent=2) + "\n"}


PARADIGMS = {
    "interchange": export_interchange,
    "esign": export_esign,
    "docassembly": export_docassembly,
    "gavel": export_gavel,
}
