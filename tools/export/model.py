#!/usr/bin/env python3
"""Normalized in-memory model of a form, shared by every exporter.

Loads `repo/forms/<ID>/schema.json` (+ `fill_geometry.json`) and resolves the raw
schema into a vendor-neutral shape: a canonical type, a data-binding role/key
derived from `fill_strategy.source`, grouped choice options, and human-readable
conditional / computed-field logic. Exporters render this — they never re-parse
the schema. Not legal advice.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field as dc_field

# --- canonical type system (every exporter maps from these, not from raw) ------
# Derived primarily from data_type, then type. The vendor type tables in the
# exporters key on these names, so adding a vendor is a table edit, not logic.
STRING, DATE, CURRENCY, NUMBER, EMAIL, PHONE = \
    "string", "date", "currency", "number", "email", "phone"
BOOLEAN, CHOICE, CHOICE_MULTI, SIGNATURE, ADDRESS, PERSON = \
    "boolean", "choice", "choice_multi", "signature", "address", "person"

_BY_DATA_TYPE = {
    "entity_name": STRING, "person_name": PERSON, "docket_number": STRING,
    "bar_number": STRING, "text": STRING, "date": DATE, "address": ADDRESS,
    "checkbox": BOOLEAN, "signature": SIGNATURE, "phone": PHONE,
    "email": EMAIL, "currency": CURRENCY,
}
_BY_TYPE = {"select_one": CHOICE, "select_many": CHOICE_MULTI, "enabler": BOOLEAN,
            "date": DATE, "currency": CURRENCY, "text": STRING}

# fill_strategy.source prefixes that are NOT mergeable data.
_NON_DATA_SOURCES = {
    "human_decision": "manual", "wet_ink": "signature", "left_blank": "manual",
    "llm_over_narrative": "narrative", "recompute_from_dependencies": "computed",
    "triage": "routing",
}


def canonical_type(f: dict) -> str:
    dt = f.get("data_type")
    if dt in _BY_DATA_TYPE:
        # a select_one with a checkbox data_type is still a choice if it groups.
        t = _BY_TYPE.get(f.get("type"))
        if t in (CHOICE, CHOICE_MULTI) and dt == "checkbox":
            return t
        return _BY_DATA_TYPE[dt]
    return _BY_TYPE.get(f.get("type"), STRING)


def binding(f: dict) -> dict:
    """Resolve fill_strategy.source into a data binding. Returns a dict with
    `kind` (data|computed|narrative|manual|signature|routing) and, for data,
    `role` + `key` + a flat `token`."""
    fs = f.get("fill_strategy", {}) or {}
    src = fs.get("source") or ""
    head = src.split(".", 1)[0]
    if head in _NON_DATA_SOURCES:
        kind = _NON_DATA_SOURCES[head]
        if f.get("data_type") == "signature" or kind == "signature":
            kind = "signature"
        return {"kind": kind, "role": None, "key": None, "token": None}
    attr = src.split(".", 1)[1] if "." in src else f["field_id"]
    if head == "case_dict":
        role, token = "matter", attr
    elif head.endswith("_record"):
        role = head[:-len("_record")]
        token = f"{role}_{attr}"
    else:
        role, token = head or "matter", attr
    return {"kind": "data", "role": role, "key": attr, "token": token}


def translate_condition(cond) -> str | None:
    """{'all_of'|'any_of': [{'field':id,'equals':v}, ...]} -> readable expression."""
    if not isinstance(cond, dict):
        return None
    for combinator, joiner in (("all_of", " AND "), ("any_of", " OR ")):
        if combinator in cond:
            parts = []
            for c in cond[combinator]:
                if isinstance(c, dict) and "field" in c:
                    parts.append(f"{c['field']} == {json.dumps(c.get('equals'))}")
            inner = joiner.join(parts)
            return f"({inner})" if len(parts) > 1 else inner
    return None


def condition_refs(cond) -> list[str]:
    """Field ids referenced by a condition (for dependency wiring)."""
    out = []
    if isinstance(cond, dict):
        for combinator in ("all_of", "any_of"):
            for c in cond.get(combinator, []) or []:
                if isinstance(c, dict) and "field" in c:
                    out.append(c["field"])
    return out


def translate_formula(node) -> str | None:
    """Op-tree -> infix expression string. Ops: add, sub, field, sum_slot."""
    if node is None:
        return None
    if not isinstance(node, dict):
        return json.dumps(node)
    op = node.get("op")
    if op == "field":
        return node.get("id", "?")
    if op in ("add", "sub"):
        sep = " + " if op == "add" else " - "
        return "(" + sep.join(translate_formula(a) for a in node.get("args", [])) + ")"
    if op == "sum_slot":
        return f"SUM({node.get('slot_group', node.get('id', 'rows'))})"
    return json.dumps(node)


@dataclass
class Form:
    form_id: str
    title: str
    category: str
    source_url: str
    page_size: list
    n_pages: int
    fields: list = dc_field(default_factory=list)   # enriched field dicts
    geometry: dict = dc_field(default_factory=dict)  # field_id -> [{page,rect}]

    def choice_groups(self) -> dict:
        """group_id -> [{field_id, value, label}] for select_one/many options."""
        groups: dict = {}
        for f in self.fields:
            g = f.get("choice_group")
            if g:
                groups.setdefault(g, []).append(
                    {"field_id": f["field_id"], "value": f.get("choice_value"),
                     "label": f.get("label")})
        return groups


def load_form(form_id: str, root: pathlib.Path) -> Form:
    base = pathlib.Path(root) / "repo" / "forms" / form_id
    schema = json.loads((base / "schema.json").read_text())
    meta = json.loads((base / "metadata.json").read_text())
    geom_raw, page_size, n_pages = {}, [612, 792], 1
    gpath = base / "fill_geometry.json"
    if gpath.exists():
        g = json.loads(gpath.read_text())
        page_size = g.get("page_size", page_size)
        n_pages = g.get("n_pages", n_pages)
        for fid, ent in (g.get("fields") or {}).items():
            geom_raw[fid] = ent.get("widgets", [])
    enriched = []
    for f in schema["fields"]:
        f = dict(f)
        f["_ctype"] = canonical_type(f)
        f["_binding"] = binding(f)
        f["_writable_expr"] = translate_condition(f.get("writable_when"))
        f["_required_expr"] = translate_condition(f.get("required_when"))
        f["_formula_expr"] = translate_formula(f.get("formula"))
        f["_widgets"] = geom_raw.get(f["field_id"], [])
        enriched.append(f)
    return Form(
        form_id=form_id,
        title=meta.get("title", schema.get("form_id", form_id)),
        category=meta.get("category", schema.get("by_category", "")),
        source_url=meta.get("source_url", schema.get("source_url", "")),
        page_size=page_size, n_pages=n_pages,
        fields=enriched, geometry=geom_raw,
    )
