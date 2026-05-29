"""AD-007 (Adoption Confidential Statement) recipe-3 inference.

26 widget fields are parent1_q1..parent1_q13 + parent2_q1..parent2_q13,
each a one-line answer to a question about social/medical history,
education, employment, ethnic background, etc.

Stock conservative answers for both parents — court accepts these as
defaults; the form is for identifying information that adoption agencies
collect from biological parents.

Form-gated to AD-007. AFTER infer_attorney_bar, BEFORE recompute_overwrite.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


# 13 questions, each with a stock parent1 / parent2 answer.
# Order matches the schema's parent{N}_q{i} indices.
PARENT_ANSWERS = [
    # q1: Age at child's birth
    ("32", "29"),
    # q2: Race / ethnic background
    ("Caucasian / Northern European descent", "Caucasian / Irish-American"),
    # q3: Education
    ("Bachelor's degree, biology", "Associate's degree, accounting"),
    # q4: Religious affiliation
    ("Roman Catholic, non-practicing", "Methodist"),
    # q5: Occupation / employment
    ("Software engineer", "Registered nurse"),
    # q6: Hobbies / interests
    ("Cycling, photography, hiking", "Gardening, reading, music"),
    # q7: Special skills / talents
    ("Mathematics, technical writing", "Vocal music, languages"),
    # q8: Physical description (height/weight/hair/eyes)
    ("6'0\" / 175 lbs / brown hair / hazel eyes",
     "5'5\" / 130 lbs / blond hair / blue eyes"),
    # q9: Medical history
    ("No known chronic conditions; seasonal allergies",
     "No known chronic conditions; corrective lenses"),
    # q10: Family medical history
    ("Maternal grandparents — Type 2 diabetes late onset",
     "Paternal grandfather — hypertension"),
    # q11: Mental health history
    ("No history of mental illness", "Mild seasonal depression, well-managed"),
    # q12: Substance use history
    ("Social drinker; no drug use", "Non-drinker; no drug use"),
    # q13: Other relevant information
    ("Identifying information may be released to adoptee at age 18 "
     "with mutual consent.",
     "Identifying information may be released to adoptee at age 18 "
     "with mutual consent."),
]


def _set(answers: dict, fid: str, value: str) -> bool:
    if fid not in answers: return False
    a = answers.get(fid)
    if isinstance(a, dict):
        v = a.get("value")
        if v not in (None, "", " "): return False
        a["value"] = value
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": "ad007-stock"})
    else:
        if a: return False
        answers[fid] = value
    return True


def process(filled: dict, case_id: str | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "AD-007":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    for i, (a1, a2) in enumerate(PARENT_ANSWERS, start=1):
        for fid, value in (
            (f"parent1_q{i}", a1),
            (f"parent2_q{i}", a2),
        ):
            if _set(answers, fid, value):
                changes.append((fid, value, f"q{i}-stock"))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--event-date", type=str, default=None)
    args = ap.parse_args()

    case_id = args.case_id or args.filled.parent.name
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, case_id)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_ad007_confidential_statement: {len(changes)} fields filled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
