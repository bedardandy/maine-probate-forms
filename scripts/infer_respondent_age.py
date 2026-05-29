"""Fill respondent_age (and DOB-and-age combos) on adult-guardianship petitions.

Why this exists:
  Adult-guardianship petitions PP-201, PP-205, PP-401 all have an
  "Item 5: Age of the Respondent" field (PP-205's version asks for
  DOB + age inline). The case-generator output rarely surfaces the
  respondent's DOB — it's not load-bearing for routing — so Qwen
  leaves the field blank (or hallucinates "0").

  This script reads the case file by case_id, derives age from
  respondent.dob if present, otherwise seeds a stable mock from the
  case_id hash (15–90 yr range — realistic adult).

Fields filled:
  respondent_age         ← integer age in years (e.g. "67")
  respondent_dob_age     ← "1957-04-12 (age 67)" combo (PP-205 Item 5)

Place in the fix chain: AFTER infer_signature_dates, BEFORE
recompute_overwrite. Does not depend on event_date.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys


CASES_PATH_DEFAULT = pathlib.Path("router/synthetic_cases.jsonl")
TODAY = dt.date.today()


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
    v = a.get("value") if isinstance(a, dict) else a
    return "" if v in (None, "", " ") else str(v).strip()


def _set(answers: dict, fid: str, value: str, source: str,
         create_if_missing: bool = False) -> bool:
    if fid not in answers:
        if not create_if_missing:
            return False
        answers[fid] = {"value": "", "confidence": 0.0}
    # Overwrite "0" too — Qwen sometimes guesses zero when the case is
    # silent on age. The deterministic value is always better.
    cur = _get(answers, fid)
    if cur and cur not in ("0", "0.0"):
        return False
    a = answers[fid]
    if isinstance(a, dict):
        a["value"] = value
        a["confidence"] = max(float(a.get("confidence") or 0), 0.85)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"respondent-age-{source}"})
    else:
        answers[fid] = value
    return True


def _lookup_case(case_id: str,
                 cases_path: pathlib.Path) -> dict | None:
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


def _respondent_dob(case: dict) -> str:
    parties = case.get("parties") or {}
    # Adult guardianship cases use `respondent` (the alleged-incapacitated
    # adult). Conservatorship may use `individual_under_protection`.
    for key in ("respondent", "individual_under_protection",
                "ward", "alleged_incapacitated"):
        p = parties.get(key)
        if isinstance(p, dict):
            dob = p.get("dob")
            if dob:
                return str(dob).strip()
    return ""


def _seed_dob(case_id: str) -> str:
    """Deterministic mock DOB → adult age 35–80 based on case_id hash.

    Stable per case so reruns produce identical output.
    """
    h = int(hashlib.sha256(case_id.encode()).hexdigest(), 16)
    age = 35 + (h % 46)  # 35..80
    year = TODAY.year - age
    month = 1 + (h // 100 % 12)
    day = 1 + (h // 10000 % 28)
    return f"{year:04d}-{month:02d}-{day:02d}"


def _age_from_dob(dob_iso: str) -> int | None:
    try:
        dob = dt.date.fromisoformat(dob_iso)
    except ValueError:
        return None
    years = TODAY.year - dob.year
    if (TODAY.month, TODAY.day) < (dob.month, dob.day):
        years -= 1
    return years if 0 < years < 120 else None


def process(filled: dict, case_id: str,
            cases_path: pathlib.Path) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    case = _lookup_case(case_id, cases_path) or {}
    dob = _respondent_dob(case)
    source = "case-dob"
    if not dob:
        dob = _seed_dob(case_id)
        source = "seed-dob"

    age = _age_from_dob(dob)
    if age is None:
        return new_filled, changes

    age_str = str(age)
    combo = f"{dob} (age {age})"

    # respondent_age must already exist in answers (it's a tree node on
    # PP-201/PP-401). respondent_dob_age is injected on PP-205 via
    # scripts/inject_text_widget.py and has no tree node — create the
    # answer entry if missing so form_filler picks it up.
    for fid, value, label, create in [
        ("respondent_age", age_str, "age", False),
        ("respondent_dob_age", combo, "dob+age", True),
    ]:
        if _set(answers, fid, value, f"{source}-{label}",
                create_if_missing=create):
            changes.append((fid, value, f"{source}-{label}"))

    return new_filled, changes


def _extract_case_id(filled_path: pathlib.Path) -> str:
    # filled_router.{event}.{form}.{stage}.json lives in
    # intermediate/router/{case_id}/
    return filled_path.parent.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--case-id", type=str, default=None,
                    help="Override case_id (otherwise inferred from path).")
    ap.add_argument("--cases-path", type=pathlib.Path,
                    default=CASES_PATH_DEFAULT,
                    help="synthetic_cases.jsonl source for respondent.dob")
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()

    case_id = args.case_id or _extract_case_id(args.filled)
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, case_id, args.cases_path)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_respondent_age: {len(changes)} field(s) populated "
          f"(case={case_id})")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
