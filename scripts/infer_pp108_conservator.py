"""PP-108 (Acceptance of Appointment by Conservator — Minor) closing block.

Phase 12 follow-up. Audit (Opus 2026-05-17) flagged the two address /
phone widgets blank on PP-108. Schema gap mirrors PP-203 — the canon
predates the W008/W009 widget additions, so the answers dict has no
entries for `conservator_address` / `conservator_phone`.

Sources the values from `case.parties.petitioner` (in a conservatorship
of a minor, the petitioner IS the proposed conservator who accepts the
appointment, just like PP-203 maps petitioner -> guardian).

Form-gated to PP-108. AFTER infer_attorney_bar, BEFORE recompute_overwrite.
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
            {"to": value, "method": f"pp108-{source}"})
    else:
        answers[fid] = value
    return True


def process(filled: dict, case_id: str | None = None,
            cases_path: pathlib.Path | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() not in ("PP-108", "PP-402"):
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    # Schema-extension widgets — answers dict may lack them. Only
    # pre-create conservator_address if the form actually uses it
    # (PP-108 yes, PP-402 already has it as a tree node).
    fid_form = (new_filled.get("form_id") or "").upper()
    schema_fids = ("conservator_address", "conservator_phone") if fid_form == "PP-108" \
                  else ("conservator_phone",)
    for new_fid in schema_fids:
        if new_fid not in answers:
            answers[new_fid] = {"value": "", "confidence": 0,
                                 "infer_provenance": []}

    case = (_lookup_case(case_id, cases_path or CASES_PATH_DEFAULT)
            if case_id else None) or {}
    parties = case.get("parties") or {}
    # Prefer the explicit conservator record (PP-402); fall back to
    # petitioner (PP-108 — petitioner IS the proposed conservator).
    source = parties.get("conservator") or parties.get("petitioner") or {}
    src_label = "conservator" if parties.get("conservator") else "petitioner"

    if isinstance(source, dict):
        addr = source.get("address") or ""
        phone = source.get("phone") or ""
        if addr and _set(answers, "conservator_address", addr,
                          f"from-{src_label}-address"):
            changes.append(("conservator_address", addr,
                            f"from-{src_label}-address"))
        if phone and _set(answers, "conservator_phone", phone,
                           f"from-{src_label}-phone"):
            changes.append(("conservator_phone", phone,
                            f"from-{src_label}-phone"))

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
    print(f"infer_pp108_conservator: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
