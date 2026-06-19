#!/usr/bin/env python3
"""Generate per-form value_guide.json sidecars: what each field should contain.

A value guide is a consumable projection of schema.json enriched with the value
specifics a filler (LLM or human) needs and the schema leaves implicit:

  * format tokens (currency decimals, date format, ZIP length, year YYYY);
  * address component requirements (street / city / state / ZIP), inferred from
    the label, plus the ME-abbreviation-vs-"Maine" note;
  * calculated fields surfaced with their formula and a "validate by recompute"
    flag so derived values are checked, not trusted;
  * adversarial `avoid` notes — plausible-but-wrong values to reject (future
    DOB, "$" inside a currency box, full state name where 2-letter is expected,
    a value that would span a printed comma/$ that should be its own field).

Writes repo/forms/<ID>/value_guide.json. Validate with verify_value_guide.py.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

YEAR_RE = re.compile(r"\byear\b|_year$|year_", re.I)
ZIP_RE = re.compile(r"\bzip\b|postal", re.I)
STATE_RE = re.compile(r"\bstate\b", re.I)
COUNTY_RE = re.compile(r"county", re.I)
STREET_ONLY_RE = re.compile(r"street address|street\b", re.I)
FULL_ADDR_RE = re.compile(r"mailing|residence|domicile|legal address|"
                          r"address.*(?:email|telephone|phone)", re.I)


def _required(field: dict) -> str:
    rw = field.get("required_when")
    if rw in (True, "always"):
        return "always"
    if rw:
        return "conditional"
    return "optional"


def _address_components(label: str) -> list[str]:
    if STREET_ONLY_RE.search(label) and not FULL_ADDR_RE.search(label):
        return ["street"]
    return ["street", "city", "state", "zip"]


def _guide_for(field: dict, declared_align: dict) -> dict:
    fid = field["field_id"]
    label = field.get("label", "") or fid.replace("_", " ")
    dt = field.get("data_type")
    dc = field.get("data_constraints") or {}
    align = declared_align.get(fid, "left")
    g: dict = {"label": label, "data_type": dt, "alignment": align,
               "required": _required(field)}

    # A county blank (often typed entity_name on "<county> Probate Court") takes a
    # county name, not a person/entity name -- example it as such so QA fills and
    # downstream consumers don't put a person where a county belongs.
    if COUNTY_RE.search(label) or COUNTY_RE.search(fid):
        g["expects"] = ("a Maine county name (the form prints 'COUNTY'/'Probate "
                        "Court'); rendered upper-case, without the word 'County'")
        g["examples"] = ["CUMBERLAND", "YORK"]
        g["avoid"] = ["a person or court name", "the trailing word 'County'"]
        return g

    if field.get("formula"):
        g["calculated"] = {"formula": field["formula"],
                           "mode": field.get("formula_mode"),
                           "validate": "recompute from inputs and compare; "
                           "do not accept a free-typed total"}

    if dt == "currency":
        g["format"] = "decimal"
        g["currency"] = {"min": dc.get("min", 0),
                         "decimals": dc.get("decimals", 2)}
        g["expects"] = "a dollar amount, digits and decimal point only"
        g["examples"] = ["1500.00", "0.00", "27834.50"]
        g["avoid"] = ["a '$' character inside the value (the form prints it)",
                      "thousands separators or words",
                      "a negative amount unless this line is explicitly a credit"]
    elif dt == "date":
        fmt = dc.get("format", "iso8601_or_us")
        g["format"] = "YYYY-MM-DD or MM/DD/YYYY" if "us" in fmt else "YYYY-MM-DD"
        g["expects"] = "a real calendar date"
        g["examples"] = ["2026-03-18", "03/18/2026"]
        g["avoid"] = ["vague text like 'last spring' or 'unknown'",
                      "an impossible/!calendar date",
                      "a value that runs across a printed comma or '20__' stub — "
                      "those are separate day/month/year fields"]
        if re.search(r"birth|dob|death", fid, re.I):
            g["avoid"].append("a date in the future")
    elif dt == "address":
        comps = _address_components(label)
        g["address_components"] = comps
        g["expects"] = "an address with: " + ", ".join(comps)
        g["state_format"] = "2-letter USPS abbreviation (ME), not 'Maine'"
        g["examples"] = ["47 Pine Hill Road, Falmouth, ME 04105"]
        g["avoid"] = ["a missing ZIP code" if "zip" in comps else None,
                      "the state spelled out ('Maine') when an abbreviation fits",
                      "a PO box where a physical/legal residence is required"]
        g["avoid"] = [a for a in g["avoid"] if a]
    elif dt in ("person_name", "entity_name"):
        g["expects"] = "full legal name as it should appear on the order"
        g["examples"] = ["Margaret L. Walsh"]
        g["avoid"] = ["initials or nicknames", "a title with no name",
                      "'Last, First' order unless the line calls for it"]
    elif dt == "phone":
        g["format"] = "NNN-NNN-NNNN"
        g["expects"] = "a 10-digit US phone number"
        g["avoid"] = ["letters", "a missing area code"]
    elif dt == "email":
        g["format"] = "local@domain"
        g["avoid"] = ["spaces", "a missing @"]
    elif dt == "docket_number":
        g["expects"] = "a Maine probate/district docket number"
        g["avoid"] = ["a docket from another jurisdiction's format"]
    elif dt == "bar_number":
        g["expects"] = "a Maine bar registration number"
    elif dt == "signature":
        g["expects"] = "leave blank — wet-ink signature"
        g["fill"] = False
    elif dt in ("checkbox",) or field.get("type") == "enabler":
        g["expects"] = "check only if the statement is true"
    else:  # generic text — specialise from the label where we can
        if YEAR_RE.search(label) or YEAR_RE.search(fid):
            g["format"] = "YYYY"
            g["calendar_year"] = True
            g["expects"] = "a 4-digit calendar year"
            g["avoid"] = ["a 2-digit year",
                          "text spanning the printed '20' stub — fill only the "
                          "remaining digits if the form prints '20__'"]
        elif ZIP_RE.search(label):
            g["format"] = "ZIP5 or ZIP+4"
            g["expects"] = "a US ZIP code"
            g["avoid"] = ["fewer than 5 digits", "letters"]
        elif STATE_RE.search(label):
            g["expects"] = "a US state"
            g["state_format"] = "2-letter USPS abbreviation (ME) preferred"
        elif COUNTY_RE.search(label):
            g["expects"] = "a Maine county name WITHOUT the word 'County' " \
                           "(the form prints 'COUNTY'); rendered upper-case"
            g["examples"] = ["CUMBERLAND", "YORK"]
            g["avoid"] = ["the trailing word 'County'"]
        else:
            g["expects"] = "free text answering the printed prompt"
    return g


def build_form(form_id: str) -> dict:
    pkg = ROOT / "repo" / "forms" / form_id
    schema = json.loads((pkg / "schema.json").read_text())
    align = {}
    ap = ROOT / "catalog" / "field_alignment.json"
    if ap.exists():
        align = json.loads(ap.read_text()).get("forms", {}).get(form_id, {})
    fields = {f["field_id"]: _guide_for(f, align) for f in schema.get("fields", [])}
    return {
        "form_id": form_id,
        "_comment": "Per-field value guide (generated by "
                    "scripts/build_value_guide.py from schema.json). Tells a "
                    "filler what each field should contain, with formats, "
                    "address components, calculations to validate, and "
                    "adversarial values to avoid. Not legal advice.",
        "fields": fields,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms")
    ap.add_argument("--check", action="store_true",
                    help="fail if any guide is out of date")
    args = ap.parse_args()
    forms = ([s.strip() for s in args.forms.split(",")] if args.forms else
             sorted(p.parent.name for p in
                    (ROOT / "repo" / "forms").glob("*/schema.json")))
    stale = []
    for form_id in forms:
        guide = build_form(form_id)
        out = ROOT / "repo" / "forms" / form_id / "value_guide.json"
        text = json.dumps(guide, indent=2, ensure_ascii=False) + "\n"
        if args.check:
            if not out.exists() or out.read_text() != text:
                stale.append(form_id)
        else:
            out.write_text(text)
    if args.check and stale:
        print("stale value_guide.json (run scripts/build_value_guide.py): "
              + ", ".join(stale))
        return 1
    print(f"{'checked' if args.check else 'wrote'} {len(forms)} value guides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
