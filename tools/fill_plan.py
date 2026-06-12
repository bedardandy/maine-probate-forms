#!/usr/bin/env python3
"""Resolve a probate form's schema against a case object into a *fill plan*.

Every probate schema field carries `fill_strategy.source`. This walks them and
sorts each field into one of:

  * resolved   — `case_dict.*` / `*_record.*` sources looked up from the case
                 object (and any `llm_over_narrative` field the agent already
                 pre-filled under `narrative_facts[field_id]`).
  * narrative  — `llm_over_narrative` fields the *agent* should compose from the
                 fact pattern (label + prompt + data_type given as the worklist).
  * recompute  — `recompute_from_dependencies` / formula fields (derived).
  * blank      — `wet_ink` / `human_decision` / `left_blank` / `triage` and
                 anything flagged `human_required` (signatures, elections).
  * unresolved — a `case_dict.*` / `*_record.*` source with no value supplied
                 (these become the "missing facts" to collect).
  * skipped    — a field whose schema `when` condition evaluates False against
                 the known facts (e.g. an "Other:" write-in when the selection
                 is not "other"); not applicable to this case, so not filled.

A field is gated off only when its controlling field is *known* and the
condition is definitively false; an unknown controller leaves the field in its
normal bucket (the conservative choice for legal forms).

No PDF is needed — this works entirely off the shipped schema. See
docs/agent-workflow.md for how the plan feeds back into a filled document.

    python3 tools/fill_plan.py --form DE-101 --case case.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
_BLANK_SOURCES = {"wet_ink", "human_decision", "left_blank", "triage"}

# Suffixes that mark a field as a non-name attribute (address, phone, the "Other:"
# write-in, etc.). Many schema fields share a record's bare source key
# (`attorney_record.attorney`) and rely on a field_id-keyed value in the record.
# When that value is absent we must NOT fall back to the bare key — that key holds
# the entity *name*, so an attribute/write-in field would otherwise be stamped
# with the person's name (e.g. the applicant's name landing on the "Other:" line).
_NON_NAME_SUFFIXES = (
    "_address", "_phone", "_phone_number", "_email", "_email_address",
    "_bar_number", "_bar_no", "_city", "_state", "_zip", "_county",
    "_residence", "_domicile", "_day", "_month", "_year", "_title",
    "_other", "_other_text",
    # Date fields share a record's bare name source (e.g. DE-101
    # `decedent_date_of_birth` <- `decedent_record.decedent`); without these a
    # missing date would fall back to the entity name landing in a date blank.
    "_date_of_birth", "_date_of_death", "_dob", "_date",
)


def _lookup(case: dict, source: str, field_id: str):
    """Resolve a `<record>.<key>` source against the case object.

    Probate's `source` names the *record* (`attorney_record.attorney`); the value
    for a specific field lives under `field_id` within that record (the record's
    keys mirror field_ids — `attorney_name`, `attorney_address`, ...). The bare
    source key holds the entity's name, used for the `<role>_full_name` field.
    Tries field_id first; falls back to the bare source key only for name-bearing
    fields (an attribute/write-in field must not inherit the entity name). None if
    absent/empty.
    """
    if "." not in source:
        return None
    record, key = source.split(".", 1)
    rec = case.get(record)
    if not isinstance(rec, dict):
        return None
    val = rec.get(field_id)
    if val not in (None, ""):
        return val
    if "." in key:
        # Two-dot source (`<record>.<role>.<attr>`, e.g.
        # `case_dict.conservator.phone`, `petitioner_record.petitioner.address`):
        # no record carries a literal "role.attr" key, so rewrite to the flat
        # `<role>_<attr>` convention and try the named record first, then the
        # role's own `<role>_record` (where canonical_adapter puts attributes).
        role, attr = key.split(".", 1)
        flat = f"{role}_{attr.replace('.', '_')}"
        role_rec = case.get(f"{role}_record")
        for r2 in (rec, role_rec):
            if isinstance(r2, dict):
                v = r2.get(flat)
                if v not in (None, ""):
                    return v
        if attr == "name_and_address":      # composite: build from the parts
            r2 = role_rec if isinstance(role_rec, dict) else rec
            nm = r2.get(f"{role}_name") or r2.get(role)
            ad = r2.get(f"{role}_address")
            if nm and ad:
                return f"{nm}, {ad}"
            return nm or ad or None
        return None
    name_bearing = "name" in field_id or "caption" in field_id
    if field_id.endswith(_NON_NAME_SUFFIXES) and not name_bearing:
        return None
    val = rec.get(key)
    return val if val not in (None, "") else None


_TRUTHY = {"yes", "true", "1", "on", "y", "checked"}
_FALSY = {"no", "false", "0", "off", "n", "", "none", "unchecked"}


def _render_value(val):
    """Coerce a resolved source value to display text for a text widget.

    A deterministic `<record>.<key>` source can hold structured data — e.g.
    DE-101's `minor_record.minor_children` is `[{name, dob}, ...]`. Without this,
    fill writes the raw Python repr (`[{'name': 'Aiden M. Reyes', ...}]`) onto the
    form. Render lists/dicts as readable text: each item's values joined by ", ",
    items by "; ". Scalars/strings pass through unchanged.
    """
    if isinstance(val, str) or val is None:
        return val
    if isinstance(val, bool):
        return "yes" if val else "no"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, dict):
        return ", ".join(str(v).strip() for v in val.values()
                         if v not in (None, ""))
    if isinstance(val, (list, tuple, set)):
        parts = []
        for item in val:
            rendered = _render_value(item)
            if rendered not in (None, ""):
                parts.append(rendered)
        return "; ".join(parts)
    return str(val)


def _rescue_from_records(case: dict, field_id: str):
    """A `llm_over_narrative` field whose value is a hard fact the case already
    supplies in a `*_record` should resolve deterministically, not be deferred to
    the LLM (e.g. DE-101 `decedent_date_of_death` lives in `decedent_record`).

    Conservative by construction: matches the EXACT field_id as a key in some
    `*_record` only. No bare-key / name fallback (so an attribute field never
    inherits an entity name) and no role-prefix guessing — repeating-group fields
    (`heir_3_address`, no `heir_3_record`) and composite phrase fields
    (`residence_and_date_of_death`) have no matching key and stay narrative.
    Returns the value, or None when no record carries it.
    """
    for key, rec in case.items():
        if key.endswith("_record") and isinstance(rec, dict):
            v = rec.get(field_id)
            if v not in (None, ""):
                return v
    return None


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) > 0
    s = str(v).strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return False
    return bool(s)


def _unquote(tok: str) -> str:
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] in "'\"" and tok[-1] == tok[0]:
        return tok[1:-1]
    return tok


def _scalar_eq(val, token: str) -> bool:
    """Compare a resolved value to a `when` literal, with yes/no/bool coercion.

    A select_many value (list) matches if any element matches.
    """
    if isinstance(val, (list, tuple, set)):
        return any(_scalar_eq(x, token) for x in val)
    t = token.strip().lower()
    if t in ("true", "false", "yes", "no"):
        return _truthy(val) == (t in ("true", "yes"))
    return str(val).strip().lower() == t


def eval_when(expr: str, values: dict):
    """Evaluate a tree `when` expression against known facts.

    Returns True (field applies), False (gated off — skip it), or None (the
    controlling field is unknown, so leave the field in its normal bucket).
    Grammar (all that the trees use): `LHS == 'v'`, `LHS != 'v'`,
    `LHS == true/false`, `LHS in ['a', 'b']`, and bare `LHS` (truthy).
    """
    expr = (expr or "").strip()
    m = re.match(r"^([A-Za-z0-9_]+)\s+in\s+\[(.*)\]$", expr)
    if m:
        lhs, items = m.group(1), m.group(2)
        if values.get(lhs) in (None, ""):
            return None
        opts = [_unquote(x).lower() for x in items.split(",") if x.strip()]
        v = values[lhs]
        if isinstance(v, (list, tuple, set)):
            return any(str(x).strip().lower() in opts for x in v)
        return str(v).strip().lower() in opts
    m = re.match(r"^([A-Za-z0-9_]+)\s*(==|!=)\s*(.+?)$", expr)
    if m:
        lhs, op, rhs = m.group(1), m.group(2), _unquote(m.group(3))
        if values.get(lhs) in (None, ""):
            return None
        hit = _scalar_eq(values[lhs], rhs)
        return hit if op == "==" else (not hit)
    m = re.match(r"^([A-Za-z0-9_]+)$", expr)
    if m:
        if values.get(expr) in (None, ""):
            return None
        return _truthy(values[expr])
    return None


def _known_values(case: dict, resolved: dict, narrative_facts: dict) -> dict:
    """All facts available to evaluate `when` controllers: case_dict + every
    `*_record` + narrative_facts, with `resolved` last (authoritative)."""
    values: dict = {}
    cd = case.get("case_dict")
    if isinstance(cd, dict):
        values.update(cd)
    for k, v in case.items():
        if k.endswith("_record") and isinstance(v, dict):
            values.update(v)
    if isinstance(narrative_facts, dict):
        values.update(narrative_facts)
    values.update(resolved)
    return values


def build_plan(form_id: str, case: dict, root: pathlib.Path = ROOT) -> dict:
    if not isinstance(case, dict):
        return {"ok": False, "error": f"case must be a JSON object (got "
                f"{type(case).__name__}); expected a case_dict/<role>_record "
                "object or a canonical {matter, parties, party, facts} object"}
    schema_path = root / "repo" / "forms" / form_id / "schema.json"
    if not schema_path.exists():
        return {"ok": False, "error": f"unknown form {form_id!r} "
                f"(no {schema_path.relative_to(root)})"}
    schema = json.loads(schema_path.read_text())
    narrative_facts = case.get("narrative_facts", {}) if isinstance(
        case.get("narrative_facts"), dict) else {}

    fields = schema.get("fields", [])
    resolved, narrative, recompute, blank, unresolved = {}, [], [], [], []
    for f in fields:
        fid = f["field_id"]
        fs = f.get("fill_strategy") or {}
        src = fs.get("source") or ""
        label = f.get("label", fid)

        if src.startswith("case_dict.") or src.split(".", 1)[0].endswith("_record"):
            val = _lookup(case, src, fid)
            if val is not None:
                resolved[fid] = _render_value(val)
            else:
                unresolved.append({"field_id": fid, "label": label,
                                   "source": src})
        elif src == "llm_over_narrative" or fs.get("llm_eligible"):
            rescued = _rescue_from_records(case, fid)
            if fid in narrative_facts and narrative_facts[fid] not in (None, ""):
                resolved[fid] = _render_value(narrative_facts[fid])
            elif rescued is not None:                 # hard fact already in a record
                resolved[fid] = _render_value(rescued)
            else:
                narrative.append({"field_id": fid, "label": label,
                                  "data_type": f.get("data_type"),
                                  "prompt": f.get("prompt") or label,
                                  "subcategory": f.get("subcategory")})
        elif src == "recompute_from_dependencies" or f.get("formula"):
            recompute.append({"field_id": fid, "label": label,
                              "formula": f.get("formula")})
        elif src in _BLANK_SOURCES or fs.get("human_required"):
            # A human decision explicitly recorded in the case wins (engine
            # doctrine: supplied values always win). Signatures stay wet-ink
            # regardless — never written from narrative facts.
            if (src != "wet_ink"
                    and fid in narrative_facts
                    and narrative_facts[fid] not in (None, "")):
                resolved[fid] = _render_value(narrative_facts[fid])
            else:
                blank.append({"field_id": fid, "label": label, "reason": src})
        else:
            unresolved.append({"field_id": fid, "label": label, "source": src})

    # Conditional gating: drop fields whose `when` is definitively false.
    skipped = []
    when_by_id = {f["field_id"]: f["when"] for f in fields if f.get("when")}
    if when_by_id:
        values = _known_values(case, resolved, narrative_facts)
        labels = {f["field_id"]: f.get("label", f["field_id"]) for f in fields}
        gated_off = {fid for fid, expr in when_by_id.items()
                     if eval_when(expr, values) is False}
        if gated_off:
            for fid in [k for k in resolved if k in gated_off]:
                resolved.pop(fid)

            def _keep(lst):
                return [x for x in lst if x["field_id"] not in gated_off]

            narrative = _keep(narrative)
            recompute = _keep(recompute)
            blank = _keep(blank)
            unresolved = _keep(unresolved)
            skipped = [{"field_id": fid, "label": labels.get(fid, fid),
                        "when": when_by_id[fid]} for fid in sorted(gated_off)]

    n = len(fields)
    return {
        "ok": True,
        "form_id": form_id,
        "title": schema.get("_skill_metadata_override", {}).get("form_title")
        or form_id,
        "n_fields": n,
        "resolved": resolved,
        "narrative": narrative,
        "recompute": recompute,
        "blank": blank,
        "unresolved": unresolved,
        "skipped": skipped,
        "coverage": {
            "resolved": len(resolved), "narrative": len(narrative),
            "recompute": len(recompute), "blank": len(blank),
            "unresolved": len(unresolved), "skipped": len(skipped),
        },
        "note": ("Not legal advice. `narrative` fields must be composed from the "
                 "fact pattern and reviewed; `unresolved` are missing facts to "
                 "collect; `skipped` fields are gated off by a `when` condition. "
                 "Write the filled PDF with tools/fill_pdf.py "
                 "(see docs/agent-workflow.md)."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True)
    ap.add_argument("--case", required=True, help="canonical or native case JSON")
    ap.add_argument("--full", action="store_true",
                    help="print the full plan (default: summary)")
    a = ap.parse_args()

    from canonical_adapter import to_case_object  # local import for CLI
    case = to_case_object(json.loads(pathlib.Path(a.case).read_text()))
    plan = build_plan(a.form, case)
    if not plan["ok"]:
        print(plan["error"]); return 1
    if a.full:
        print(json.dumps(plan, indent=2)); return 0
    c = plan["coverage"]
    print(f"{plan['form_id']}: {plan['n_fields']} fields — "
          f"resolved {c['resolved']}, narrative {c['narrative']} (agent fills), "
          f"recompute {c['recompute']}, blank {c['blank']}, "
          f"unresolved {c['unresolved']}, skipped {c['skipped']}")
    for u in plan["unresolved"][:12]:
        print(f"  missing: {u['field_id']:32} <- {u.get('source','')}")
    for s in plan["skipped"][:12]:
        print(f"  skipped: {s['field_id']:32} (when {s['when']})")
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    raise SystemExit(main())
