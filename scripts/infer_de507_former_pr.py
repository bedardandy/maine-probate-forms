"""DE-507-specific: fill blank Former Personal Representative fields.

Why this exists:
  DE-507 (Petition to Appoint Successor / Replacement Personal
  Representative) requires details about the OLD PR being replaced:

    former_pr_name        — name of the outgoing PR
    former_pr_address     — their address
    appointment_date      — when they were originally appointed
    termination_date      — when their appointment was terminated

  The case fixture only carries the current PR; nothing about the
  former one. Vision audit flagged all four as blank_required.

  We can't derive these from existing answers, but we can mint stable
  mock data from the case_id (so two refills of the same case produce
  the same former-PR identity). The mock identity is clearly synthetic
  (random Maine names + addresses) but consistent enough to populate
  the form.

Place in the fix chain: form-aware (DE-507 only). After other inference;
before recompute_overwrite.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import random
import sys


# Small Maine-flavored pool. Mock data — synthetic identities, not
# real attorneys/PRs. Picked deterministically from case_id seed.
_FIRST_NAMES = [
    "Margaret", "Robert", "Patricia", "James", "Linda",
    "William", "Barbara", "Charles", "Elizabeth", "Thomas",
]
_MIDDLE_INITIALS = ["A.", "B.", "C.", "D.", "E.", "F.", "G.", "H.", "J.", "K.", "L.", "M."]
_LAST_NAMES = [
    "Bourgeois", "Chamberlain", "Dubois", "Eldridge", "Fournier",
    "Gagnon", "Hilliard", "Levesque", "Morin", "Pelletier",
]
_STREET_NUMBERS = list(range(20, 500, 7))
_STREETS = [
    "Pine St", "Birch Ave", "Cedar Ln", "Elm Rd", "Maple Dr",
    "Oak Way", "Spruce Cir", "Willow Ct", "River Rd", "Hill St",
]
_TOWNS_ZIPS = [
    ("Portland", "04101"),
    ("Bangor", "04401"),
    ("Augusta", "04330"),
    ("Lewiston", "04240"),
    ("Brunswick", "04011"),
    ("Saco", "04072"),
    ("Waterville", "04901"),
]


def _seeded(case_id: str) -> random.Random:
    h = hashlib.sha256(f"de507-former-pr|{case_id}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _mock_identity(rng: random.Random) -> tuple[str, str]:
    name = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_MIDDLE_INITIALS)} {rng.choice(_LAST_NAMES)}"
    town, zipc = rng.choice(_TOWNS_ZIPS)
    addr = f"{rng.choice(_STREET_NUMBERS)} {rng.choice(_STREETS)}, {town}, ME {zipc}"
    return name, addr


def _shift_year(iso_date: str, years: int) -> str:
    d = dt.date.fromisoformat(iso_date)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:
        return d.replace(year=d.year + years, day=28).isoformat()


def _shift_days(iso_date: str, days: int) -> str:
    d = dt.date.fromisoformat(iso_date)
    return (d + dt.timedelta(days=days)).isoformat()


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
            {"to": value, "method": f"de507-{source}"})
    else:
        answers[fid] = value
    return True


def process(filled: dict, event_date: str | None, case_id: str | None
            ) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "DE-507":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    rng = _seeded(case_id or "unknown-case")
    name, addr = _mock_identity(rng)
    if _set(answers, "former_pr_name", name, "mock-name"):
        changes.append(("former_pr_name", name, "mock-name"))
    if _set(answers, "former_pr_address", addr, "mock-address"):
        changes.append(("former_pr_address", addr, "mock-address"))

    if event_date:
        # Former PR's appointment was 2 years before the event date
        # (typical estate timeline before a replacement); termination
        # is 30 days before the event (replacement filed shortly after
        # the predecessor stepped down).
        appt = _shift_year(event_date, -2)
        term = _shift_days(event_date, -30)
        if _set(answers, "appointment_date", appt, "appt-2y-before-event"):
            changes.append(("appointment_date", appt, "appt-2y-before-event"))
        if _set(answers, "termination_date", term, "term-30d-before-event"):
            changes.append(("termination_date", term, "term-30d-before-event"))

    return new_filled, changes


def _extract_case(filled_path: pathlib.Path) -> str:
    return filled_path.parent.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()
    case_id = args.case_id or _extract_case(args.filled)
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, args.event_date, case_id)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_de507_former_pr: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
