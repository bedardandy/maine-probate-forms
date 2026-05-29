"""Fill PP-412 (Conservator's Report) narrative items + plan date.

PP-412 has six narrative items at the heart of the annual report:
  1. services_provided           (filled by LLM from facts)
  2. recommended_changes         (filled by LLM from facts)
  3. de_minimis_value_received   ← stock "None"
  4. business_relation_provider  ← stock "None"
  5. business_relation_paid      ← stock "None"
  6. coconservator_status        ← stock "Not applicable"

Plus paragraph 7c "A copy of the Conservator's most recently approved
plan dated ____" → `approved_plan_date`, from facts.appointment_date OR
event_date − 365d (same approach as infer_adult_status_report).

Items 1 and 2 we leave alone — they're case-specific and the LLM
usually has enough to write them. The 4-item attestation block (3-6)
is the standard boilerplate "no gifts, no conflicts, no co-conservator"
attestation; mirror of PP-209 Q10-Q16.

Place in fix chain: AFTER infer_conservator_dates, BEFORE
recompute_overwrite. Form-gated (form_id == PP-412). Idempotent.
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
            {"to": value, "method": f"pp412-{source}"})
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
    if (new_filled.get("form_id") or "").upper() != "PP-412":
        return new_filled, []

    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    case = _lookup_case(case_id, cases_path) or {}
    facts = case.get("facts") or {}

    # 7c — approved plan date. Use facts.appointment_date if present,
    # else event_date − 365d. Conservators are required to file a plan
    # within 60d of appointment, so the "most recently approved plan"
    # is anchored to the appointment.
    plan_date = facts.get("appointment_date") or facts.get("plan_approval_date") or ""
    if not plan_date and event_date:
        try:
            plan_date = _add_days(event_date, -365)
        except ValueError:
            plan_date = event_date
    if plan_date and _set(answers, "approved_plan_date", plan_date,
                          "from-facts"):
        changes.append(("approved_plan_date", plan_date, "from-facts"))

    # Items 3-6 — standard attestations.
    templates = [
        ("de_minimis_value_received",
         "None. Neither the Conservator nor any member of the Conservator's "
         "household has received any item of more than de minimis value "
         "from any person providing goods or services to the individual "
         "during the reporting period."),
        ("business_relation_provider",
         "None. The Conservator has no business relation with any person "
         "providing goods or services to the individual."),
        ("business_relation_paid",
         "None. The Conservator has no business relation with any person "
         "the Conservator has paid or who has benefited from the "
         "individual's property."),
        ("coconservator_status",
         "Not applicable — no Co-Conservator or Successor Conservator has "
         "been appointed to serve."),
    ]
    for fid, val in templates:
        if _set(answers, fid, val, "stock"):
            changes.append((fid, "(stock)", "stock"))

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
    print(f"infer_pp412_narrative: {len(changes)} field(s) populated "
          f"(case={case_id}, form={filled.get('form_id')})")
    for fid, val, src in changes[:6]:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
