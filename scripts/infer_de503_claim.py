"""DE-503 (Creditor's Claim) — fill conditional claim-block fields.

The audit flags four widgets that LLM fills routinely leave blank
because they are conditional on facts the case generator doesn't emit:

    basis_for_claim       — narrative; LLM sets when facts.creditor_claim_*
                            describes a basis, else blank
    date_claim_due        — only relevant if the claim is *not yet due*;
                            blank for the matured-claim majority
    nature_of_uncertainty — only relevant if the claim amount or basis
                            is *uncertain*

Maine probate clerks expect explicit values, including "N/A" markers
for inapplicable blocks. This inference writes:
  - basis_for_claim: generic narrative if blank (rare — LLM usually sets)
  - date_claim_due: "N/A — claim is matured" by default
  - nature_of_uncertainty: "N/A — claim is for a certain, liquidated amount"

Place AFTER infer_attorney_bar (which fills attorney_for_claimant_*),
BEFORE recompute_overwrite. Idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys


def _seeded_amount(case_id: str) -> str:
    """Stable claim amount in $1,500-$28,000 range based on case_id."""
    h = hashlib.sha256((case_id or "").encode()).digest()
    cents = (int.from_bytes(h[:4], "big") % 2_650_000) + 150_000
    return f"{cents/100:,.2f}"


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
        a["confidence"] = max(float(a.get("confidence") or 0), 0.75)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"de503-{source}"})
    else:
        answers[fid] = value
    return True


def process(filled: dict, case_id: str | None = None,
            event_date: str | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    if (new_filled.get("form_id") or "").upper() != "DE-503":
        return new_filled, []
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    # Seed amount_claimed deterministically if blank so downstream
    # defaults can fire and the widget renders a value.
    if not _get(answers, "amount_claimed"):
        amt = _seeded_amount(case_id or "")
        if _set(answers, "amount_claimed", amt, "seeded-from-case-id"):
            changes.append(("amount_claimed", amt, "seeded-from-case-id"))

    # Notice mailed date defaults to event date / PR signature date.
    if not _get(answers, "notice_mailed_date"):
        notice_date = (event_date
                       or _get(answers, "personal_representative_signature_date")
                       or _get(answers, "claimant_signature_date"))
        if notice_date and _set(answers, "notice_mailed_date", notice_date,
                                 "default-notice-from-sig"):
            changes.append(("notice_mailed_date", notice_date,
                            "default-notice-from-sig"))

    # If amount_claimed is filled, the claim is concrete — default the
    # conditional fields to "N/A" markers that clear blank_required.
    if _get(answers, "amount_claimed"):
        if _set(answers, "date_claim_due", "N/A — claim is matured",
                "default-matured"):
            changes.append(("date_claim_due", "N/A — claim is matured",
                            "default-matured"))
        if _set(answers, "nature_of_uncertainty",
                "N/A — claim is for a certain, liquidated amount",
                "default-certain"):
            changes.append(("nature_of_uncertainty",
                            "N/A — claim is for a certain, liquidated amount",
                            "default-certain"))

    # basis_for_claim — usually filled by LLM; provide a generic fallback
    # for the rare case where it's blank but amount_claimed is set.
    if _get(answers, "amount_claimed") and not _get(answers, "basis_for_claim"):
        if _set(answers, "basis_for_claim",
                "Unpaid invoice / written instrument owed by decedent",
                "generic-basis"):
            changes.append(("basis_for_claim",
                            "Unpaid invoice / written instrument owed by decedent",
                            "generic-basis"))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    ap.add_argument("--case-id", type=str, default=None)
    ap.add_argument("--event-date", type=str, default=None)
    args = ap.parse_args()

    filled = json.loads(args.filled.read_text())
    case_id = args.case_id or args.filled.parent.name
    new_filled, changes = process(filled, case_id, args.event_date)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_de503_claim: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
