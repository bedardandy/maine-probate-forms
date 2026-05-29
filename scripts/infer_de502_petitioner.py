"""DE-502 (Demand for Bond) — fill petitioner block items 1/2/3.

Phase 11 widget injection added three widgets:
  petitioner_name_and_address  W015 (multiline)
  petitioner_interest_nature   W016 (multiline)
  petitioner_interest_value    W017 (single)

These widgets weren't in the canon stage (the LLM didn't see them),
so we deterministically fill from the case dict's petitioner record.
Interest nature/value default to "beneficiary" framing because DE-502
is filed by an interested party demanding the PR post a bond.

Form-gated (DE-502). AFTER infer_attorney_bar, BEFORE recompute_overwrite.
Idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
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
            {"to": value, "method": f"de502-{source}"})
    else:
        answers[fid] = value
    return True


def _seeded_value(case_id: str) -> str:
    """Seed an interest value in $5,000-$80,000 range based on case_id."""
    h = hashlib.sha256((case_id or "").encode()).digest()
    cents = (int.from_bytes(h[:4], "big") % 7_500_000) + 500_000
    return f"${cents/100:,.2f}"


INTEREST_NATURE_OPTIONS = (
    "Beneficiary under the Decedent's Will",
    "Heir at law of the Decedent",
    "Creditor with outstanding claim against the Estate",
    "Devisee of specific real property under the Will",
)


def _seeded_nature(case_id: str) -> str:
    h = hashlib.sha256((case_id or "").encode()).digest()
    return INTEREST_NATURE_OPTIONS[h[0] % len(INTEREST_NATURE_OPTIONS)]


def process(filled: dict, case_id: str | None = None,
            cases_path: pathlib.Path | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "DE-502":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    # Inject empty keys if missing (canon predates schema extension)
    for fid in ("petitioner_name_and_address",
                "petitioner_interest_nature",
                "petitioner_interest_value"):
        if fid not in answers:
            answers[fid] = {"value": "", "confidence": 0,
                            "infer_provenance": []}

    case = (_lookup_case(case_id, cases_path or CASES_PATH_DEFAULT)
            if case_id else None) or {}
    parties = case.get("parties") or {}
    petitioner = (parties.get("petitioner")
                  or parties.get("applicant") or {})
    if isinstance(petitioner, dict):
        name = petitioner.get("full_name") or ""
        addr = petitioner.get("address") or ""
        name_addr = f"{name}, {addr}".strip(", ") if name or addr else ""
        if name_addr and _set(answers, "petitioner_name_and_address",
                               name_addr, "from-petitioner"):
            changes.append(("petitioner_name_and_address", name_addr,
                            "from-petitioner"))

    # Interest nature: seeded enum (or leave LLM value if present)
    if not _get(answers, "petitioner_interest_nature"):
        nat = _seeded_nature(case_id or "")
        if _set(answers, "petitioner_interest_nature", nat, "seeded-nature"):
            changes.append(("petitioner_interest_nature", nat, "seeded-nature"))

    # Interest value: deterministic seed
    if not _get(answers, "petitioner_interest_value"):
        val = _seeded_value(case_id or "")
        if _set(answers, "petitioner_interest_value", val, "seeded-value"):
            changes.append(("petitioner_interest_value", val, "seeded-value"))

    new_filled["answers"] = answers
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
    print(f"infer_de502_petitioner: {len(changes)} field(s) populated "
          f"(case={case_id})")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
