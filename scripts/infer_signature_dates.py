"""Fill empty *signature-date* fields with the event date.

Why this exists:
  Qwen (and most well-aligned LLMs) refuse to fabricate dates the
  narrative doesn't explicitly tag. On almost every probate form there
  is a "Dated:" or "<role> Signature Date:" line which is, by
  convention, the date the form is signed and filed — i.e. the
  triggering event date. The narrative doesn't say "signature date:
  X" because that's a procedural fact about the act of signing, not
  a fact-pattern fact about the case. So the model leaves these
  fields blank and the rendered form has empty signature-date lines.

  Vision audit caught this on DE-503 page 1 (#284). Rather than try
  to coax the model into filling these (brittle), we do it
  deterministically here: any field whose ID matches a known
  signature-date pattern and whose current value is empty gets the
  event date stamped in.

Place in the fix chain: AFTER canonicalize_enums (so $-prefix is
already stripped) and AFTER infer_gates (so we don't write to fields
that have been gated out). Run BEFORE recompute_overwrite.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


# Field-name patterns that indicate "the date this form was signed".
# Tight enough to exclude legitimate non-signature dates like
# `notice_mailed_date`, `claim_filing_date`, `pr_appointment_date`,
# `decedent_date_of_death`, etc.
SIG_DATE_RE = re.compile(
    r"(?:^|_)(?:"
    r"signature_date|date_signed|signed_date|dated|"
    r"applicant_signature_date|petitioner_signature_date|"
    r"claimant_signature_date|pr_signature_date|"
    r"attorney_signature_date|guardian_signature_date|"
    r"conservator_signature_date|witness_signature_date|"
    r"signer_date|"
    # PP-412 Conservator Report's "Dated:" field is wired as
    # `report_date` (the date the report was signed/filed). Same
    # semantics as a signature_date.
    r"report_date|"
    # DE-406 Probate Account uses short names `pr_date` and `copr_date`
    # for the PR / Co-PR signature dates at the bottom of the page.
    r"pr_date|copr_date|"
    # PP-209 Guardian Status Report uses `guardian_date` / `co_guardian_date`.
    r"guardian_date|co_guardian_date|"
    # PP-407 Conservator Account uses `dated` (already matched by
    # DATED_BARE_RE below) plus `conservator_date` for the signature row.
    r"conservator_date|"
    # Defensive: notary_date is normally populated by
    # infer_notary_fields, but include here so the field is filled
    # even if the notary script is bypassed.
    r"notary_date|"
    # AF-102 Affidavit of Heirship: "Dated:" field above the affiant
    # signature is wired as `affidavit_date`. Same semantics as a
    # signature_date — the date the affidavit was signed/notarized.
    r"affidavit_date"
    r")$"
)
# Some forms have a `dated` field at the absolute end — match exact
# token "dated" or "_dated" as suffix only.
DATED_BARE_RE = re.compile(r"(?:^|_)dated$")


def _is_signature_date_field(fid: str, schema_entry: dict | None) -> bool:
    if SIG_DATE_RE.search(fid) or DATED_BARE_RE.search(fid):
        return True
    # Also catch fields the schema explicitly declares as
    # data_type=date AND id contains "sign" or "dated"
    if schema_entry and schema_entry.get("data_type") == "date":
        if "signature" in fid or "signed" in fid:
            return True
    return False


def process(schema: dict, filled: dict, event_date: str) -> tuple[dict, list]:
    new_filled = json.loads(json.dumps(filled))
    answers = new_filled.get("answers") or {}
    changes: list[tuple[str, str]] = []

    schema_by_id: dict[str, dict] = {}
    for f in schema.get("fields", []):
        schema_by_id[f["field_id"]] = f

    for fid, a in list(answers.items()):
        if not _is_signature_date_field(fid, schema_by_id.get(fid)):
            continue
        current = a.get("value") if isinstance(a, dict) else a
        if current not in (None, "", " "):
            continue
        if isinstance(a, dict):
            a["value"] = event_date
            a["confidence"] = max(float(a.get("confidence") or 0), 0.85)
            a.setdefault("infer_provenance", []).append(
                {"to": event_date, "method": "signature-date-from-event"})
        else:
            answers[fid] = event_date
        changes.append((fid, event_date))

    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", type=pathlib.Path, required=True)
    ap.add_argument("--filled", type=pathlib.Path, required=True,
                    help="Input gated.json (post-infer_gates).")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, required=True,
                    help="ISO date (YYYY-MM-DD) — the event/filing date.")
    args = ap.parse_args()

    schema = json.loads(args.schema.read_text())
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(schema, filled, args.event_date)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print(f"infer_signature_dates: {len(changes)} field(s) populated "
          f"with {args.event_date}")
    for fid, val in changes:
        print(f"  {fid} -> {val}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
