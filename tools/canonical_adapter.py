#!/usr/bin/env python3
"""Bridge a canonical fact object to probate's structured case object.

The companion `maine-court-forms` project drives forms from a *canonical
fact object*:

    { "matter":  {docket_number, court_county, court_location, filing_date, ...},
      "parties": { "<role>": {full_name, address, city, state, zip, phone,
                              email, date_of_birth, ...}, ... },
      "party":   {full_name, address, ...},          # the filing party
      "facts":   { /* form-specific narrative */ } }

Probate schemas instead reference a *case object* via each field's
`fill_strategy.source` — e.g. `case_dict.county_probate_court`,
`applicant_record.applicant`, `decedent_record.decedent_date_of_birth`,
`narrative_facts.<x>`. This module converts the former into the latter so the
same fact pattern an agent builds for court forms also fills probate plans
(`tools/fill_plan.py`).

Probate `parties` roles are probate-native (applicant, petitioner, decedent,
adoptee, guardian, minor, conservator, ward, respondent, attorney, ...). For
each role present, a `<role>_record` is emitted with both `<role>` and
`<role>_name` set to the name and `<role>_<attr>` for every attribute, so a
field whose source is `applicant_record.applicant` *or*
`applicant_record.applicant_address` resolves. Extra keys are harmless — the
resolver only reads the keys a given schema references.
"""
from __future__ import annotations

import json
import sys

# Canonical party-attribute name -> the suffix used in probate `<role>_<suffix>`.
_ATTR_SUFFIX = {
    "address": "address", "street": "address",
    "city": "city", "state": "state", "zip": "zip", "zipcode": "zip",
    "phone": "phone", "telephone": "phone",
    "email": "email",
    "date_of_birth": "date_of_birth", "dob": "date_of_birth",
    "date_of_death": "date_of_death",
    "bar_number": "bar_number", "bar": "bar_number",
    "domicile": "domicile",
}


def _full_name(party: dict) -> str:
    if party.get("full_name"):
        return str(party["full_name"]).strip()
    parts = [party.get(k) for k in ("first_name", "middle_name", "last_name")]
    return " ".join(str(p).strip() for p in parts if p).strip()


def _role_record(role: str, party: dict) -> dict:
    """{role: name, role_name: name, role_<suffix>: value, role_<key>: value}."""
    name = _full_name(party)
    rec: dict[str, str] = {}
    if name:
        rec[role] = name
        rec[f"{role}_name"] = name
    for k, v in party.items():
        if v in (None, "") or k in ("full_name", "first_name", "middle_name",
                                    "last_name"):
            continue
        suffix = _ATTR_SUFFIX.get(k, k)          # passthrough unknown keys
        rec[f"{role}_{suffix}"] = v
    return rec


def _address_line(party: dict) -> str:
    bits = [party.get("address")]
    tail = " ".join(str(party[k]) for k in ("city", "state", "zip")
                    if party.get(k))
    loc = ", ".join(b for b in (party.get("address"),
                                ", ".join(str(party[k]) for k in ("city", "state")
                                          if party.get(k))) if b)
    z = party.get("zip")
    return (f"{loc} {z}".strip() if z else loc) or ""


def is_canonical(obj: dict) -> bool:
    """True for a court-style canonical fact object (vs an already-built case
    object that carries `case_dict` / `*_record` keys directly)."""
    if not isinstance(obj, dict):
        return False
    if "case_dict" in obj or any(k.endswith("_record") for k in obj):
        return False
    return any(k in obj for k in ("matter", "parties", "party", "facts"))


def to_case_object(canonical: dict) -> dict:
    """Canonical fact object -> probate case object (case_dict + *_record +
    narrative_facts). Already-built case objects pass through unchanged."""
    if not is_canonical(canonical):
        return canonical                          # assume native case object

    matter = canonical.get("matter", {}) or {}
    parties = canonical.get("parties", {}) or {}
    party = canonical.get("party", {}) or {}
    facts = canonical.get("facts", {}) or {}

    # case_dict: emit common aliases so whichever key a form uses resolves.
    county = matter.get("court_county") or matter.get("county")
    docket = matter.get("docket_number") or matter.get("docket_no")
    fdate = matter.get("filing_date") or matter.get("probate_date")
    decedent = parties.get("decedent", {}) or {}
    dec_name = _full_name(decedent)
    case_dict = {k: v for k, v in {
        "county": county, "county_probate_court": county,
        "location": matter.get("court_location"),
        "docket_number": docket, "docket_no": docket,
        "probate_docket_no": docket, "district_docket_no":
            matter.get("district_docket_no"),
        "filing_date": fdate, "probate_date": fdate,
        "decedent_name_caption": dec_name or None,
        "caption": (f"Estate of {dec_name}" if dec_name else None),
        # `estate_of_decedent` schema fields are typed person_name and sit on a
        # blank line the form pre-prints "Estate of ___" beside; the value is the
        # decedent name only (an "Estate of" prefix here double-prints over the
        # form's printed label). Use `caption` above for a full "Estate of X" string.
        "estate_of_decedent": dec_name or None,
    }.items() if v}

    out: dict = {"case_dict": case_dict}
    for role, p in parties.items():
        if isinstance(p, dict):
            rec = _role_record(role, p)
            if rec:
                out[f"{role}_record"] = rec
    # The filing party -> form_subject_record.form_subject ("Name of Address").
    if party:
        nm = _full_name(party)
        addr = _address_line(party)
        out["form_subject_record"] = {
            "form_subject": (f"{nm} of {addr}" if nm and addr else nm) or addr}
    out["narrative_facts"] = dict(facts)
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="canonical fact object -> probate "
                                 "case object")
    ap.add_argument("case", help="path to canonical (or native) case JSON")
    a = ap.parse_args()
    obj = json.loads(open(a.case).read())
    json.dump(to_case_object(obj), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
