"""Preserve signature-date fields unless the signing date is known.

Why this exists:
  Qwen (and most well-aligned LLMs) refuse to fabricate dates the
  narrative doesn't explicitly tag. On almost every probate form there
  is a "Dated:" or "<role> Signature Date:" line which is, by
  convention, the date the form is signed and filed — i.e. the
  triggering event date. The narrative doesn't say "signature date:
  X" because that's a procedural fact about the act of signing, not
  a fact-pattern fact about the case. So the model leaves these
  fields blank. That is correct unless the case data specifically
  records when the signer actually signed. A filing/event date is not
  evidence of the signing or notarization date.

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
    """Compatibility no-op.

    Do not synthesize signature dates from event_date. Existing explicit values
    are preserved; blank signing dates remain blank for the signer or notary.
    """
    new_filled = json.loads(json.dumps(filled))
    changes: list[tuple[str, str]] = []
    return new_filled, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", type=pathlib.Path, required=True)
    ap.add_argument("--filled", type=pathlib.Path, required=True,
                    help="Input gated.json (post-infer_gates).")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-date", type=str, required=True,
                    help="Retained for pipeline compatibility; never used as a "
                         "substitute for an unknown signing date.")
    args = ap.parse_args()

    schema = json.loads(args.schema.read_text())
    filled = json.loads(args.filled.read_text())
    new_filled, changes = process(schema, filled, args.event_date)
    args.out.write_text(json.dumps(new_filled, indent=2))
    print("infer_signature_dates: 0 fields populated; signing dates require "
          "an explicit known date")
    for fid, val in changes:
        print(f"  {fid} -> {val}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
