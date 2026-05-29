"""Split ISO dates into word-form tokens for notarial-clause widgets.

Why this exists:
  Affidavit / oath forms have notarial jurats like:

      "personally appeared on this ___ day of _____________, 20___."

  with three separate widgets that need three distinct tokens:
    - day-of-month number   (e.g. "15")
    - month name            (e.g. "September")
    - 2-digit year suffix   (e.g. "24" for 2024)

  Qwen has no way to split a single ISO date "2024-09-15" across
  three fields, and our prior fix that simply replicated the full
  date into each widget rendered "2024-09-15" in all three slots —
  visible as wrong_column flags from the audit.

  This script fills `<role>_day`, `<role>_month`, and
  `<role>_year_suffix` fields from a canonical ISO date elsewhere in
  the answers (notarization_date, affidavit_date, or event_date).

Currently wired for: AF-102 notarization (notarization_day,
notarization_month, notarization_year_suffix).

Place in the fix chain: AFTER infer_signature_dates / infer_notary_*
and BEFORE recompute_overwrite.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys


_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
    v = a.get("value") if isinstance(a, dict) else a
    return "" if v in (None, "", " ") else str(v).strip()


def _set(answers: dict, fid: str, value: str, source: str) -> bool:
    # Unlike other infer scripts, this one is the FIRST writer for the
    # split fields (notarization_day/month/year_suffix were added to
    # the AF-102 tree mid-cycle; pre-existing fixed.json files lack
    # these keys). Create the key if missing.
    if fid not in answers:
        answers[fid] = {"value": "", "confidence": 0.0}
    if _get(answers, fid):
        return False
    a = answers[fid]
    if isinstance(a, dict):
        a["value"] = value
        a["confidence"] = max(float(a.get("confidence") or 0), 0.85)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"date-word-{source}"})
    else:
        answers[fid] = value
    return True


def _parse_iso(iso_date: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(iso_date)
    except Exception:
        return None


def process(filled: dict, event_date: str | None = None
            ) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    # Pick the best source date: prefer notarization_date or
    # affidavit_date (set by infer_signature_dates / infer_notary_*),
    # fall back to event_date.
    for src_field in ("notarization_date", "affidavit_date"):
        iso = _get(answers, src_field)
        if iso:
            break
    else:
        iso = event_date or ""

    d = _parse_iso(iso)
    if d is None:
        return new_filled, changes

    day = str(d.day)                         # "15"  (no zero-pad)
    month = _MONTHS[d.month]                 # "September"
    year_suffix = f"{d.year % 100:02d}"      # "24"

    # AF-102 (and any future form using these field names)
    for fid, val, src in [
        ("notarization_day", day, "iso-day"),
        ("notarization_month", month, "iso-month"),
        ("notarization_year_suffix", year_suffix, "iso-year-suffix"),
    ]:
        if _set(answers, fid, val, src):
            changes.append((fid, val, src))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, args.event_date)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_date_word_form: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
