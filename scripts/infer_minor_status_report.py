"""Fill GS-014 (Status Report of the Guardian for a Minor).

Why this exists:
  GS-014 is a 100+ field annual report on a minor under guardianship.
  It demands granular facts that no synthetic case narrative carries:
  the minor's school/grade/educational needs, the legal parents'
  identities/addresses/phones, contact frequency, court-ordered
  duties, funds received. Qwen can't fabricate consistent values from
  a 2-sentence case description, so 80%+ of the form goes blank.

  This script fills GS-014 with deterministic mock content seeded from
  the case_id. Where the case has real data (minor.dob, petitioner
  address as guardian's address, parents in `extra_parties`), use it;
  otherwise generate stable mock values.

Fields filled (high-impact subset surfaced by audit):
  guardianship_grant_date       ← event_date − 30d  (appointment)
  minor_present_age             ← derived from minor.dob
  minor_date_of_birth           ← minor.dob
  minor_current_address         ← petitioner.address
  address_since_date            ← guardianship_grant_date
  school_details                ← stock template per age bracket
  minor_grade_level             ← derived from age
  minor_educational_achievements← stock
  minor_educational_needs       ← stock
  needs_being_met               ← "yes"
  legal_parent_{1,2,3}_*        ← extra_parties.legal_parent_N OR
                                  "Deceased — see case record" when
                                  case.facts.parents_deceased=True
  guardian_responsibilities     ← stock template
  other_minor_information       ← stock template
  conclusions_recommendations   ← stock template
  guardian_statement_name       ← petitioner.full_name (guardian)
  statement_date                ← event_date
  guardian_address/phone/email  ← petitioner.address/phone/email

Place in the fix chain: AFTER infer_signature_dates and infer_notary_*,
BEFORE recompute_overwrite. Idempotent — only fills empty fields.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys


CASES_PATH_DEFAULT = pathlib.Path("router/synthetic_cases.jsonl")


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
    v = a.get("value") if isinstance(a, dict) else a
    return "" if v in (None, "", " ") else str(v).strip()


def _set(answers: dict, fid: str, value: str, source: str) -> bool:
    if fid not in answers:
        return False
    if _get(answers, fid):
        return False
    a = answers[fid]
    if isinstance(a, dict):
        a["value"] = value
        a["confidence"] = max(float(a.get("confidence") or 0), 0.80)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"minor-status-{source}"})
    else:
        answers[fid] = value
    return True


def _set_choice(answers: dict, fid: str, value: str) -> bool:
    """For select_one fields the value is the option literal."""
    return _set(answers, fid, value, "choice")


def _lookup_case(case_id: str, cases_path: pathlib.Path) -> dict | None:
    if not cases_path.exists():
        return None
    for line in cases_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("case", {}).get("case_id") == case_id:
            return d.get("case")
    return None


def _seed_int(case_id: str, salt: str, lo: int, hi: int) -> int:
    h = int(hashlib.sha256(f"{case_id}|{salt}".encode()).hexdigest(), 16)
    return lo + (h % (hi - lo + 1))


def _seed_choice(case_id: str, salt: str, choices: list[str]) -> str:
    h = int(hashlib.sha256(f"{case_id}|{salt}".encode()).hexdigest(), 16)
    return choices[h % len(choices)]


def _add_days(iso: str, days: int) -> str:
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat()


def _age_from_dob(dob: str, ref: dt.date) -> int | None:
    try:
        d = dt.date.fromisoformat(dob)
    except (ValueError, TypeError):
        return None
    y = ref.year - d.year
    if (ref.month, ref.day) < (d.month, d.day):
        y -= 1
    return y if 0 <= y < 25 else None


GRADE_BY_AGE = {
    0: "Infant (daycare)", 1: "Infant (daycare)", 2: "Toddler (daycare)",
    3: "Pre-K", 4: "Pre-K", 5: "Kindergarten",
    6: "1st grade", 7: "2nd grade", 8: "3rd grade", 9: "4th grade",
    10: "5th grade", 11: "6th grade", 12: "7th grade",
    13: "8th grade", 14: "9th grade", 15: "10th grade",
    16: "11th grade", 17: "12th grade",
}

SCHOOL_NAMES = [
    "Bangor Children's Center", "Penobscot Valley Day Care",
    "Riverside Elementary School", "Brewer Community School",
    "Hampden Academy", "John Bapst Memorial High School",
    "Bangor High School", "Orono Middle School",
]


def _stock_school(case_id: str, age: int) -> str:
    name = _seed_choice(case_id, "school", SCHOOL_NAMES)
    address = "100 School Street, Bangor, ME 04401"
    phone = f"207-555-{_seed_int(case_id, 'school_phone', 1000, 9999):04d}"
    return f"{name}, {address}, {phone}"


def process(filled: dict, case_id: str, event_date: str | None,
            cases_path: pathlib.Path) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    case = _lookup_case(case_id, cases_path) or {}
    parties = case.get("parties") or {}
    extra = case.get("extra_parties") or {}
    facts = case.get("facts") or {}

    # ── Minor identity ───────────────────────────────────────────────
    minor = parties.get("minor") or parties.get("individual_under_protection") or {}
    minor_dob = (minor.get("dob") if isinstance(minor, dict) else None) or ""
    minor_name = (minor.get("full_name") if isinstance(minor, dict) else "") or ""

    ref_date = dt.date.today()
    if event_date:
        try:
            ref_date = dt.date.fromisoformat(event_date)
        except ValueError:
            pass

    age = _age_from_dob(minor_dob, ref_date) if minor_dob else None
    if age is None:
        age = _seed_int(case_id, "minor_age", 3, 16)

    if minor_dob:
        _set(answers, "minor_date_of_birth", minor_dob, "from-minor")
        if _get(answers, "minor_date_of_birth") == minor_dob:
            changes.append(("minor_date_of_birth", minor_dob, "from-minor"))

    if _set(answers, "minor_present_age", str(age), "from-dob"):
        changes.append(("minor_present_age", str(age), "from-dob"))

    # ── Guardianship grant date ──────────────────────────────────────
    # If facts has appointment_date use it; otherwise event_date - 365d
    # for an anniversary event, else event_date - 30d.
    grant_date = facts.get("appointment_date") or ""
    if not grant_date and event_date:
        try:
            grant_date = _add_days(event_date, -365)
        except ValueError:
            grant_date = event_date
    if grant_date:
        if _set(answers, "guardianship_grant_date", grant_date, "from-facts"):
            changes.append(("guardianship_grant_date", grant_date, "from-facts"))
        if _set(answers, "address_since_date", grant_date, "from-grant"):
            changes.append(("address_since_date", grant_date, "from-grant"))

    # ── Minor address (from guardian/petitioner) ─────────────────────
    petitioner = parties.get("petitioner") or parties.get("guardian") or {}
    guardian_addr = (petitioner.get("address") if isinstance(petitioner, dict) else "") or ""
    guardian_name = (petitioner.get("full_name") if isinstance(petitioner, dict) else "") or ""
    guardian_phone = (petitioner.get("phone") if isinstance(petitioner, dict) else "") or ""
    guardian_email = (petitioner.get("email") if isinstance(petitioner, dict) else "") or ""

    if guardian_addr:
        if _set(answers, "minor_current_address", guardian_addr, "from-guardian"):
            changes.append(("minor_current_address", guardian_addr, "from-guardian"))

    # ── Education ─────────────────────────────────────────────────────
    school = _stock_school(case_id, age)
    if _set(answers, "school_details", school, "stock"):
        changes.append(("school_details", school, "stock"))
    grade = GRADE_BY_AGE.get(age, "Pre-K")
    if _set(answers, "minor_grade_level", grade, "from-age"):
        changes.append(("minor_grade_level", grade, "from-age"))
    if _set(answers, "minor_educational_achievements",
            "Meeting age-appropriate academic milestones; "
            "good attendance and engagement reported by teachers.", "stock"):
        changes.append(("minor_educational_achievements",
                        "(stock education achievement)", "stock"))
    if _set(answers, "minor_educational_needs",
            "Standard curriculum; no special educational needs identified at this time.",
            "stock"):
        changes.append(("minor_educational_needs",
                        "(stock education needs)", "stock"))
    if _set_choice(answers, "needs_being_met", "yes"):
        changes.append(("needs_being_met", "yes", "default-yes"))

    # ── Legal parents (W051-W059) ────────────────────────────────────
    # If parents_deceased=true, fill all three rows with "Deceased".
    # Otherwise pull from extra_parties.legal_parent_N (or stock).
    parents_deceased = bool(facts.get("parents_deceased"))
    for i in (1, 2, 3):
        ep_key = f"legal_parent_{i}"
        ep = extra.get(ep_key) if isinstance(extra, dict) else None
        if isinstance(ep, dict) and ep.get("full_name"):
            name = ep.get("full_name")
            addr = ep.get("address") or guardian_addr or ""
            phone = ep.get("phone") or ""
        elif parents_deceased:
            name = "Deceased" if i <= 2 else ""
            addr = "Deceased" if i <= 2 else ""
            phone = "N/A" if i <= 2 else ""
        elif i == 1:
            # Fallback: synthesize a plausible legal parent (often
            # a co-parent the guardian isn't married to)
            name = _seed_choice(case_id, "parent1_name",
                ["Michael Bennett", "David Thibodeau", "James Whelan",
                 "Robert O'Malley", "Christopher Morrison"])
            addr = "Address unknown — last known Bangor, ME"
            phone = "Unknown"
        elif i == 2:
            name = _seed_choice(case_id, "parent2_name",
                ["Jennifer Bennett", "Linda Thibodeau", "Patricia Whelan",
                 "Susan O'Malley", "Mary Morrison"])
            addr = "Address unknown — last known Bangor, ME"
            phone = "Unknown"
        else:
            name = addr = phone = ""

        if name and _set(answers, f"legal_parent_{i}_name", name, f"parent{i}"):
            changes.append((f"legal_parent_{i}_name", name, f"parent{i}"))
        if addr and _set(answers, f"legal_parent_{i}_address", addr, f"parent{i}"):
            changes.append((f"legal_parent_{i}_address", addr, f"parent{i}"))
        if phone and _set(answers, f"legal_parent_{i}_phone", phone, f"parent{i}"):
            changes.append((f"legal_parent_{i}_phone", phone, f"parent{i}"))

    # ── Parent contact ───────────────────────────────────────────────
    # If parents are deceased, no contact possible.
    if parents_deceased:
        if _set_choice(answers, "contact_with_parents", "no"):
            changes.append(("contact_with_parents", "no", "parents-deceased"))
        if _set_choice(answers, "parent_decision_making", "no"):
            changes.append(("parent_decision_making", "no", "parents-deceased"))
        if _set(answers, "parent_decision_making_explanation",
                "Both legal parents are deceased. The guardian makes all "
                "decisions for the minor in accordance with the appointment "
                "order.", "stock"):
            changes.append(("parent_decision_making_explanation",
                            "(stock)", "parents-deceased"))
        if _set_choice(answers, "parent_financial_support", "no"):
            changes.append(("parent_financial_support", "no", "parents-deceased"))
        if _set(answers, "parent_financial_support_explanation",
                "Both legal parents are deceased and provide no financial "
                "support. The minor's needs are met through the guardian's "
                "resources and any survivor benefits.", "stock"):
            changes.append(("parent_financial_support_explanation",
                            "(stock)", "parents-deceased"))
    else:
        if _set_choice(answers, "contact_with_parents", "yes"):
            changes.append(("contact_with_parents", "yes", "default"))
        if _set(answers, "parent_contact_details",
                "The minor has contact with the parent(s) approximately "
                "monthly, supervised visits at the guardian's home, "
                "typically lasting 2-3 hours each visit.", "stock"):
            changes.append(("parent_contact_details", "(stock)", "default"))

    # ── Court-ordered guardian duties ────────────────────────────────
    if _set(answers, "guardian_responsibilities",
            "The guardian provides for the minor's care, education, health "
            "and safety. The guardian arranges medical and dental care, "
            "maintains school enrollment, ensures the minor's emotional "
            "well-being, and reports annually to the court as required by "
            "the appointment order.", "stock"):
        changes.append(("guardian_responsibilities", "(stock)", "stock"))

    # ── Other minor information + conclusion ─────────────────────────
    if _set(answers, "other_minor_information",
            "The minor is adjusting well to the guardian's household. "
            "No areas of significant concern at this time.", "stock"):
        changes.append(("other_minor_information", "(stock)", "stock"))
    if _set(answers, "conclusions_recommendations",
            "The guardianship is serving the minor's best interests. "
            "The guardian recommends that the guardianship order be "
            "continued without modification.", "stock"):
        changes.append(("conclusions_recommendations", "(stock)", "stock"))

    # ── Guardian's statement block ───────────────────────────────────
    if guardian_name and _set(answers, "guardian_statement_name",
                              guardian_name, "from-guardian"):
        changes.append(("guardian_statement_name", guardian_name, "from-guardian"))
    if event_date and _set(answers, "statement_date", event_date, "from-event"):
        changes.append(("statement_date", event_date, "from-event"))
    if guardian_addr and _set(answers, "guardian_address",
                              guardian_addr, "from-guardian"):
        changes.append(("guardian_address", guardian_addr, "from-guardian"))
    if guardian_phone and _set(answers, "guardian_phone",
                               guardian_phone, "from-guardian"):
        changes.append(("guardian_phone", guardian_phone, "from-guardian"))
    if guardian_email and _set(answers, "guardian_email",
                               guardian_email, "from-guardian"):
        changes.append(("guardian_email", guardian_email, "from-guardian"))

    return new_filled, changes


def _extract_case_id(filled_path: pathlib.Path) -> str:
    return filled_path.parent.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--cases-path", type=pathlib.Path,
                    default=CASES_PATH_DEFAULT)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()

    case_id = args.case_id or _extract_case_id(args.filled)
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, case_id, args.event_date,
                                   args.cases_path)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_minor_status_report: {len(changes)} field(s) populated "
          f"(case={case_id})")
    for fid, val, src in changes[:8]:
        print(f"  {fid} -> {val!r} ({src})")
    if len(changes) > 8:
        print(f"  ... and {len(changes) - 8} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
