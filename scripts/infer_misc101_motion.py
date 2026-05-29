"""MISC-101-specific: fill generic motion-form fields.

Why this exists:
  MISC-101 is the catch-all Motion Form used across probate practice.
  Required fields include motion subject ("Motion FOR …"), particular
  reasons, certificate of service, dated/signed lines — all of which
  the LLM leaves blank because the narrative is silent on the specific
  motion content. Vision audit flags ~7 fields per case.

  For synthetic test cases we don't have a "this is a motion to
  continue, here's why" narrative. We populate the most-common motion
  (continuance — additional time to complete inventory/accounting)
  with generic explanatory text. Real productionized use would have
  case-specific motion content from the user.

Fields filled:
  motion_for                              ← "Continuance"
  particular_reasons                      ← generic explanation
  movant_printed_name                     ← movant_name_address first
                                            segment, else attorney_name
  motion_date / service_date / movant_signature
                                          ← event_date
  certificate_of_service_name             ← attorney_name or movant_printed_name
  service_recipients                      ← "All parties of record and the
                                            Register of Probate, by
                                            first-class mail."

Place in the fix chain: form-aware (MISC-101 only). After other
inference; before recompute_overwrite.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None: return ""
    v = a.get("value") if isinstance(a, dict) else a
    return "" if v in (None, "", " ") else str(v).strip()


def _set(answers: dict, fid: str, value: str, source: str) -> bool:
    if fid not in answers: return False
    if _get(answers, fid): return False
    a = answers[fid]
    if isinstance(a, dict):
        a["value"] = value
        a["confidence"] = max(float(a.get("confidence") or 0), 0.70)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"misc101-{source}"})
    else:
        answers[fid] = value
    return True


def _first_segment(s: str) -> str:
    if not s: return ""
    return s.split(",", 1)[0].strip()


GENERIC_MOTION_FOR = "Continuance"
GENERIC_REASONS = (
    "Additional time is needed to complete the inventory and to gather "
    "the documentation necessary to prepare the accounting."
)
# Vision audit caught the previous generic ("All parties of record and the
# Register of Probate, by first-class mail.") overlapping the printed
# "Mailing Address (including Zip Code)" column header and being truncated
# at the 150pt-wide widget edge. The form expects ONE recipient (name +
# address) per row, not a free-form description, so leave blank rather
# than auto-fill a string that won't render cleanly.
GENERIC_SERVICE_RECIPIENTS = ""


def _lookup_case(case_id: str, cases_path: pathlib.Path) -> dict | None:
    if not case_id or not cases_path.exists():
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


CASES_PATH_DEFAULT = pathlib.Path("router/synthetic_cases.jsonl")


def process(filled: dict, event_date: str | None,
            case_id: str | None = None,
            cases_path: pathlib.Path = CASES_PATH_DEFAULT) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "MISC-101":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    if _set(answers, "motion_for", GENERIC_MOTION_FOR, "generic-continuance"):
        changes.append(("motion_for", GENERIC_MOTION_FOR, "generic-continuance"))
    if _set(answers, "particular_reasons", GENERIC_REASONS,
            "generic-explanation"):
        changes.append(("particular_reasons", GENERIC_REASONS,
                        "generic-explanation"))

    # Mirror particular_reasons → motion_to_continue_reasons (section 4A
    # widget on MISC-101 is named motion_to_continue_reasons; vision
    # audit flagged this as blank_required even when particular_reasons
    # is filled because they are separate widgets).
    if _set(answers, "motion_to_continue_reasons", GENERIC_REASONS,
            "mirror-particular-reasons"):
        changes.append(("motion_to_continue_reasons", GENERIC_REASONS,
                        "mirror-particular-reasons"))

    # 4B "I learned of my need to file this Motion to Continue on this
    # date" — Maine MISC-101 form has the date widget mapped to
    # `motion_to_continue_explanation`. Use event_date − 14d as a
    # plausible "learned-of-need" anchor for synthetic cases (motions
    # are typically prepared 1-2 weeks before the hearing).
    if event_date:
        import datetime as _dt
        try:
            learn_d = (_dt.date.fromisoformat(event_date) - _dt.timedelta(days=14)).isoformat()
        except ValueError:
            learn_d = event_date
        if _set(answers, "motion_to_continue_explanation", learn_d,
                "learn-14d-before-event"):
            changes.append(("motion_to_continue_explanation", learn_d,
                            "learn-14d-before-event"))

    # docket_no — pull from case.docket_number if present, else only
    # use case_id when it already looks docket-shaped (contains uppercase
    # letters + digits + a separator). adoption-petition-726-york is
    # not a docket, so we leave the widget blank in that case.
    case = _lookup_case(case_id, cases_path) if case_id else None
    docket = (case or {}).get("docket_number")
    if not docket and case_id:
        import re as _re
        # heuristic: e.g. PR-2024-000892, 2024-CP-011493, MPR-2024-0828-ES
        if _re.search(r"^[A-Z0-9]+-?[A-Z]?-?\d{4}", case_id):
            docket = case_id
    if docket and _set(answers, "docket_no", docket, "from-case"):
        changes.append(("docket_no", docket, "from-case"))

    movant_full = _get(answers, "movant_name_address")
    attorney = _get(answers, "attorney_name")
    movant_name = _first_segment(movant_full) or attorney
    if movant_name and _set(answers, "movant_printed_name", movant_name,
                              "from-movant-or-attorney"):
        changes.append(("movant_printed_name", movant_name,
                        "from-movant-or-attorney"))

    if event_date:
        for fid, src in [
            ("motion_date", "from-event"),
            ("service_date", "from-event"),
            ("movant_signature", "sig-name-from-movant"),
        ]:
            if fid == "movant_signature":
                v = movant_name
            else:
                v = event_date
            if v and _set(answers, fid, v, src):
                changes.append((fid, v, src))

    cert_name = attorney or movant_name
    if cert_name and _set(answers, "certificate_of_service_name", cert_name,
                            "cert-from-attorney-or-movant"):
        changes.append(("certificate_of_service_name", cert_name,
                        "cert-from-attorney-or-movant"))

    # service_recipients deliberately left blank — see comment on
    # GENERIC_SERVICE_RECIPIENTS above. Real use needs case-specific
    # recipients.
    _ = GENERIC_SERVICE_RECIPIENTS

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
    new_filled, changes = process(filled, args.event_date, case_id,
                                   args.cases_path)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_misc101_motion: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
