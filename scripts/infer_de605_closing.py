"""DE-605 (Petition to Reopen Estate / Closing) — fill closing dates
and certificate-page header.

DE-605 page 1 has three closing-event dates that the LLM doesn't have
in facts:
  - sworn_statement_filed_date     "Sworn Statement (DE-602) closing
                                    the Estate was filed on ___"
  - court_order_closing_date       "or the Estate was formally closed
                                    by a Court order dated ___"
  - appointment_termination_date   "Personal Representative's
                                    appointment terminated on ___"

These are anchored to the closing event. Page 2 has a Certificate
block (filed by the Register of Probate) needing docket_no and
cert_date — those propagate from the case docket and event date.
The Register's signature is intentionally left blank (filled by
clerk on receipt).

Place in fix chain: AFTER infer_notary_*, BEFORE recompute_overwrite.
Form-gated (DE-605). Idempotent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys


CASES_PATH_DEFAULT = pathlib.Path("router/synthetic_cases.jsonl")
DOCKET_LIKE_RE = re.compile(r"^[A-Z]{0,4}-?\d{4}-?[A-Z]{2,3}-?\d{3,6}(?:-[A-Z]{2})?$")


def _synth_docket(case_id: str) -> str:
    if DOCKET_LIKE_RE.match(case_id):
        return case_id
    h = hashlib.sha256(case_id.encode()).hexdigest()
    n = int(h[:5], 16) % 90000 + 10000
    return f"2024-EP-{n:05d}"


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
            {"to": value, "method": f"de605-{source}"})
    else:
        answers[fid] = value
    return True


def _add_days(iso: str, days: int) -> str:
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat()


def process(filled: dict, event_date: str | None,
            case_id: str | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "DE-605":
        return new_filled, []

    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    if event_date:
        # Sworn statement filed = estate closing date (= event date for
        # closing-anchored events) OR slightly before for reopen-anchored.
        # In our synthetic pipeline DE-605 is generated against the
        # decedent_death_date event when the LLM speculates about future
        # closing. Use event_date as a sane anchor.
        sworn_date = event_date
        # Court order closing the estate — usually within a few days
        # of the sworn statement. Use same date (filings often coincide).
        order_date = event_date
        # PR appointment terminated when estate closed.
        term_date = event_date

        for fid, val, src in [
            ("sworn_statement_filed_date", sworn_date, "from-event"),
            ("court_order_closing_date", order_date, "from-event"),
            ("appointment_termination_date", term_date, "from-event"),
        ]:
            if _set(answers, fid, val, src):
                changes.append((fid, val, src))

    # Page-1 docket — synth if blank so cert page propagation succeeds.
    if case_id and not _get(answers, "docket_no"):
        docket_synth = _synth_docket(case_id)
        if _set(answers, "docket_no", docket_synth, "synth-from-case-id"):
            changes.append(("docket_no", docket_synth, "synth"))

    # Certificate page header. cert_county_probate_court and cert_docket_no
    # mirror their page-1 counterparts.
    county = _get(answers, "county_probate_court")
    docket = _get(answers, "docket_no")
    if county and _set(answers, "cert_county_probate_court", county,
                       "from-page-1-county"):
        changes.append(("cert_county_probate_court", county, "from-page-1"))
    if docket and _set(answers, "cert_docket_no", docket,
                       "from-page-1-docket"):
        changes.append(("cert_docket_no", docket, "from-page-1"))
    if event_date and _set(answers, "cert_date", event_date,
                           "from-event"):
        changes.append(("cert_date", event_date, "from-event"))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()

    case_id = args.case_id or args.filled.parent.name
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, args.event_date, case_id)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_de605_closing: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
