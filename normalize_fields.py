#!/usr/bin/env python3
"""
normalize_fields.py — Targeted field name cleanup pass for realigned probate forms.

Goals:
1. Resolve cross-concept duplicates (docket_no vs docket_number, etc.)
2. Strip text_ prefix from fields (AcroForm artifact)
3. Normalize mixed-case to snake_case
4. Standardize address line naming (attorney_address_street / attorney_address_city_state_zip)
5. Ensure shared common fields use identical names across all 104 forms

Run: python3 normalize_fields.py [--dry-run] [--verbose]
"""

import fitz
import shutil
import re
import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict

DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

OUTPUT_DIR = Path("output_realigned")
BACKUP_DIR = Path("output_realigned_prenorm_backup")


# ─────────────────────────────────────────────────────────────────────────────
# CANONICAL NAME MAP
# Maps any variant → canonical form
# ─────────────────────────────────────────────────────────────────────────────

CANONICAL = {
    # Docket number — use docket_no everywhere (most common)
    "docket_number":              "docket_no",
    "docket_number_2":            "docket_no_2",

    # County/court — use county_probate_court as canonical (most descriptive)
    # "county" alone is ambiguous; county_probate_court is explicit
    "county":                     "county_probate_court",
    "county_2":                   "county_probate_court_2",
    "county_name":                "county_probate_court",
    # NOTE: 3 forms have collision if county_name → county_probate_court
    # because they already have a county_probate_court field AND a separate
    # county_name used for notary/register context.
    # These are handled via PER_FILE_CANONICAL overrides below.

    # Attorney phone — use attorney_phone (shorter, consistent)
    "attorney_phone_number":      "attorney_phone",

    # Attorney address — standardize lines
    # attorney_address_1 = street line 1, attorney_address_2 = city/state/zip line
    # text_ prefixed versions → drop prefix
    "text_attorney_address_1":    "attorney_address_1",
    "text_attorney_address_2":    "attorney_address_2",
    # Plain "attorney_address" (no line number) → attorney_address_1
    "attorney_address":           "attorney_address_1",

    # text_ prefixed attorney fields — strip prefix
    "text_attorney_name":         "attorney_name",
    "text_attorney_bar_number":   "attorney_bar_number",
    "text_attorney_main_bar_number": "attorney_bar_number",
    "text_attorney_email":        "attorney_email",
    "text_attorney_email_address":"attorney_email",
    "text_attorney_phone":        "attorney_phone",
    "text_attorney_phone_number": "attorney_phone",

    # Petitioner signature — use petitioner_signature (subject-first)
    "signature_petitioner":       "petitioner_signature",
    "signature_petitioner_1":     "petitioner_signature_1",
    "signature_petitioner_2":     "petitioner_signature_2",

    # Decedent/subject name — use decedent_name (correct spelling)
    "decendent_name":             "decedent_name",  # fix typo

    # Date variations — context-specific, only normalize the generic ones
    # date_dated → date_signed (more explicit about what it is)
    "date_dated":                 "date_signed",
    "date_dated_1":               "date_signed_1",
    "date_dated_2":               "date_signed_2",
    "date_Dated":                 "date_signed",

    # Mixed-case / broken names — normalize to snake_case
    "text_County":                "county_probate_court",
    "text_Name_of_parent":        "name_of_parent",
    "text_A_True_Copy_Attest":    "a_true_copy_attest",

    # Custodian text_ prefixed
    "text_custodian_address":     "custodian_address",
    "text_custodian_current_address": "custodian_current_address",
    "text_custodian_date":        "custodian_date",
    "text_custodian_name":        "custodian_name",
    "text_custody_agency_details":   "custody_agency_details",
    "text_custody_dhhs_details":     "custody_dhhs_details",
    "text_custody_other_details":    "custody_other_details",
    "text_custody_petitioner_details": "custody_petitioner_details",
    "text_persons_affecting_custody": "persons_affecting_custody",

    # Mixed-case field names → snake_case
    "date_Date_Judge_Withdrawal_or_Revocation": "date_judge_withdrawal_or_revocation",
    "date_Date_Order_of_Approval":              "date_order_of_approval",
    "date_Date_Withdrawal_or_Revocation":       "date_withdrawal_or_revocation",
    "date_Date_before_me":                      "date_before_me",
    "signature_Signature_of_Judge":             "signature_judge",
    "signature_Signature_of_Judge_Order_of_Approval":       "signature_judge_order_of_approval",
    "signature_Signature_of_Judge_Withdrawal_or_Revocation":"signature_judge_withdrawal_or_revocation",
    "signature_Signature_of_Parent_Withdrawal_or_Revocation":"signature_parent_withdrawal_or_revocation",
    "signature_Signature_of_Register_Clerk":    "signature_register_clerk",
    "signature_Signature_of_surrendering_parent": "signature_surrendering_parent",
    "signature_D":                "signature_d",
    "signature_E":                "signature_e",
    "signature_F":                "signature_f",
    "checkbox_C":                 "checkbox_c",
    "checkbox_D":                 "checkbox_d",
    "checkbox_E":                 "checkbox_e",
    "checkbox_F":                 "checkbox_f",
    "conservator_choice_A":       "conservator_choice_a",
    "conservator_choice_B":       "conservator_choice_b",
    "guardian_choice_A":          "guardian_choice_a",
    "guardian_choice_B":          "guardian_choice_b",
    "no_checkbox_G":              "no_checkbox_g",
    "no_checkbox_H":              "no_checkbox_h",
    "no_checkbox_I":              "no_checkbox_i",
    "no_checkbox_J":              "no_checkbox_j",
    "no_checkbox_K":              "no_checkbox_k",
    "yes_checkbox_G":             "yes_checkbox_g",
    "yes_checkbox_H":             "yes_checkbox_h",
    "yes_checkbox_I":             "yes_checkbox_i",
    "yes_checkbox_J":             "yes_checkbox_j",
    "yes_checkbox_K":             "yes_checkbox_k",
    "icwa_name_of_trIBE":         "icwa_name_of_tribe",
    "question_10_name_of_trIBE_field": "question_10_name_of_tribe",
    "attorney_main_bar_number":   "attorney_bar_number",
}


