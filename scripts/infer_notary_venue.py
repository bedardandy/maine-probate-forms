"""Fill the *notary venue* fields on oath/affidavit forms.

Why this exists:
  Affidavit-style forms (AF-102, AF-103, etc.) have a notarial clause
  with State / County / day-of-month / month-of-year blanks for the
  notary venue stamp, plus a `notarization_date` field. Qwen leaves
  these blank because the case narrative never asserts them — they're
  procedural facts of the act of notarization, not case facts.

Fields filled:
  notary_state         ← "Maine" (default — all forms here are Maine)
  notary_county        ← county_probate_court (court-of-record county
                          is also the notarization county for any
                          probate-related notarization)
  notarization_date    ← event_date

Place in the fix chain: AFTER infer_notary_fields (which handles
DE-603's separate notary_county/notary_date) and BEFORE
recompute_overwrite.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


DEFAULT_STATE = "Maine"


def _lookup_county_cross_form(case_id: str, current_form: str) -> str:
    """When the current form's narrative doesn't surface county info
    (AF-102's narrative often gives only town: "Portland"), borrow
    county_probate_court from any sibling form's fill for the same
    case. DE-101/DE-201 etc. usually have it populated.
    """
    case_dir = pathlib.Path("intermediate/router") / case_id
    if not case_dir.is_dir():
        return ""
    for fixed in case_dir.glob("filled_router.*.fixed.json"):
        if f".{current_form}." in fixed.name:
            continue
        try:
            data = json.loads(fixed.read_text())
        except Exception:
            continue
        ans = data.get("answers") or {}
        for fid in ("county_probate_court", "county", "decedent_county"):
            a = ans.get(fid)
            if a is None:
                continue
            v = a.get("value") if isinstance(a, dict) else a
            if v and v not in ("", " "):
                return str(v).strip()
    return ""


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
            {"to": value, "method": f"venue-{source}"})
    else:
        answers[fid] = value
    return True


def process(filled: dict, event_date: str | None = None,
            case_id: str | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []
    form_id = (new_filled.get("form_id") or "").upper()

    # Court-of-record county. Different forms wire this as
    # `county_probate_court` (DE-101 family), `county` (DE-406,
    # PP-412), or `decedent_county` (AF-102 affidavit-of-heirship —
    # decedent's county is the venue for the notarization, since the
    # affiant attests in that county).
    court_county = (_get(answers, "county_probate_court")
                    or _get(answers, "county")
                    or _get(answers, "decedent_county"))

    # Last resort: borrow county from a sibling form's fill of the
    # same case. AF-102's narrative often gives only the town
    # ("Portland"), leaving every county-bearing field blank — but
    # DE-101 (same case) usually has county_probate_court filled.
    if not court_county and case_id and form_id:
        court_county = _lookup_county_cross_form(case_id, form_id)
        if court_county:
            # Also backfill decedent_county for forms that have it.
            if "decedent_county" in answers and _set(
                    answers, "decedent_county", court_county,
                    "decedent-county-from-sibling"):
                changes.append(("decedent_county", court_county,
                                "decedent-county-from-sibling"))

    if _set(answers, "notary_state", DEFAULT_STATE, "state-default"):
        changes.append(("notary_state", DEFAULT_STATE, "state-default"))

    if court_county and _set(answers, "notary_county", court_county,
                              "county-from-court"):
        changes.append(("notary_county", court_county, "county-from-court"))

    if event_date and _set(answers, "notarization_date", event_date,
                            "date-from-event"):
        changes.append(("notarization_date", event_date, "date-from-event"))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--case-id", type=str, default=None,
                    help="Override case_id (otherwise inferred from path).")
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()

    case_id = args.case_id or args.filled.parent.name
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, args.event_date, case_id)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_notary_venue: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
