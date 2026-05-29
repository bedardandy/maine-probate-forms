"""Apply high-confidence naming fixes from audit reports.

Parses audit-report `details` text for `should be 'X'` / `rename to 'X'` /
`should be named 'X'` patterns. When the suggested name is a clean snake_case
identifier and the current widget exists in the PDF, applies the rename.

Usage:
  scripts/apply_naming_fixes.py                                # dry-run all forms
  scripts/apply_naming_fixes.py --apply                        # write fixed PDFs
  scripts/apply_naming_fixes.py --reports reports/opus-alignment-fused-full \\
                                --pdfs   output_fused \\
                                --out    output_fused_renamed
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Patterns ordered most-specific → general. Group 1 captures the suggested name.
RENAME_PATTERNS = [
    # Quoted name (most specific)
    re.compile(r"should be named\s+['\"`]([a-z][a-z0-9_]{2,40})['\"`]", re.IGNORECASE),
    re.compile(r"rename(?:d)?(?:\s+it)?\s+to\s+['\"`]([a-z][a-z0-9_]{2,40})['\"`]", re.IGNORECASE),
    re.compile(r"should be\s+['\"`]([a-z][a-z0-9_]{2,40})['\"`]", re.IGNORECASE),
    re.compile(r"name should be\s+['\"`]([a-z][a-z0-9_]{2,40})['\"`]", re.IGNORECASE),
    # Unquoted snake_case (Opus often omits quotes — must contain underscore
    # to avoid matching common words like 'standard' or 'expanded')
    re.compile(r"should be named\s+([a-z][a-z0-9]*_[a-z0-9_]{2,40})\b", re.IGNORECASE),
    re.compile(r"rename(?:d)?(?:\s+it)?\s+to\s+([a-z][a-z0-9]*_[a-z0-9_]{2,40})\b", re.IGNORECASE),
    re.compile(r"should be\s+([a-z][a-z0-9]*_[a-z0-9_]{2,40})\b", re.IGNORECASE),
]

# Phrases that indicate the suggestion is uncertain — skip when these appear.
UNCERTAIN_HINTS = re.compile(
    r"\b(e\.g\.|maybe|perhaps|or similar|something like|or\s+['\"`])",
    re.IGNORECASE,
)


def extract_rename(details: str) -> str | None:
    """Return suggested snake_case name if details unambiguously suggests one."""
    if not details:
        return None
    if UNCERTAIN_HINTS.search(details):
        return None
    for pat in RENAME_PATTERNS:
        m = pat.search(details)
        if m:
            return m.group(1)
    return None


def collect_fixes(report_path: pathlib.Path) -> list[tuple[str, str, str]]:
    """Return list of (current_name, suggested_name, page_idx) for high-confidence renames."""
    if not report_path.exists():
        return []
    d = json.loads(report_path.read_text())
    fixes = []
    for pg in d.get("pages", []):
        pno = pg.get("page_number", 0)
        for issue in pg.get("issues", []):
            if issue.get("type") != "naming":
                continue
            current = issue.get("field_name", "").strip()
            if not current:
                continue
            suggested = extract_rename(issue.get("details", ""))
            if not suggested or suggested == current:
                continue
            fixes.append((current, suggested, pno))
    return fixes


def find_pdf_for_report(report_stem: str, pdf_root: pathlib.Path) -> pathlib.Path | None:
    """Match report file (e.g. 'PP-205 ..._fused') to its PDF under pdf_root."""
    for sub in pdf_root.rglob("*.pdf"):
        if sub.stem == report_stem:
            return sub
    return None


def apply_renames_to_pdf(pdf_path: pathlib.Path, fixes: list,
                         out_path: pathlib.Path, dry_run: bool) -> dict:
    """Open PDF, rename matching widgets. Returns counts.

    PyMuPDF requires widgets to remain page-bound during .update(). We iterate
    per-page so each widget is fresh from its page() call when we modify it.
    """
    d = fitz.open(pdf_path)
    applied = 0
    skipped_not_found = 0
    skipped_collision = 0
    # First pass: collect all existing names for collision detection
    existing_all = set()
    for page in d:
        for w in page.widgets() or []:
            existing_all.add(w.field_name)
    # Group fixes by page
    fixes_by_page: dict[int, list[tuple[str, str]]] = {}
    for cur, sug, pno in fixes:
        fixes_by_page.setdefault(pno, []).append((cur, sug))
    # Apply per-page (widgets stay page-bound throughout)
    for pno, pg_fixes in fixes_by_page.items():
        if pno >= d.page_count:
            skipped_not_found += len(pg_fixes)
            continue
        page = d[pno]
        widgets = list(page.widgets() or [])
        for current, suggested in pg_fixes:
            target = next((w for w in widgets if w.field_name == current), None)
            if target is None:
                skipped_not_found += 1
                continue
            if suggested in existing_all and suggested != current:
                skipped_collision += 1
                continue
            if not dry_run:
                target.field_name = suggested
                target.update()
                existing_all.discard(current)
                existing_all.add(suggested)
            applied += 1
    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if applied:
            d.save(out_path, deflate=True)
        else:
            # No fixes applied → copy original so output tree is complete
            shutil.copyfile(pdf_path, out_path)
    d.close()
    return {
        "applied": applied,
        "skipped_not_found": skipped_not_found,
        "skipped_collision": skipped_collision,
        "total_proposed": len(fixes),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default="reports/opus-alignment-fused-full",
                    help="Audit-report directory")
    ap.add_argument("--pdfs", default="output_fused",
                    help="PDF source directory")
    ap.add_argument("--out", default="output_fused_renamed",
                    help="Output directory for fixed PDFs")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write fixed PDFs (default: dry-run)")
    ap.add_argument("--form", default=None, help="Filter to single form substring")
    args = ap.parse_args()

    reports_dir = ROOT / args.reports
    pdfs_dir = ROOT / args.pdfs
    out_dir = ROOT / args.out

    summary = {
        "forms_with_renames": 0,
        "total_applied": 0,
        "total_skipped_not_found": 0,
        "total_skipped_collision": 0,
        "total_proposed": 0,
    }
    rows = []
    for r in sorted(reports_dir.glob("*.json")):
        if args.form and args.form not in r.name:
            continue
        report_stem = r.stem
        fixes = collect_fixes(r)
        if not fixes:
            continue
        pdf = find_pdf_for_report(report_stem, pdfs_dir)
        if not pdf:
            print(f"[skip] {report_stem}: no matching PDF found")
            continue
        # Mirror PDF subdir into out
        rel = pdf.relative_to(pdfs_dir)
        out_path = out_dir / rel
        result = apply_renames_to_pdf(pdf, fixes, out_path, dry_run=not args.apply)
        if result["applied"] > 0 or result["total_proposed"] > 0:
            rows.append((report_stem, result, fixes))
            summary["forms_with_renames"] += 1 if result["applied"] else 0
            summary["total_applied"] += result["applied"]
            summary["total_skipped_not_found"] += result["skipped_not_found"]
            summary["total_skipped_collision"] += result["skipped_collision"]
            summary["total_proposed"] += result["total_proposed"]

    # Print summary
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"\n{mode} naming-fix pass")
    print(f"  Forms inspected: {sum(1 for _ in reports_dir.glob('*.json'))}")
    print(f"  Forms with rename suggestions: {len(rows)}")
    print(f"  Total proposed renames: {summary['total_proposed']}")
    print(f"  Applied: {summary['total_applied']}")
    print(f"  Skipped (widget not found): {summary['total_skipped_not_found']}")
    print(f"  Skipped (name collision): {summary['total_skipped_collision']}")
    print()
    print("Top forms by rename count:")
    rows.sort(key=lambda r: -r[1]["applied"])
    for stem, res, fixes in rows[:10]:
        applied = res["applied"]
        proposed = res["total_proposed"]
        print(f"  {applied:3d}/{proposed:3d}  {stem[:60]}")
        # Show first 3 actual rename pairs
        for cur, sug, pno in fixes[:3]:
            print(f"        page{pno}: {cur:30s} → {sug}")

    if not args.apply:
        print("\n(dry-run — re-run with --apply to actually write fixed PDFs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
