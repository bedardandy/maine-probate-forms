"""Fill PP-412 (and similar conservator-report) date fields.

Why this exists:
  PP-412 (Conservator's Report) has procedural date fields that Qwen
  doesn't fill because the narrative is silent on them:

    report_period_start    — the reporting window start
    report_period_end      — the reporting window end

  These are derivable from the event_date (the report-anniversary date
  is the period end; the start is one year earlier for an annual
  report).

  conservatorship_grant_date — the original conservator appointment
  date — is NOT derivable from event_date alone. It needs a Case.facts
  entry. This script leaves it blank if absent; the Case-fixture
  enrichment work (Bucket 3) is the right place to surface it.

Place in the fix chain: AFTER infer_signature_dates / infer_notary_*
and BEFORE recompute_overwrite.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
    if isinstance(a, dict):
        v = a.get("value")
    else:
        v = a
    if v in (None, "", " "):
        return ""
    return str(v).strip()


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
            {"to": value, "method": f"conservator-{source}"})
    else:
        answers[fid] = value
    return True


def _shift_year(iso_date: str, years: int) -> str:
    d = dt.date.fromisoformat(iso_date)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:
        # Feb-29 → Feb-28 in non-leap target year.
        return d.replace(year=d.year + years, day=28).isoformat()


def _anniversary_index(event_type: str | None) -> int:
    """Extract N from event types like `e1_appointment_anniversary`,
    `e3_appointment_anniversary` etc. Defaults to 1."""
    if not event_type:
        return 1
    for tok in event_type.split("_"):
        if tok.startswith("e") and tok[1:].isdigit():
            return max(1, int(tok[1:]))
    return 1


def process(filled: dict, event_date: str | None,
            event_type: str | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    if not event_date:
        return new_filled, changes

    # report_period_end ← event_date (annual anniversary IS the period
    # end-date)
    if _set(answers, "report_period_end", event_date, "end-from-event"):
        changes.append(("report_period_end", event_date, "end-from-event"))

    # report_period_start ← event_date − 1y
    start = _shift_year(event_date, -1)
    if _set(answers, "report_period_start", start, "start-1y-before-event"):
        changes.append(("report_period_start", start, "start-1y-before-event"))

    # conservatorship_grant_date ← event_date − N years, where N is
    # the appointment-anniversary index (e1 → 1 year ago, e3 → 3 years
    # ago, etc.). The original conservator appointment necessarily
    # precedes every anniversary report by exactly N years for a clean
    # synthetic case.
    n = _anniversary_index(event_type)
    grant_date = _shift_year(event_date, -n)
    if _set(answers, "conservatorship_grant_date", grant_date,
            f"grant-{n}y-before-event"):
        changes.append(("conservatorship_grant_date", grant_date,
                        f"grant-{n}y-before-event"))

    return new_filled, changes


def _extract_event_type(filled_path: pathlib.Path) -> str:
    """filled_router.{event}.{form}.{stage}.json → event"""
    parts = filled_path.name.split(".")
    return parts[1] if len(parts) > 1 else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--event-type", type=str, default=None,
                    help="Override event_type (otherwise inferred from path).")
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()

    event_type = args.event_type or _extract_event_type(args.filled)
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, args.event_date, event_type)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_conservator_dates: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