# Per-file overrides: { filename_stem_fragment: { old_name: new_name } }
# Used when a global canonical mapping would create a collision.
PER_FILE_CANONICAL = {
    # AD-009: has county (header) AND county_name (notary county) — different fields
    "AD-009 Certificate of Counseling": {
        "county_name": "county_notary",
    },
    # DE-605: county_name appears twice — once as notary county (p0), once as court header (p1)
    # Handled by position logic in process_pdf below.
    "DE-605 Verified Application for Certificate of Discharge": {
        # p0 county_name near notary → county_notary
        # p1 county_name in court header → county_probate_court (already canonical)
        # We'll skip county_name→county_probate_court globally for this file
        "county_name": "county_notary",   # default override; p1 gets fixed specially
    },
    # N-112: county_name near Register of Probate attest block
    "N-112 Notice of Intent to Register Guardianship or Conservatorship": {
        "county_name": "county_register",
    },
}


def canonical(name: str, file_stem: str = "") -> str:
    """Return the canonical form of a field name, or the name itself.
    Per-file overrides take precedence over global CANONICAL map.
    """
    for key, overrides in PER_FILE_CANONICAL.items():
        if key in file_stem:
            if name in overrides:
                return overrides[name]
    return CANONICAL.get(name, name)


def process_pdf(pdf_path: Path, dry_run: bool = True) -> dict:
    """Rename fields in a PDF according to the canonical map. Returns stats."""
    doc = fitz.open(pdf_path)
    file_stem = pdf_path.stem
    changes = []
    skipped = []

    for page_num, page in enumerate(doc):
        for widget in page.widgets():
            old_name = widget.field_name or ""
            # Special case: DE-605 page 1 county_name is the court header
            if "DE-605" in file_stem and page_num == 1 and old_name == "county_name":
                new_name = "county_probate_court"
            else:
                new_name = canonical(old_name, file_stem)
            if new_name != old_name:
                changes.append((old_name, new_name))
                if not dry_run:
                    widget.field_name = new_name
                    widget.update()

    if not dry_run and changes:
        doc.save(pdf_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    return {
        "file": pdf_path.name,
        "changes": changes,
        "skipped": skipped,
    }


def main():
    parser = argparse.ArgumentParser(description="Normalize probate form field names")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all renames")
    parser.add_argument("--backup", action="store_true", help="Backup output_realigned before changes")
    args = parser.parse_args()

    pdfs = sorted(OUTPUT_DIR.rglob("*.pdf"))
    print(f"{'[DRY RUN] ' if args.dry_run else ''}Processing {len(pdfs)} PDFs in {OUTPUT_DIR}/")
    print(f"Canonical rules: {len(CANONICAL)} mappings\n")

    if args.backup and not args.dry_run:
        if BACKUP_DIR.exists():
            shutil.rmtree(BACKUP_DIR)
        shutil.copytree(OUTPUT_DIR, BACKUP_DIR)
        print(f"✅ Backup created: {BACKUP_DIR}/\n")

    total_changes = 0
    total_files_changed = 0
    all_results = []

    for pdf_path in pdfs:
        try:
            result = process_pdf(pdf_path, dry_run=args.dry_run)
            all_results.append(result)
            n = len(result["changes"])
            if n > 0:
                total_changes += n
                total_files_changed += 1
                status = "  (dry)" if args.dry_run else "  ✅"
                print(f"{status} {pdf_path.name}: {n} rename(s)")
                if args.verbose:
                    for old, new in result["changes"]:
                        print(f"       {old!r} → {new!r}")
        except Exception as e:
            print(f"  ❌ ERROR {pdf_path.name}: {e}")

    print(f"\n{'─'*60}")
    print(f"{'[DRY RUN] ' if args.dry_run else ''}Done.")
    print(f"  Files changed:  {total_files_changed}/{len(pdfs)}")
    print(f"  Total renames:  {total_changes}")

    # Save results JSON
    report_path = Path("normalize_results.json")
    with open(report_path, "w") as f:
        json.dump({
            "dry_run": args.dry_run,
            "total_pdfs": len(pdfs),
            "files_changed": total_files_changed,
            "total_renames": total_changes,
            "canonical_rules": CANONICAL,
            "results": all_results,
        }, f, indent=2)
    print(f"  Report:         {report_path}")

    if args.dry_run:
        print("\nRun without --dry-run (add --backup) to apply changes.")


if __name__ == "__main__":
    main()
