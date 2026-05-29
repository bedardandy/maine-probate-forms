"""N-112 (Notice of Foreign Appointment) — fill appointing court block.

N-112 is filed in Maine to notify the court of a fiduciary appointment
made by a foreign court. Two fields:

  appointing_court_name      ← name of the foreign appointing court
  appointing_court_address   ← address of that court

The case generator doesn't emit a "foreign appointing court" concept.
For synthetic test cases, fill these with a plausible neighboring-state
probate court reference (seeded by case_id for stability).

Form-gated. AFTER notary, BEFORE recompute_overwrite. Idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys


COURTS = (
    ("New Hampshire Circuit Court — Probate Division",
     "Strafford County Courthouse, 259 County Farm Road, Dover, NH 03820"),
    ("Massachusetts Probate and Family Court",
     "Suffolk County Courthouse, 24 New Chardon Street, Boston, MA 02114"),
    ("Vermont Superior Court — Probate Division",
     "Chittenden Unit, 175 Main Street, Burlington, VT 05402"),
    ("Connecticut Probate Court",
     "District of Hartford, 250 Constitution Plaza, Hartford, CT 06103"),
    ("Rhode Island Probate Court",
     "Providence Probate Court, 25 Dorrance Street, Providence, RI 02903"),
)


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
        a["confidence"] = max(float(a.get("confidence") or 0), 0.75)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"n112-{source}"})
    else:
        answers[fid] = value
    return True


def process(filled: dict, case_id: str) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "N-112":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    h = hashlib.sha256((case_id or "").encode()).digest()
    court_name, court_addr = COURTS[h[0] % len(COURTS)]

    if _set(answers, "appointing_court_name", court_name, "seeded-foreign"):
        changes.append(("appointing_court_name", court_name, "seeded-foreign"))
    if _set(answers, "appointing_court_address", court_addr, "seeded-foreign"):
        changes.append(("appointing_court_address", court_addr,
                        "seeded-foreign"))

    return new_filled, changes


def _extract_case_id(filled_path: pathlib.Path) -> str:
    return filled_path.parent.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    ap.add_argument("--event-date", type=str, default=None)
    args = ap.parse_args()
    case_id = args.case_id or _extract_case_id(args.filled)
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, case_id)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_n112_appointing_court: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
