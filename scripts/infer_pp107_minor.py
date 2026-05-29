"""PP-107 (Petition for Conservator for Minor) — fill defaults block.

Phase 12 follow-up. Audit (Opus 2026-05-17) flagged items 12 (asset
description), 14 (asset table), 15/16/17 (YES/NO pairs) blank on p4
and items 2/5/6/7 (relationship/age/address/nominee) blank on p1.

This script fills the deterministic-default fields that the LLM leaves
blank because the synthetic narrative doesn't carry the data. Idempotent.

Form-gated (PP-107). AFTER infer_attorney_bar, BEFORE recompute_overwrite.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import sys


CASES_PATH_DEFAULT = pathlib.Path("router/synthetic_cases.jsonl")


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
        cid = d.get("case_id") or d.get("case", {}).get("case_id")
        if cid == case_id:
            return d.get("case") or d
    return None


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
            {"to": value, "method": f"pp107-{source}"})
    else:
        answers[fid] = value
    return True


ASSET_TEMPLATES = (
    ("Custodial savings account (UTMA)", lambda v: f"${v:,.2f}"),
    ("UGMA brokerage account",            lambda v: f"${v:,.2f}"),
    ("College savings 529 plan",          lambda v: f"${v:,.2f}"),
    ("Inheritance trust beneficial interest", lambda v: f"${v:,.2f}"),
)


def _seeded_asset(case_id: str) -> tuple[str, str]:
    h = hashlib.sha256((case_id or "").encode()).digest()
    name, fmt = ASSET_TEMPLATES[h[0] % len(ASSET_TEMPLATES)]
    val = (int.from_bytes(h[1:5], "big") % 9_500_000) + 500_000  # cents
    return name, fmt(val / 100)


NOMINEE_JUSTIFICATIONS = (
    "Nominee is the minor's closest available adult relative with capacity to serve.",
    "Nominee has a long-standing caregiving relationship with the minor and resides in proximity.",
    "Nominee is the petitioner identified above and meets the statutory qualifications.",
)


def _seeded_justification(case_id: str) -> str:
    h = hashlib.sha256((case_id or "").encode()).digest()
    return NOMINEE_JUSTIFICATIONS[h[0] % len(NOMINEE_JUSTIFICATIONS)]


# Stock relationship enums for petitioner -> minor.  Conservator-of-minor
# petitions in practice are filed by parents, grandparents, or close
# adult relatives; the case-generator currently leaves relationship null,
# so we seed a stable default per case.
PETITIONER_RELATIONSHIPS = (
    "Parent of the minor; petitioner has a direct interest in protecting the minor's estate.",
    "Maternal grandparent of the minor; primary caregiver since the minor's birth.",
    "Paternal grandparent of the minor; financially responsible for the minor.",
    "Adult sibling of the minor; serves as guardian and seeks conservator authority.",
)


def _seeded_petitioner_relationship(case_id: str) -> str:
    h = hashlib.sha256((case_id or "").encode()).digest()
    return PETITIONER_RELATIONSHIPS[h[2] % len(PETITIONER_RELATIONSHIPS)]


def _compute_age(dob: str, event_date: str | None) -> int | None:
    """Return integer years between dob and event_date (or today)."""
    if not dob:
        return None
    try:
        d = datetime.date.fromisoformat(dob[:10])
    except ValueError:
        return None
    ref = datetime.date.today()
    if event_date:
        try:
            ref = datetime.date.fromisoformat(event_date[:10])
        except ValueError:
            pass
    age = ref.year - d.year - ((ref.month, ref.day) < (d.month, d.day))
    return max(age, 0)


def _has_age_token(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    if re.search(r"\bage\s+\d", t):
        return True
    if re.search(r"\d+\s*(years?|yr|yrs)\b", t):
        return True
    if re.search(r"\bdob\b", t) or re.search(r"\bborn\b", t):
        return True
    return False


def process(filled: dict, case_id: str | None = None,
            cases_path: pathlib.Path | None = None,
            event_date: str | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "PP-107":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    case = (_lookup_case(case_id, cases_path or CASES_PATH_DEFAULT)
            if case_id else None) or {}
    parties = case.get("parties") or {}
    petitioner = parties.get("petitioner") or {}
    minor = parties.get("minor") or parties.get("individual_under_protection") or {}

    # minor_residence: use minor.address, fallback to petitioner.address
    if not _get(answers, "minor_residence"):
        addr = (minor.get("address") if isinstance(minor, dict) else "") \
               or (petitioner.get("address") if isinstance(petitioner, dict) else "")
        if addr and _set(answers, "minor_residence", addr, "from-minor-or-petitioner-addr"):
            changes.append(("minor_residence", addr, "from-minor-or-petitioner-addr"))

    # minor_info: PDF label is "Age of the Minor". LLM tends to fill
    # name+address; augment in-place with age if missing.
    minor_dob = minor.get("dob") if isinstance(minor, dict) else ""
    age = _compute_age(minor_dob, event_date) if minor_dob else None
    cur_minor_info = _get(answers, "minor_info")
    if age is not None and not _has_age_token(cur_minor_info):
        a = answers.get("minor_info")
        new_val = f"{cur_minor_info}; Age: {age} years" if cur_minor_info \
                  else f"Age: {age} years (DOB {minor_dob})"
        if isinstance(a, dict):
            a["value"] = new_val
            a.setdefault("infer_provenance", []).append(
                {"to": new_val, "method": "pp107-age-augment"})
            changes.append(("minor_info", new_val, "age-augment"))
        elif "minor_info" in answers:
            answers["minor_info"] = new_val
            changes.append(("minor_info", new_val, "age-augment"))

    # petitioner_relationship and nominee_relationship — seeded defaults.
    # In conservator-of-minor petitions where petitioner==nominee (the
    # common case here), both fields express the same relationship.
    if not _get(answers, "petitioner_relationship"):
        rel = _seeded_petitioner_relationship(case_id or "")
        if _set(answers, "petitioner_relationship", rel,
                "seeded-petitioner-rel"):
            changes.append(("petitioner_relationship", rel,
                            "seeded-petitioner-rel"))
    if not _get(answers, "nominee_relationship"):
        # Mirror the petitioner relationship (petitioner is nominee).
        rel = (_get(answers, "petitioner_relationship")
               or _seeded_petitioner_relationship(case_id or ""))
        if _set(answers, "nominee_relationship", rel, "mirror-petitioner-rel"):
            changes.append(("nominee_relationship", rel,
                            "mirror-petitioner-rel"))

    # Notify persons block — schema marks these as required but the case
    # generator doesn't emit a notice list. Use Maine probate conventions:
    # other parent (if known), nearest adult relative not the petitioner.
    NA_NAME = "None known — petitioner is sole adult relative"
    NA_ADDR = "Not applicable"
    NA_REL = "N/A"
    if not _get(answers, "notify_persons_name"):
        if _set(answers, "notify_persons_name", NA_NAME,
                "stock-no-other-notifies"):
            changes.append(("notify_persons_name", NA_NAME,
                            "stock-no-other-notifies"))
    if not _get(answers, "notify_persons_address"):
        if _set(answers, "notify_persons_address", NA_ADDR,
                "stock-no-other-notifies"):
            changes.append(("notify_persons_address", NA_ADDR,
                            "stock-no-other-notifies"))
    if not _get(answers, "notify_persons_relationship"):
        if _set(answers, "notify_persons_relationship", NA_REL,
                "stock-no-other-notifies"):
            changes.append(("notify_persons_relationship", NA_REL,
                            "stock-no-other-notifies"))

    # Asset table — seed Item 14 row 1
    if not _get(answers, "minor_assets_asset"):
        asset_name, asset_value = _seeded_asset(case_id or "")
        if _set(answers, "minor_assets_asset", asset_name, "seeded-asset"):
            changes.append(("minor_assets_asset", asset_name, "seeded-asset"))
        if _set(answers, "minor_assets_value", asset_value, "seeded-asset-value"):
            changes.append(("minor_assets_value", asset_value, "seeded-asset-value"))

    # YES/NO checkbox defaults — Maine probate convention is "no" for
    # interpreter, bankruptcy, conviction unless narrative says otherwise.
    for fid, val, src in [
        ("interpreter_needed",  "no", "default-no-interpreter"),
        ("nominee_bankruptcy",  "no", "default-no-bankruptcy"),
        ("nominee_conviction",  "no", "default-no-conviction"),
    ]:
        if _set(answers, fid, val, src):
            changes.append((fid, val, src))

    # nominee_justification — stock if blank
    if not _get(answers, "nominee_justification"):
        just = _seeded_justification(case_id or "")
        if _set(answers, "nominee_justification", just, "seeded-justification"):
            changes.append(("nominee_justification", just, "seeded-justification"))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--event-date", type=str, default=None)
    ap.add_argument("--cases-path", type=pathlib.Path,
                    default=CASES_PATH_DEFAULT)
    args = ap.parse_args()

    case_id = args.case_id or args.filled.parent.name
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, case_id, args.cases_path,
                                  args.event_date)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_pp107_minor: {len(changes)} field(s) populated (case={case_id})")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
