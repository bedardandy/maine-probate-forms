"""Fill the guardian/conservator closing block widgets.

PP-203 and similar guardian/conservator forms end with three widgets:

    Guardian            (signature/printed name line)
    By                  (delegated signer if signing for an organization;
                         for an individual guardian, repeats the name)
    Its                 (role of the appearer — "Guardian" or
                         "Conservator", NOT the relationship)

LLM fills are inconsistent: sometimes only `guardian_signature` is set,
sometimes only `guardian_by`, and `guardian_its` often ends up with the
relationship phrase ("adult daughter") instead of the role label. This
inference normalizes the block so all three slots carry a sensible
value, treating `guardian_signature` as the canonical name.

Place in the chain AFTER infer_notary_fields, BEFORE recompute_overwrite.
Idempotent and guarded — never overwrites a non-empty `guardian_signature`.
"""
from __future__ import annotations

import argparse
import json
import pathlib
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


# What role label to drop into the `_its` slot. PP-203 is a guardian
# acceptance/oath form so the role is always "Guardian". Add overrides
# here when new forms with different roles (conservator, etc.) need it.
ROLE_LABEL = "Guardian"
# Relationship terms that signal the LLM stuffed a relationship into
# the role field instead of the role itself. Detected so we overwrite.
RELATIONSHIP_HINTS = (
    "daughter", "son", "sister", "brother", "spouse", "husband", "wife",
    "mother", "father", "parent", "child", "aunt", "uncle", "cousin",
    "friend", "neighbor",
)


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
    v = a.get("value") if isinstance(a, dict) else a
    return "" if v in (None, "", " ") else str(v).strip()


def _set(answers: dict, fid: str, value: str, source: str,
         allow_overwrite: bool = False) -> bool:
    if fid not in answers:
        return False
    if not allow_overwrite and _get(answers, fid):
        return False
    a = answers[fid]
    if isinstance(a, dict):
        a["value"] = value
        a["confidence"] = max(float(a.get("confidence") or 0), 0.80)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"guardian-close-{source}"})
    else:
        answers[fid] = value
    return True


def process(filled: dict, case_id: str | None = None,
            cases_path: pathlib.Path | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    # PP-203 widgets W008/W009 (added in Phase 10): pull guardian address +
    # phone from the petitioner record (in guardianship cases, the
    # petitioner IS the proposed guardian who accepts the appointment).
    # These keys may be absent from canon (older fills predate the schema
    # extension) — inject them as empty entries first so _set can write.
    for new_fid in ("guardian_address", "guardian_telephone"):
        if new_fid not in answers:
            answers[new_fid] = {"value": "", "confidence": 0,
                                 "infer_provenance": []}
    case = (_lookup_case(case_id, cases_path or CASES_PATH_DEFAULT)
            if case_id else None) or {}
    parties = case.get("parties") or {}

    # respondent_name: in guardianship cases the LLM often leaves this
    # blank because the case dict uses "minor" or "individual_under_protection"
    # instead of "respondent". Backfill from any of those.
    if not _get(answers, "respondent_name"):
        for party_key in ("respondent", "minor", "individual_under_protection",
                          "ward", "incapacitated_person"):
            party = parties.get(party_key) or {}
            if isinstance(party, dict) and party.get("full_name"):
                if _set(answers, "respondent_name", party["full_name"],
                        f"from-{party_key}"):
                    changes.append(("respondent_name", party["full_name"],
                                    f"from-{party_key}"))
                break

    petitioner = parties.get("petitioner") or {}
    if isinstance(petitioner, dict):
        pet_addr = petitioner.get("address") or ""
        pet_phone = petitioner.get("phone") or ""
        if pet_addr and _set(answers, "guardian_address", pet_addr,
                              "from-petitioner-address"):
            changes.append(("guardian_address", pet_addr,
                            "from-petitioner-address"))
        if pet_phone and _set(answers, "guardian_telephone", pet_phone,
                               "from-petitioner-phone"):
            changes.append(("guardian_telephone", pet_phone,
                            "from-petitioner-phone"))
    new_filled["answers"] = answers

    sig = _get(answers, "guardian_signature")
    by = _get(answers, "guardian_by")
    its = _get(answers, "guardian_its")

    # Canonical name: whichever of signature/by is set, otherwise empty.
    name = sig or by
    if not name:
        return new_filled, changes

    if not sig and _set(answers, "guardian_signature", name, "promote-by"):
        changes.append(("guardian_signature", name, "promote-by"))
    if not by and _set(answers, "guardian_by", name, "mirror-signature"):
        changes.append(("guardian_by", name, "mirror-signature"))

    # _its should hold the role label, not the relationship. Overwrite
    # if it looks like a relationship phrase.
    its_lower = its.lower()
    its_is_relationship = its and any(h in its_lower for h in RELATIONSHIP_HINTS)
    if not its:
        if _set(answers, "guardian_its", ROLE_LABEL, "role-default"):
            changes.append(("guardian_its", ROLE_LABEL, "role-default"))
    elif its_is_relationship:
        if _set(answers, "guardian_its", ROLE_LABEL, "role-fix-relationship",
                allow_overwrite=True):
            changes.append(("guardian_its", ROLE_LABEL,
                            "role-fix-relationship"))

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
    new_filled, changes = process(filled, case_id, args.cases_path)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_guardian_closing: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
