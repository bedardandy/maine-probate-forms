"""AF-103 (Affidavit of Name Change for Adult) — fill narrative blanks.

Audit residuals (Phase 17):
  children_status, minor_children_details, new_name_is_former_spouse,
  affiant_date — all left blank by the LLM canon because the case
  generator doesn't carry these specifics.

Form-gated to AF-103. AFTER infer_attorney_bar, BEFORE recompute_overwrite.
Idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
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
            {"to": value, "method": f"af103-{source}"})
    else:
        answers[fid] = value
    return True


def process(filled: dict, case_id: str | None = None,
            event_date: str | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "AF-103":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    # children_status: seeded yes/no based on case_id
    h = hashlib.sha256((case_id or "").encode()).digest()
    has_minor = (h[0] % 4 == 0)  # 25% have minor children
    if not _get(answers, "children_status"):
        v = "Yes" if has_minor else "No"
        if _set(answers, "children_status", v, "seeded-children"):
            changes.append(("children_status", v, "seeded-children"))

    if not _get(answers, "minor_children_details"):
        v = ("None — affiant has no minor children" if not has_minor
             else "1 minor child, age 12, residing with affiant")
        if _set(answers, "minor_children_details", v, "default-no-minors"):
            changes.append(("minor_children_details", v, "default-no-minors"))

    if not _get(answers, "new_name_is_former_spouse"):
        if _set(answers, "new_name_is_former_spouse", "No",
                "default-not-former-spouse"):
            changes.append(("new_name_is_former_spouse", "No",
                            "default-not-former-spouse"))

    if not _get(answers, "affiant_date") and event_date:
        if _set(answers, "affiant_date", event_date, "from-event-date"):
            changes.append(("affiant_date", event_date, "from-event-date"))

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
    new_filled, changes = process(filled, case_id, args.event_date)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_af103_name_change: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
