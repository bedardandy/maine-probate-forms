#!/usr/bin/env python3
"""Universal validator for filled probate forms.

Reads schema.json and a filled-values dict; returns violations as
{field_id, severity, code, message}.

What it checks:
  - data_type contract per field (currency, date, person_name, …)
  - data_constraints (min/max/decimals/format)
  - writable_when: rejects writes that violate conditional-writability
  - required_when: flags missing values when condition is true
  - validators[]:
      populate_from_case_dict        (informational only without case_dict)
      recompute_from_dependencies    (delegates to recompute_formulas
                                      from sibling skill.md or override)
      dedupe_within(<group>_desc)
      cross_section_dedupe(g1,g2,...)
      nonempty_if_desc

Run:
    python3 scripts/validate_filled.py \
        --schema repo/forms/DE-405/schema.json \
        --filled intermediate/fact_eval/DE-405/filled_1.json
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict


SEVERITY_ERR = "error"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

# ──────────────────────────────────────────────────────────────────────────────
# Data-type contracts.
# ──────────────────────────────────────────────────────────────────────────────
CURRENCY_RE = re.compile(r"^-?\$?\d{1,3}(,\d{3})*(\.\d{1,2})?$|^-?\$?\d+(\.\d{1,2})?$")
DATE_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})$"
)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$")
# Maine probate docket formats — many variants seen in real filings:
#   2025-0418              bare year-number
#   2025-0418-WC           year-number-type
#   2025-WC-0418           year-type-number
#   2025-CUM-PR-0418       year-county-type-number
#   2026-AND-GA-114        same shape, 3-letter county (Androscoggin)
#   KNO-2026-0184          county-year-number (older county-first form)
#   318-2024-GI-00087      district-year-type-number (district court referral)
DOCKET_ME_RE = re.compile(
    r"^(?:[A-Z0-9]{2,5}-)?"     # optional county/district prefix
    r"\d{4}-"                   # year (required)
    r"(?:[A-Z]{2,5}-)?"         # optional type or county
    r"(?:[A-Z]{2,5}-)?"         # optional secondary type
    r"\d{1,6}"                  # case number
    r"(?:-[A-Z]{2,5})?$"        # optional trailing type
)
BAR_ME_RE = re.compile(r"^\d{2,6}$")


def _typecheck(field: dict, value) -> list[dict]:
    if value in (None, ""):
        return []
    dt = field.get("data_type")
    constraints = field.get("data_constraints") or {}
    fid = field["field_id"]
    out: list[dict] = []
    s = str(value).strip()

    if dt == "currency":
        if not CURRENCY_RE.match(s):
            out.append(_v(fid, SEVERITY_ERR, "bad_currency",
                          f"value {s!r} is not a valid currency"))
        else:
            num = float(re.sub(r"[$,]", "", s))
            mn = constraints.get("min")
            mx = constraints.get("max")
            if mn is not None and num < mn:
                out.append(_v(fid, SEVERITY_ERR, "below_min",
                              f"{num} < min {mn}"))
            if mx is not None and num > mx:
                out.append(_v(fid, SEVERITY_ERR, "above_max",
                              f"{num} > max {mx}"))
    elif dt == "date":
        if not DATE_RE.match(s):
            out.append(_v(fid, SEVERITY_ERR, "bad_date",
                          f"value {s!r} is not a recognized date"))
    elif dt == "email":
        if not EMAIL_RE.match(s):
            out.append(_v(fid, SEVERITY_ERR, "bad_email", f"{s!r}"))
    elif dt == "phone":
        if not PHONE_RE.match(s):
            out.append(_v(fid, SEVERITY_ERR, "bad_phone", f"{s!r}"))
    elif dt == "docket_number":
        if constraints.get("jurisdiction") == "ME":
            if not DOCKET_ME_RE.match(s):
                out.append(_v(fid, SEVERITY_WARN, "bad_docket",
                              f"value {s!r} does not match Maine docket "
                              f"format YYYY-NNNN-XX"))
    elif dt == "bar_number":
        if constraints.get("jurisdiction") == "ME":
            if not BAR_ME_RE.match(s):
                out.append(_v(fid, SEVERITY_WARN, "bad_bar_number",
                              f"value {s!r} is not a Maine bar number"))
    # text/person_name/entity_name/address/signature: free-form, no contract
    return out


def _v(fid, sev, code, msg) -> dict:
    return {"field_id": fid, "severity": sev, "code": code, "message": msg}


# ──────────────────────────────────────────────────────────────────────────────
# Condition evaluator for writable_when / required_when (JSON boolean tree).
# Supported leaves: {"field": "...", "equals": v}
#                   {"field": "...", "ne": v}
#                   {"field": "...", "gt"|"gte"|"lt"|"lte": n}
#                   {"field": "...", "in": [v...]}
#                   {"field": "...", "exists": true|false}
# Supported branches: {"all_of": [...]}, {"any_of": [...]}, {"none_of": [...]}
# ──────────────────────────────────────────────────────────────────────────────
TRUTHY_STRINGS = {"yes", "true", "1", "x", "checked", "y", "on"}
FALSY_STRINGS = {"no", "false", "0", "n", "off", "unchecked", "none"}


def _coerce_bool(v):
    """Coerce a value to a Python bool when it represents a checkbox /
    yes-no field. Returns None when ambiguous (a non-bool, non-yes/no
    string) so the caller can fall back to direct comparison."""
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return False
    if isinstance(v, str):
        s = v.strip().lower()
        if s in TRUTHY_STRINGS: return True
        if s in FALSY_STRINGS:  return False
    return None


def _eval_cond(cond, values: dict) -> bool:
    if cond is None:
        return True
    if "all_of" in cond:
        return all(_eval_cond(c, values) for c in cond["all_of"])
    if "any_of" in cond:
        return any(_eval_cond(c, values) for c in cond["any_of"])
    if "none_of" in cond:
        return not any(_eval_cond(c, values) for c in cond["none_of"])
    fid = cond.get("field")
    v = values.get(fid) if fid else None
    if "equals" in cond:
        rhs = cond["equals"]
        # Boolean equality with truthy-string tolerance (so a checkbox
        # filled with "Yes" / "X" / "true" counts as True).
        if isinstance(rhs, bool):
            coerced = _coerce_bool(v)
            if coerced is not None:
                return coerced is rhs
        return v == rhs
    if "ne" in cond:
        rhs = cond["ne"]
        if isinstance(rhs, bool):
            coerced = _coerce_bool(v)
            if coerced is not None:
                return coerced is not rhs
        return v != rhs
    if "exists" in cond:
        present = v not in (None, "", [])
        return present is bool(cond["exists"])
    if "in" in cond:
        return v in cond["in"]
    for op in ("gt", "gte", "lt", "lte"):
        if op in cond:
            try:
                lhs = float(re.sub(r"[$,]", "", str(v))) if v not in (None, "") else None
                rhs = float(cond[op])
            except Exception:
                return False
            if lhs is None:
                return False
            if op == "gt":  return lhs > rhs
            if op == "gte": return lhs >= rhs
            if op == "lt":  return lhs < rhs
            if op == "lte": return lhs <= rhs
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Declarative-validator interpreters.
# ──────────────────────────────────────────────────────────────────────────────
DEDUPE_RE = re.compile(r"^dedupe_within\(([^)]+)\)$")
CROSS_RE = re.compile(r"^cross_section_dedupe\(([^)]+)\)$")
EQUALS_FIELD_RE = re.compile(r"^equals_field\(([^,)]+)(?:,\s*(\w+))?\)$")
VALUE_IN_RE = re.compile(r"^value_in\(([^)]+)\)$")
DATE_ORDER_RE = re.compile(r"^date_order\(([^,)]+),\s*([<>=]+)\)$")
VALUE_RANGE_RE = re.compile(
    r"^value_range\(\s*([-+]?[\d.,*]+|\*)\s*,\s*([-+]?[\d.,*]+|\*)\s*\)$"
)
REGEX_MATCH_RE = re.compile(r"^regex_match\(([a-z0-9_]+)\)$")

# Named regex patterns. Keys are referenced by `regex_match(<name>)`.
# Patterns are intentionally permissive: validators flag the obviously
# wrong, not the merely unusual.
REGEX_PATTERNS = {
    "maine_zip":         re.compile(r"^\d{5}(?:-\d{4})?$"),
    "us_phone":          re.compile(r"^[\d()+\- .x]{7,25}$"),
    "us_email":          re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$"),
    "maine_bar_number":  re.compile(r"^\d{2,6}$"),
    # Maine docket: same variants as data_type=docket_number check.
    "maine_docket":      re.compile(
        r"^(?:[A-Z0-9]{2,5}-)?\d{4}-(?:[A-Z]{2,5}-)?"
        r"(?:[A-Z]{2,5}-)?\d{1,6}(?:-[A-Z]{2,5})?$"
    ),
    "non_empty":         re.compile(r"\S"),
}
# Two slot conventions (prefix may contain digits like `page1`):
SLOT_RE_MID = re.compile(r"^([a-z]+(?:_[a-z0-9]+)*?)_(\d+)_([a-z]+(?:_[a-z]+)*)$")
SLOT_RE_END = re.compile(r"^([a-z]+(?:_[a-z0-9]+)*?)_([a-z]+)_(\d+)$")


def _match_slot(fid: str):
    m = SLOT_RE_MID.match(fid)
    if m:
        return (m.group(1), int(m.group(2)), m.group(3))
    m = SLOT_RE_END.match(fid)
    if m:
        return (m.group(1), int(m.group(3)), m.group(2))
    return None


# back-compat alias
SLOT_RE = SLOT_RE_MID


def _slot_fields(field_lookup: dict, group_field: str) -> list[str]:
    """Return all field_ids matching either '<group>_<n>_<suffix>' or
    '<group>_<suffix>_<n>' for a given 'group_suffix' compound key like
    'tang_desc' or 'funds_received_amount'."""
    if "_" not in group_field:
        return []
    parts = group_field.rsplit("_", 1)
    prefix, suffix = parts[0], parts[1]
    out = []
    for fid in field_lookup:
        m = _match_slot(fid)
        if m and m[0] == prefix and m[2] == suffix:
            out.append(fid)
    return out


def _norm(v) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip().lower()


def run_dedupe(field_lookup: dict, values: dict,
               group_field: str) -> list[dict]:
    fids = _slot_fields(field_lookup, group_field)
    seen: dict[str, str] = {}
    out: list[dict] = []
    for fid in sorted(fids):
        v = _norm(values.get(fid))
        if not v:
            continue
        if v in seen:
            out.append(_v(fid, SEVERITY_ERR, "dedupe_violation",
                          f"value duplicates {seen[v]} within "
                          f"{group_field}"))
        else:
            seen[v] = fid
    return out


def run_cross_section_dedupe(field_lookup: dict, values: dict,
                             this_field: dict,
                             other_groups: list[str]) -> list[dict]:
    out: list[dict] = []
    fid = this_field["field_id"]
    v = _norm(values.get(fid))
    if not v:
        return out
    for og in other_groups:
        for other_fid in _slot_fields(field_lookup, og):
            if _norm(values.get(other_fid)) == v and other_fid != fid:
                out.append(_v(fid, SEVERITY_ERR, "cross_section_dup",
                              f"value duplicates {other_fid} in {og}"))
    return out


_CAPTION_PREFIXES = re.compile(
    r"^\s*(estate of|in re|matter of|guardianship of|conservatorship of)\s+",
    re.IGNORECASE,
)


def _parse_date(s: str):
    """Best-effort date parse. Returns datetime.date or None."""
    if s in (None, ""):
        return None
    import datetime as _dt
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
                "%B %d, %Y", "%b %d, %Y",
                "%B %d %Y", "%d %B %Y"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def run_date_order(this_field: dict, values: dict,
                   other_id: str, comparator: str) -> list[dict]:
    """Enforces date_order: this_field's date relates to other_id's date
    via comparator (<, <=, >, >=, =).
    Example: decedent_date_of_death > decedent_date_of_birth.
    """
    fid = this_field["field_id"]
    this_d = _parse_date(values.get(fid))
    other_d = _parse_date(values.get(other_id))
    if this_d is None or other_d is None:
        return []  # can't compare; skip
    cmp_map = {
        "<":  lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        ">":  lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "=":  lambda a, b: a == b,
        "==": lambda a, b: a == b,
    }
    if comparator not in cmp_map:
        return []
    if cmp_map[comparator](this_d, other_d):
        return []
    return [_v(fid, SEVERITY_ERR, "date_order_violation",
               f"{fid} ({this_d}) {comparator} {other_id} ({other_d}) is false")]


def run_regex_match(this_field: dict, values: dict,
                     pattern_name: str) -> list[dict]:
    """Match the field's value against a named regex from REGEX_PATTERNS.
    Empty/unfilled values pass (use required_when to enforce presence).
    """
    fid = this_field["field_id"]
    raw = values.get(fid)
    if raw in (None, ""):
        return []
    pat = REGEX_PATTERNS.get(pattern_name)
    if pat is None:
        return [_v(fid, SEVERITY_ERR, "regex_match_unknown_pattern",
                   f"unknown pattern {pattern_name!r}")]
    if pat.search(str(raw)):
        return []
    return [_v(fid, SEVERITY_ERR, "regex_match_failed",
               f"value {raw!r} does not match pattern {pattern_name!r}")]


def _parse_numeric(s) -> float | None:
    """Strip $, commas, whitespace; return a float or None."""
    if s in (None, ""):
        return None
    s2 = str(s).strip().replace("$", "").replace(",", "").replace("%", "")
    s2 = s2.replace("(", "-").replace(")", "")  # accounting negatives
    try:
        return float(s2)
    except ValueError:
        return None


def run_value_range(this_field: dict, values: dict,
                     lo: str, hi: str) -> list[dict]:
    """Numeric bound check. `lo` and `hi` may be `*` for unbounded.
    Empty/unfilled values pass (use required_when to enforce non-empty).
    """
    fid = this_field["field_id"]
    raw = values.get(fid)
    if raw in (None, ""):
        return []
    v = _parse_numeric(raw)
    if v is None:
        return [_v(fid, SEVERITY_ERR, "value_range_unparseable",
                   f"value {raw!r} not numeric")]
    lo_f = None if lo == "*" else _parse_numeric(lo)
    hi_f = None if hi == "*" else _parse_numeric(hi)
    if lo_f is not None and v < lo_f:
        return [_v(fid, SEVERITY_ERR, "value_below_range",
                   f"value {v} < min {lo_f}")]
    if hi_f is not None and v > hi_f:
        return [_v(fid, SEVERITY_ERR, "value_above_range",
                   f"value {v} > max {hi_f}")]
    return []


def run_value_in(this_field: dict, values: dict,
                 allowed: list[str]) -> list[dict]:
    """Enforces that this_field's value is one of the allowed options.
    Empty/unfilled values are allowed (use required_when to enforce
    non-empty separately).

    Match is case-insensitive after whitespace normalization, AND we
    accept truthy/falsy aliases for yes/no options (so "Yes", "true",
    "x" all map to the `yes` allowed option).
    """
    fid = this_field["field_id"]
    v = values.get(fid)
    if v in (None, ""):
        return []
    raw = _norm(v)
    allowed_norm = [_norm(a) for a in allowed]
    if raw in allowed_norm:
        return []
    # Try boolean/yes-no coercion: if `yes`/`no` are among the allowed
    # values and the actual value coerces to a bool, match accordingly.
    coerced = _coerce_bool(v)
    if coerced is True and "yes" in allowed_norm:
        return []
    if coerced is False and "no" in allowed_norm:
        return []
    return [_v(fid, SEVERITY_ERR, "value_not_in_choices",
               f"value {raw!r} not in allowed set "
               f"{sorted(set(allowed_norm))}")]


def run_equals_field(field_lookup: dict, values: dict, this_field: dict,
                     other_id: str, mode: str = "exact") -> list[dict]:
    """Cross-field consistency: this_field's value must equal other_id's
    value. `mode` controls normalization:
      - exact: byte-equal after _norm()
      - relaxed: ignore punctuation + case
      - caption: relaxed + strip "Estate of" / "In re" / similar prefixes
        from either side before comparing
    """
    fid = this_field["field_id"]
    this_v = _norm(values.get(fid))
    other_v = _norm(values.get(other_id))
    if not this_v or not other_v:
        return []  # don't flag when either side is empty

    def relaxed(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", "", s).strip()

    def caption(s: str) -> str:
        return relaxed(_CAPTION_PREFIXES.sub("", s))

    if mode == "exact" and this_v == other_v:
        return []
    if mode == "relaxed" and relaxed(this_v) == relaxed(other_v):
        return []
    if mode == "caption" and caption(this_v) == caption(other_v):
        return []
    return [_v(fid, SEVERITY_ERR, "field_mismatch",
               f"value differs from {other_id}: {this_v!r} vs {other_v!r}")]


def run_nonempty_if_desc(field_lookup: dict, values: dict,
                        this_field: dict) -> list[dict]:
    fid = this_field["field_id"]
    m = _match_slot(fid)
    if not m:
        return []
    prefix, idx, suffix = m
    if suffix in ("desc", "description"):
        return []
    # Try both naming conventions for the sibling desc field
    desc_id_mid = f"{prefix}_{idx}_desc"
    desc_id_end = f"{prefix}_desc_{idx}"
    desc_id = desc_id_mid if desc_id_mid in field_lookup else desc_id_end
    if desc_id not in field_lookup:
        return []
    desc_v = _norm(values.get(desc_id))
    self_v = _norm(values.get(fid))
    if self_v and not desc_v:
        return [_v(fid, SEVERITY_ERR, "orphan_value",
                   f"value present but {desc_id} is empty")]
    return []


# ──────────────────────────────────────────────────────────────────────────────
# Formula DSL — interpreted from schema.fields[].formula. Recursive eval.
#
# {op: const, value: <number>}
# {op: field, id: <field_id>}
# {op: sum_slot, prefix: <p>, suffix: <s>, from: <int>, to: <int>}
# {op: add|sub|mul|div|min|max, args: [<expr>, ...]}    sub: a-b-c-...
# {op: abs, arg: <expr>}
# {op: if, cond: <bool_expr>, then: <expr>, else: <expr>}
#
# Returns float, or None if any required input is non-numeric.
# ──────────────────────────────────────────────────────────────────────────────
def _to_num(v):
    if v in (None, ""):
        return 0.0
    try:
        return float(re.sub(r"[$,]", "", str(v)))
    except Exception:
        return None


def eval_formula(expr, values: dict):
    if expr is None:
        return None
    if not isinstance(expr, dict):
        return None
    op = expr.get("op")
    if op == "const":
        return float(expr["value"])
    if op == "field":
        return _to_num(values.get(expr["id"]))
    if op == "sum_slot":
        total = 0.0
        for i in range(int(expr["from"]), int(expr["to"]) + 1):
            n = _to_num(values.get(f"{expr['prefix']}_{i}_{expr['suffix']}"))
            if n is None:
                return None
            total += n
        return total
    if op in ("add", "sub", "mul", "div", "min", "max"):
        vals = [eval_formula(a, values) for a in expr.get("args", [])]
        if any(v is None for v in vals):
            return None
        if not vals:
            return None
        if op == "add": return sum(vals)
        if op == "sub":
            res = vals[0]
            for x in vals[1:]: res -= x
            return res
        if op == "mul":
            res = vals[0]
            for x in vals[1:]: res *= x
            return res
        if op == "div":
            res = vals[0]
            for x in vals[1:]:
                if x == 0: return None
                res /= x
            return res
        if op == "min": return min(vals)
        if op == "max": return max(vals)
    if op == "abs":
        v = eval_formula(expr.get("arg"), values)
        return abs(v) if v is not None else None
    if op == "if":
        cond_val = eval_formula(expr.get("cond"), values)
        if cond_val is None: return None
        return eval_formula(expr.get("then" if cond_val else "else"), values)
    return None


def run_recompute(schema: dict, values: dict) -> list[dict]:
    out: list[dict] = []
    for f in schema.get("fields") or []:
        expr = f.get("formula")
        if not expr:
            continue
        fid = f["field_id"]
        expected = eval_formula(expr, values)
        if expected is None:
            continue  # input missing; can't verify
        got = _to_num(values.get(fid))
        if got is None:
            if values.get(fid) not in (None, ""):
                out.append(_v(fid, SEVERITY_ERR, "non_numeric",
                              "computed cell has non-numeric value"))
            continue
        # Tolerance: 0.5% relative or $5 absolute, whichever is greater.
        # Catches genuine arithmetic errors but tolerates rounding (e.g. a
        # human-entered total that the LLM read back to whole-dollar).
        tolerance = max(5.0, abs(expected) * 0.005)
        # formula_mode controls comparison:
        #   "exact" (default) — flag when |got - expected| > tolerance
        #   "at_least"        — flag only when got < expected - tolerance.
        #                       Allows got > expected to accommodate
        #                       addendum-overflow rows that aren't in the
        #                       sum_slot range. See PP-406 examples.
        mode = f.get("formula_mode") or "exact"
        if mode == "at_least":
            if got < expected - tolerance:
                out.append(_v(fid, SEVERITY_ERR, "recompute_below_minimum",
                              f"got {got:.2f}, expected at least "
                              f"{expected:.2f} (sum of in-form slots)"))
        else:
            if abs(got - expected) > tolerance:
                out.append(_v(fid, SEVERITY_ERR, "recompute_mismatch",
                              f"got {got:.2f}, expected {expected:.2f}"))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Main validation loop.
# ──────────────────────────────────────────────────────────────────────────────
def validate(schema: dict, values: dict,
             case_dict: dict | None = None) -> list[dict]:
    fields = schema.get("fields") or []
    field_lookup = {f["field_id"]: f for f in fields}
    case_dict = case_dict or {}
    violations: list[dict] = []
    dedupe_groups_done: set[str] = set()

    for f in fields:
        fid = f["field_id"]
        val = values.get(fid)
        # writable_when violation: value present where it shouldn't be
        ww = f.get("writable_when")
        if ww is not None and val not in (None, ""):
            if not _eval_cond(ww, values):
                violations.append(_v(fid, SEVERITY_ERR, "not_writable",
                                     f"value present but writable_when "
                                     f"condition is false"))
        # required_when violation: value missing where it should be present
        rw = f.get("required_when")
        if rw is not None and _eval_cond(rw, values):
            if val in (None, ""):
                violations.append(_v(fid, SEVERITY_ERR, "missing_required",
                                     "required_when condition true; value "
                                     "is empty"))
        # type contract
        violations.extend(_typecheck(f, val))

        # declarative validators
        for v_tag in f.get("validators", []):
            m = DEDUPE_RE.match(v_tag)
            if m:
                group_field = m.group(1).strip()
                if group_field in dedupe_groups_done:
                    continue
                dedupe_groups_done.add(group_field)
                violations.extend(run_dedupe(field_lookup, values,
                                             group_field))
                continue
            m = CROSS_RE.match(v_tag)
            if m:
                others = [g.strip() for g in m.group(1).split(",") if g.strip()]
                violations.extend(run_cross_section_dedupe(
                    field_lookup, values, f, others))
                continue
            if v_tag == "nonempty_if_desc":
                violations.extend(run_nonempty_if_desc(field_lookup, values, f))
                continue
            m = EQUALS_FIELD_RE.match(v_tag)
            if m:
                other_id = m.group(1).strip()
                mode = (m.group(2) or "exact").strip()
                violations.extend(run_equals_field(
                    field_lookup, values, f, other_id, mode))
                continue
            m = VALUE_IN_RE.match(v_tag)
            if m:
                allowed = [x.strip() for x in m.group(1).split(",")
                           if x.strip()]
                violations.extend(run_value_in(f, values, allowed))
                continue
            m = DATE_ORDER_RE.match(v_tag)
            if m:
                other_id = m.group(1).strip()
                comparator = m.group(2).strip()
                violations.extend(run_date_order(
                    f, values, other_id, comparator))
                continue
            m = VALUE_RANGE_RE.match(v_tag)
            if m:
                lo = m.group(1).strip()
                hi = m.group(2).strip()
                violations.extend(run_value_range(f, values, lo, hi))
                continue
            m = REGEX_MATCH_RE.match(v_tag)
            if m:
                violations.extend(run_regex_match(
                    f, values, m.group(1).strip()))
                continue
            if v_tag == "populate_from_case_dict":
                if not case_dict:
                    continue
                src = (f.get("fill_strategy") or {}).get("source") or ""
                key = src.split(".", 1)[1] if src.startswith("case_dict.") else None
                if key and key in case_dict and val != case_dict[key]:
                    violations.append(_v(fid, SEVERITY_WARN, "case_dict_drift",
                                         f"value {val!r} != case_dict[{key}]="
                                         f"{case_dict[key]!r}"))
                continue
            # recompute_from_dependencies handled in one pass below

    violations.extend(run_recompute(schema, values))
    return violations


def _flatten_filled(d: dict) -> dict:
    """Accept any of:
        {field_id: value, ...}
        {answers: {field_id: {value, confidence, reasoning}, ...}}  (fill_form output)
        {fields: [{field_id, value, ...}]}                         (older shape)
    """
    if isinstance(d.get("answers"), dict):
        return {k: v.get("value") if isinstance(v, dict) else v
                for k, v in d["answers"].items()}
    if isinstance(d.get("fields"), list):
        return {f["field_id"]: f.get("value") for f in d["fields"]
                if "field_id" in f}
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", type=pathlib.Path, required=True)
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--case-dict", type=pathlib.Path,
                    help="optional JSON of case_dict values for deterministic check")
    ap.add_argument("--show-info", action="store_true",
                    help="include severity=info messages")
    args = ap.parse_args()
    schema = json.loads(args.schema.read_text())
    raw = json.loads(args.filled.read_text())
    values = _flatten_filled(raw)
    case_dict = json.loads(args.case_dict.read_text()) if args.case_dict else {}
    violations = validate(schema, values, case_dict)
    counts: dict[str, int] = defaultdict(int)
    for v in violations:
        counts[v["severity"]] += 1
    print(f"validated {schema['form_id']} / {args.filled.name}: "
          f"{len(values)} values vs {schema['n_fields']} schema fields")
    print(f"  errors: {counts['error']}, warns: {counts['warn']}, "
          f"info: {counts['info']}")
    for v in violations:
        if v["severity"] == SEVERITY_INFO and not args.show_info:
            continue
        print(f"  [{v['severity']:5}] {v['field_id']:<30} "
              f"{v['code']:<22} {v['message']}")
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
