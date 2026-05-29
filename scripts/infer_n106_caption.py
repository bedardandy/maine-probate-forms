"""N-106 (Removal to Superior Court) — fill caption block.

N-106 uses a litigation-style caption (Plaintiff / Defendant) on a
form generated from probate parties (applicant + estate). The synth
case generator emits probate parties (petitioner, decedent, etc.)
which the LLM doesn't know to map. Result: Plaintiff/Defendant blank.

Mapping convention used here (matches Maine practice when probate
matters are removed to Superior Court):
  - Plaintiff  = removing party (typically the applicant/petitioner)
  - Defendant  = "Estate of [Decedent Name]"

Also fills county_probate_court / docket_number from case facts when
left empty, and county_for_order from the same.

Form-gated (N-106). Place AFTER notary, BEFORE recompute. Idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys


CASES_PATH_DEFAULT = pathlib.Path("router/synthetic_cases.jsonl")

# Looks like a real docket (e.g. "KEN-2024-EP-00892", "2025-EP-00403-ME")
DOCKET_LIKE_RE = re.compile(r"^[A-Z]{0,4}-?\d{4}-?[A-Z]{2,3}-?\d{3,6}(?:-[A-Z]{2})?$")


def _synth_docket(case_id: str) -> str:
    """If case_id looks like a docket, return it; otherwise hash-synth."""
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
            {"to": value, "method": f"n106-{source}"})
    else:
        answers[fid] = value
    return True


ME_COUNTIES = ("Cumberland","York","Penobscot","Kennebec","Androscoggin",
               "Aroostook","Hancock","Knox","Oxford","Sagadahoc","Somerset",
               "Waldo","Washington","Lincoln","Franklin","Piscataquis")


def _seeded_county(case_id: str) -> str:
    h = hashlib.sha256((case_id or "").encode()).digest()
    return ME_COUNTIES[h[0] % len(ME_COUNTIES)]


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
        if d.get("case", {}).get("case_id") == case_id:
            return d.get("case")
    return None


def process(filled: dict, case_id: str, cases_path: pathlib.Path,
            event_date: str | None = None
            ) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "N-106":
        return new_filled, []

    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    case = _lookup_case(case_id, cases_path) or {}
    parties = case.get("parties") or {}
    facts = case.get("facts") or {}

    # Determine plaintiff (removing party) and defendant.
    applicant = (parties.get("applicant") or parties.get("petitioner")
                 or parties.get("personal_representative") or {})
    decedent = parties.get("decedent") or {}
    decedent_name = decedent.get("full_name") if isinstance(decedent, dict) else ""
    applicant_name = applicant.get("full_name") if isinstance(applicant, dict) else ""

    if applicant_name and _set(answers, "plaintiff", applicant_name,
                                "from-applicant"):
        changes.append(("plaintiff", applicant_name, "from-applicant"))
    if decedent_name:
        defendant = f"Estate of {decedent_name}"
        if _set(answers, "defendant", defendant, "from-decedent"):
            changes.append(("defendant", defendant, "from-decedent"))

    # Removing party name field
    if applicant_name and _set(answers, "name_of_removing_party",
                                applicant_name, "from-applicant"):
        changes.append(("name_of_removing_party", applicant_name,
                        "from-applicant"))

    # County / docket — fall back to LLM-populated county_probate_court
    # already on this fill, then to facts, then to court block, then to
    # a stable hash-seeded Maine county (last-resort default).
    county = (_get(answers, "county_probate_court")
              or facts.get("county")
              or (case.get("court") or {}).get("county")
              or _seeded_county(case_id))
    docket = (facts.get("docket_no")
              or _synth_docket(case.get("case_id") or case_id))
    if county:
        if _set(answers, "county_probate_court", county, "from-existing-or-facts"):
            changes.append(("county_probate_court", county, "from-existing"))
        if _set(answers, "county_for_order", county, "from-county"):
            changes.append(("county_for_order", county, "from-county"))
    if docket and _set(answers, "docket_number", docket, "from-facts-or-synth"):
        changes.append(("docket_number", docket, "from-facts-or-synth"))

    # `date` field — the filing/signing date at the bottom of the form.
    if event_date and _set(answers, "date", event_date, "from-event-date"):
        changes.append(("date", event_date, "from-event-date"))

    return new_filled, changes


def _extract_case_id(filled_path: pathlib.Path) -> str:
    return filled_path.parent.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--cases-path", type=pathlib.Path,
                    default=CASES_PATH_DEFAULT)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    ap.add_argument("--event-date", type=str, default=None)
    args = ap.parse_args()

    case_id = args.case_id or _extract_case_id(args.filled)
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, case_id, args.cases_path,
                                   args.event_date)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_n106_caption: {len(changes)} field(s) populated "
          f"(case={case_id})")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
