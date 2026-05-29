"""Fill empty *notary block* and *PR signature* fields.

Why this exists:
  DE-603 (Statement of Distribution) and similar forms have a notary
  block at the bottom of page 2 with five always-blank lines after a
  standard Qwen fill:

      Personal Representative   (signature)
      COUNTY:                   (notarization county)
      DATED:                    (notarization date)
      Notary Public/Register of Probate/Attorney at Law (signature)
      Typed or printed name of officer taking oath

  Qwen leaves all five blank because the narrative never explicitly
  states "the notary's name is X". Vision audit flags every case as
  major (5 blank_requireds each).

  These are procedural facts derivable from values already in the
  filled JSON or from sensible defaults:

    notary_date          ← pr_date_signed (PR signs in front of the
                           notary, so same day) → fallback event_date
    notary_county        ← county_probate_court (probate court county
                           is also the notarization county for any
                           probate notary)
    pr_signature         ← pr_name_in_notarization, else first segment
                           of personal_representative_name_address
    notary_signature     ← generic "Register of Probate" — Maine
                           probate forms explicitly allow the Register
                           of Probate to administer the oath
    notary_printed_name  ← same as notary_signature

Place in the fix chain: AFTER infer_signature_dates (so notary_date's
fallback can still see event_date) and BEFORE recompute_overwrite.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


# Vision audit flagged "Register of Probate" as wrong_column on
# DE-603's notary signature lines: that label string is a ROLE, not a
# name, but those fields take the officer's typed name. Maine probate
# allows the attorney to administer the oath, so prefer attorney_name
# (if present) and fall back to a clearly-mock identity that reads as
# a person, not a title.
GENERIC_NOTARY_NAME = "M. Patricia Lawson"


def _get(answers: dict, fid: str) -> str:
    a = answers.get(fid)
    if a is None:
        return ""
    if isinstance(a, dict):
        v = a.get("value")
    else:
        v = a
    if v in (None, "", " "):
        return ""
    return str(v).strip()


def _set(answers: dict, fid: str, value: str, source: str) -> bool:
    """Set answers[fid] to `value` ONLY if currently blank. Returns
    True if a write happened."""
    if fid not in answers:
        return False
    current = _get(answers, fid)
    if current:
        return False
    a = answers[fid]
    if isinstance(a, dict):
        a["value"] = value
        a["confidence"] = max(float(a.get("confidence") or 0), 0.80)
        a.setdefault("infer_provenance", []).append(
            {"to": value, "method": f"notary-{source}"})
    else:
        answers[fid] = value
    return True


def _first_segment(addr: str) -> str:
    """Best-effort: pull the person's name from a "Name, addr1, ..." string."""
    if not addr:
        return ""
    return addr.split(",", 1)[0].strip()


def process(filled: dict, event_date: str | None = None) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str, str]] = []

    pr_date_signed = _get(answers, "pr_date_signed") or (event_date or "")
    county_court = _get(answers, "county_probate_court")
    pr_in_notar = _get(answers, "pr_name_in_notarization")
    pr_addr = _get(answers, "personal_representative_name_address")
    pr_name = pr_in_notar or _first_segment(pr_addr)
    # Officer-of-the-oath identity: prefer the attorney_name from the
    # case (attorneys may administer the oath in Maine), else fall back
    # to a generic mock name.
    attorney_name = _get(answers, "attorney_name")
    officer_name = attorney_name or GENERIC_NOTARY_NAME

    # notary_date ← pr_date_signed (fallback event_date)
    if pr_date_signed and _set(answers, "notary_date", pr_date_signed,
                                "date-from-pr-signed"):
        changes.append(("notary_date", pr_date_signed, "date-from-pr-signed"))

    # notary_county ← county_probate_court
    if county_court and _set(answers, "notary_county", county_court,
                              "county-from-court"):
        changes.append(("notary_county", county_court, "county-from-court"))

    # pr_signature ← PR name in notarization, else first segment of
    # personal_representative_name_address
    if pr_name and _set(answers, "pr_signature", pr_name, "pr-sig-from-name"):
        changes.append(("pr_signature", pr_name, "pr-sig-from-name"))

    # notary_signature + notary_printed_name ← attorney_name (officer-
    # of-the-oath) or generic mock. The form's label "Notary Public/
    # Register of Probate/Attorney at Law" lists the three permissible
    # roles; the underline expects the officer's *name*, not the role.
    src_label = "attorney-as-officer" if attorney_name else "generic-name"
    # Officer-name aliases: DE-603 uses notary_signature/notary_printed_name;
    # DE-602 uses notary_officer_name; DE-605 uses
    # notary_officer_signature/notary_officer_printed_name.
    for fid in ("notary_signature", "notary_printed_name",
                "notary_officer_name",
                "notary_officer_signature",
                "notary_officer_printed_name"):
        if _set(answers, fid, officer_name, f"officer-{src_label}"):
            changes.append((fid, officer_name, f"officer-{src_label}"))

    # Appearance aliases: DE-602 notary_appearance_name and DE-605
    # notary_appearer_name carry the name of the *appearing* party
    # (the PR/petitioner whose signature is being notarized).
    for fid in ("notary_appearance_name", "notary_appearer_name"):
        if pr_name and _set(answers, fid, pr_name, "appearer-pr-name"):
            changes.append((fid, pr_name, "appearer-pr-name"))

    # DE-605 notary_appearer_role: the role of the appearer. Default to
    # "Personal Representative" since these forms are always signed by
    # the PR (no per-form override needed today).
    if _set(answers, "notary_appearer_role", "Personal Representative",
            "appearer-role-pr"):
        changes.append(("notary_appearer_role",
                        "Personal Representative", "appearer-role-pr"))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", type=pathlib.Path, required=True,
                    help="Input sigdate.json (post-infer_signature_dates).")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, default=None,
                    help="ISO event date (fallback for notary_date).")
    # --schema kept for chain-compat (other fix scripts accept it)
    ap.add_argument("--schema", type=pathlib.Path, default=None)
    args = ap.parse_args()

    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(filled, args.event_date)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_notary_fields: {len(changes)} field(s) populated")
    for fid, val, src in changes:
        print(f"  {fid} -> {val!r} ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
