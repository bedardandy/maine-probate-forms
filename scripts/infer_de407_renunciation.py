"""DE-407 (Renunciation-Nomination) — fill declarant printed-name + address.

Audit (Phase 17) flagged:
  - Printed or Typed Name  → mirror `declarant_name` if blank
  - Address                → from case.parties.declarant (or petitioner)
  - Address (cont.)        → unused, leave blank

Form-gated to DE-407. AFTER infer_attorney_bar, BEFORE recompute_overwrite.
Idempotent.
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
        if not line.strip(): continue
        try: d = json.loads(line)
        except json.JSONDecodeError: continue
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
            {"to": value, "method": f"de407-{source}"})
    else:
        answers[fid] = value
    return True


def process(filled: dict, case_id: str | None = None,
            cases_path: pathlib.Path | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "DE-407":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    case = (_lookup_case(case_id, cases_path or CASES_PATH_DEFAULT)
            if case_id else None) or {}
    parties = case.get("parties") or {}

    # Printed name: mirror declarant_name
    declarant = _get(answers, "declarant_name")
    if declarant and not _get(answers, "printed_or_typed_name"):
        if _set(answers, "printed_or_typed_name", declarant,
                "mirror-declarant"):
            changes.append(("printed_or_typed_name", declarant,
                            "mirror-declarant"))

    # Address: prefer declarant party, fall back to petitioner
    addr = ""
    for key in ("declarant", "petitioner", "applicant"):
        p = parties.get(key) or {}
        if isinstance(p, dict) and p.get("address"):
            addr = p["address"]
            break
    if addr and not _get(answers, "address"):
        if _set(answers, "address", addr, "from-case-party"):
            changes.append(("address", addr, "from-case-party"))

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
    print(f"infer_de407_renunciation: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
