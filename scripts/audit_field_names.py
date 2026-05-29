"""Audit field names across all output PDFs.

Three checks:
  1. Every field name matches snake_case regex `^[a-z][a-z0-9_]*$`
  2. No PDF has duplicate field names (would break form-fill semantics)
  3. Cross-form: common-concept field names that don't share a canonical form
     (e.g. one form uses 'docket_no', another uses 'docket_number')

Outputs a summary report. Exit 1 if any violation is found.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import fitz

SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Concept families: variants we'd flag if both appear across the corpus.
CONCEPT_FAMILIES = [
    {"docket_no", "docket_number"},
    {"case_no", "case_number"},
    {"county", "county_probate_court", "county_name"},
    {"decedent", "decedent_name", "name_of_decedent"},
    {"signature", "signature_petitioner", "petitioner_signature"},
    {"date_signed", "date_dated", "dated"},
    {"attorney_phone", "attorney_phone_number"},
    {"attorney_address", "attorney_address_1"},
]


def main(root: Path) -> int:
    pdfs = sorted(p for p in root.rglob("*.pdf") if "previews" not in p.parts)
    if not pdfs:
        print(f"No PDFs in {root}"); return 1

    total_fields = 0
    bad_names: list[tuple[str, str]] = []  # (file, name)
    intra_dups: list[tuple[str, str]] = []  # (file, name)
    name_to_files: dict[str, set[str]] = defaultdict(set)

    for pdf in pdfs:
        rel = str(pdf.relative_to(root))
        doc = fitz.open(pdf)
        seen_local: set[str] = set()
        for page in doc:
            for w in page.widgets() or []:
                name = w.field_name or ""
                total_fields += 1
                if not SNAKE_RE.match(name):
                    bad_names.append((rel, name))
                if name in seen_local:
                    intra_dups.append((rel, name))
                seen_local.add(name)
                name_to_files[name].add(rel)
        doc.close()

    # Cross-form concept-family conflicts
    cross_conflicts: list[tuple[set[str], dict[str, int]]] = []
    for family in CONCEPT_FAMILIES:
        present = {n: len(name_to_files[n]) for n in family if n in name_to_files}
        if len(present) >= 2:
            cross_conflicts.append((family, present))

    # ── report ────────────────────────────────────────────────────────────
    print(f"Audited {len(pdfs)} PDFs, {total_fields} fields, "
          f"{len(name_to_files)} unique field names\n")

    print(f"[1] Snake-case compliance: "
          f"{'PASS' if not bad_names else f'FAIL — {len(bad_names)} bad name(s)'}")
    for f, n in bad_names[:15]:
        print(f"    {f}: {n!r}")
    if len(bad_names) > 15:
        print(f"    ... +{len(bad_names) - 15} more")
    print()

    print(f"[2] Intra-PDF uniqueness: "
          f"{'PASS' if not intra_dups else f'FAIL — {len(intra_dups)} dup(s)'}")
    for f, n in intra_dups[:10]:
        print(f"    {f}: {n!r}")
    print()

    print(f"[3] Cross-form concept consistency: "
          f"{'PASS' if not cross_conflicts else f'WARN — {len(cross_conflicts)} family(ies) conflict'}")
    for family, present in cross_conflicts:
        print(f"    family {sorted(family)}:")
        for name, ct in sorted(present.items(), key=lambda x: -x[1]):
            print(f"        {name:<35s} appears in {ct:>3d} form(s)")
    print()

    # Quick stats: most-shared canonical names (sanity check)
    shared = sorted(name_to_files.items(), key=lambda x: -len(x[1]))[:15]
    print("Top shared names across forms:")
    for n, files in shared:
        print(f"  {n:<40s} {len(files):>3d}")
    print()

    fail = bool(bad_names or intra_dups)
    return 1 if fail else 0


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output")
    sys.exit(main(root))
