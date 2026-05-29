"""Fill PP-209 (Guardian's Status Report for an Adult).

Mirror of infer_minor_status_report.py, but for the adult-guardian
annual report. The form has 16 narrative items (Q1-Q16) about the
individual's living situation, the guardian's actions, fees, delegation,
plans, etc. — facts no synthetic case narrative carries.

Place in the fix chain: AFTER infer_signature_dates / infer_notary_*,
BEFORE recompute_overwrite. Idempotent — only fills empty fields.

Fields filled (high-impact subset):
  guardianship_grant_date                  ← facts.appointment_date OR
                                              event_date − 365d
  full_legal_name_address_location         ← from parties.respondent/
                                              individual_under_protection
  current_mental_physical_social_condition ← stock template
  living_arrangements                      ← stock template
  supported_decision_making_services       ← stock
  guardian_visits                          ← stock (with sample dates)
  actions_taken_by_guardian                ← stock
  individual_participation_in_decision_making ← stock
  facility_plan_consistency                ← stock or "N/A — community"
  gifts_received_from_individual           ← "None"
  business_relationships                   ← "None"
  guardian_fees                            ← "No fees paid for the year"
  delegated_powers                         ← "No powers delegated"
  plan_deviation_and_revision              ← stock
  future_care_plans                        ← stock
  recommendation_for_continued_guardianship← stock recommendation
  co_guardian_status                       ← "Not applicable"
  guardian_date                            ← event_date
  guardian_signature                       ← guardian/petitioner name
"""
from __future__ import annotations

import argparse
import datetime as dt
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
            {"to": value, "method": f"adult-status-{source}"})
    else:
        answers[fid] = value
    return True


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


def _add_days(iso: str, days: int) -> str:
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat()


def process(filled: dict, case_id: str, event_date: str | None,
            cases_path: pathlib.Path) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    case = _lookup_case(case_id, cases_path) or {}
    parties = case.get("parties") or {}
    facts = case.get("facts") or {}

    # The protected adult is the "respondent" (adult guardianship) or
    # "individual_under_protection" depending on case generator output.
    ind = (parties.get("respondent")
           or parties.get("individual_under_protection")
           or parties.get("ward")
           or {})
    ind_name = ind.get("full_name") if isinstance(ind, dict) else ""
    ind_addr = ind.get("address") if isinstance(ind, dict) else ""

    # Guardian = (post-appointment) petitioner. Per case_chain.py the
    # `add_party from=petitioner to=guardian` op runs at hearing.
    guardian = (parties.get("guardian") or parties.get("petitioner") or {})
    guardian_name = guardian.get("full_name") if isinstance(guardian, dict) else ""

    # Grant date from appointment_date fact OR event_date − 365d.
    grant_date = facts.get("appointment_date") or ""
    if not grant_date and event_date:
        try:
            grant_date = _add_days(event_date, -365)
        except ValueError:
            grant_date = event_date
    if grant_date and _set(answers, "guardianship_grant_date", grant_date,
                            "from-facts"):
        changes.append(("guardianship_grant_date", grant_date, "from-facts"))

    # Individual identity (Q1 — full legal name + address + location)
    if ind_name:
        loc = f"{ind_name} — residing at {ind_addr or 'address as set forth on Petition'} (Maine)"
        if _set(answers, "full_legal_name_address_location", loc, "from-respondent"):
            changes.append(("full_legal_name_address_location", loc, "from-respondent"))

    # Q2-Q16 stock narrative templates
    templates = [
        ("current_mental_physical_social_condition",
         "The individual is in stable mental, physical, and social condition. "
         "There has been no significant deterioration during the reporting period. "
         "Caregivers report consistent quality of life."),
        ("living_arrangements",
         "The individual resides at the address listed in Q1 throughout the reporting period. "
         "No changes in living arrangement occurred."),
        ("supported_decision_making_services",
         "The individual receives community-based supports including assistance with daily activities. "
         "The Guardian is of the opinion that current care arrangements are adequate to meet the individual's needs."),
        ("guardian_visits",
         "The Guardian visits the individual approximately weekly throughout the reporting period. "
         "Visits include both in-person check-ins and contact by phone with caregivers."),
        ("actions_taken_by_guardian",
         "The Guardian has acted in the individual's best interests including arranging medical and dental care, "
         "managing financial affairs as authorized, and coordinating with care providers."),
        ("individual_participation_in_decision_making",
         "The individual participates in decision-making to the extent possible given current capacity. "
         "Preferences are solicited and honored where consistent with safety."),
        ("facility_plan_consistency",
         "Not applicable — individual resides in the community and is not in a facility with a written plan."),
        ("gifts_received_from_individual",
         "None. Neither the Guardian nor any member of the Guardian's household has received any item of more-than-de-minimis value from anyone providing goods or services to the individual during the reporting period."),
        ("business_relationships",
         "None. The Guardian has no business relationships with persons paid from or benefiting from the individual's property."),
        ("guardian_fees",
         "No Guardian fees have been paid or are outstanding for the reporting period. The Guardian serves without compensation."),
        ("delegated_powers",
         "None. The Guardian has not delegated any powers to an agent during the reporting period."),
        ("plan_deviation_and_revision",
         "The Guardian has not materially deviated from the most recent Plan. No revised Plan is anticipated at this time."),
        ("future_care_plans",
         "The Guardian intends to continue the current care arrangement and to monitor the individual's needs. "
         "Adjustments will be made as appropriate to ensure continued well-being."),
        ("recommendation_for_continued_guardianship",
         "The Guardian recommends that the guardianship continue without modification. "
         "Current scope is appropriate to the individual's needs."),
        ("co_guardian_status",
         "Not applicable — no Co-Guardian or Successor Guardian has been appointed."),
    ]
    for fid, val in templates:
        if _set(answers, fid, val, "stock"):
            changes.append((fid, "(stock)", "stock"))

    # Signature block
    if event_date and _set(answers, "guardian_date", event_date, "from-event"):
        changes.append(("guardian_date", event_date, "from-event"))
    if guardian_name and _set(answers, "guardian_signature", guardian_name,
                              "from-guardian"):
        changes.append(("guardian_signature", guardian_name, "from-guardian"))

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
    print(f"infer_adult_status_report: {len(changes)} field(s) populated "
          f"(case={case_id})")
    for fid, val, src in changes[:6]:
        print(f"  {fid} -> {val!r} ({src})")
    if len(changes) > 6:
        print(f"  ... and {len(changes) - 6} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
