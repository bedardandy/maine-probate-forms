"""Backfill `bar_number` and `email` onto attorney records in cases.

Why this exists:
  The LLM-driven case generator (router/generate_case.py) emits attorney
  records with only full_name/address/phone/attrs.firm — no Maine bar
  number, no email. Forms with `attorney_bar_number` / `attorney_email`
  widgets (DE-201, DE-502, DE-504, DE-507, PP-412 ...) then leave them
  blank, and the vision audit flags `blank_required`.

  This script deterministically derives bar# and email from attorney
  full_name (and firm if available) so the values are stable across
  refills and across forms — the same attorney always gets the same
  bar# and email.

Usage:
  python3 scripts/backfill_attorney_block.py
      --in router/synthetic_cases.jsonl
      --out router/synthetic_cases.jsonl

  Writes a `.before_attorney_backfill` sibling backup before clobber.

The same `derive_bar` / `derive_email` functions are imported by
router/generate_case.py so future regenerations carry these fields
automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys


# Common Maine TLD + a few firm-fallback domains for the derived email.
DEFAULT_DOMAINS = (
    "mainelaw.com", "portlandlaw.com", "kennebeclaw.com",
    "mainejustice.com", "downeastlegal.com",
)
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def derive_bar_number(attorney_name: str) -> str:
    """Mock Maine bar number stable per attorney name.

    Real Maine bar numbers are roughly 1-12000; we bias mock numbers
    into 5000-19999 to avoid colliding with low real numbers. Prefix
    with 'ME-' to match the format Qwen emits when it fills the field.
    """
    h = hashlib.sha256(attorney_name.encode()).digest()
    n = 5000 + (int.from_bytes(h[:4], "big") % 15000)
    return f"ME-{n}"


def _slugify_name(name: str) -> str:
    """Turn 'Sarah J. O’Connell' → 'sarah.oconnell'."""
    s = name.lower()
    s = s.replace("’", "").replace("'", "")
    parts = [p for p in NON_ALNUM_RE.split(s) if p]
    # Drop middle initials (single letters), keep first + last
    parts = [p for p in parts if len(p) > 1]
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[-1]}"
    return parts[0] if parts else "attorney"


def _derive_domain(firm: str | None, name: str) -> str:
    if firm:
        # 'Thibodeau & Associates' → 'thibodeau-associates.com' style
        slug = firm.lower()
        slug = slug.replace("’", "").replace("'", "")
        slug = slug.replace("&", "and")
        slug = NON_ALNUM_RE.sub("-", slug).strip("-")
        # Drop trailing legal qualifiers
        for tail in ("-llc", "-pa", "-pllc", "-llp", "-and-associates",
                     "-pc", "-law-office", "-law-offices", "-attorneys"):
            if slug.endswith(tail):
                slug = slug[: -len(tail)]
                break
        if slug:
            return f"{slug}.com"
    # Fall back to a stable per-name pick from DEFAULT_DOMAINS
    h = hashlib.sha256(name.encode()).digest()
    return DEFAULT_DOMAINS[h[0] % len(DEFAULT_DOMAINS)]


# Widget rects on most Maine probate forms are ~25-30 chars wide. Cap
# the synthesized email so it doesn't overflow and trigger the audit's
# `truncated` verdict (real-world emails ~25 chars).
EMAIL_MAX = 28


def derive_email(attorney_name: str, firm: str | None = None) -> str:
    slug = _slugify_name(attorney_name)
    domain = _derive_domain(firm, attorney_name)
    full = f"{slug}@{domain}"
    if len(full) <= EMAIL_MAX:
        return full
    # Step 1: drop firm-derived domain, fall back to short DEFAULT_DOMAINS pick.
    h = hashlib.sha256(attorney_name.encode()).digest()
    domain = DEFAULT_DOMAINS[h[0] % len(DEFAULT_DOMAINS)]
    full = f"{slug}@{domain}"
    if len(full) <= EMAIL_MAX:
        return full
    # Step 2: still too long → shorten slug to first-initial.last form,
    # then iterate DEFAULT_DOMAINS shortest-first to find one that fits.
    parts = slug.split(".")
    if len(parts) >= 2:
        slug = f"{parts[0][0]}.{parts[-1]}"
    for d in sorted(DEFAULT_DOMAINS, key=len):
        full = f"{slug}@{d}"
        if len(full) <= EMAIL_MAX:
            return full
    # Names with very long last names: keep using shortest default.
    return f"{slug}@{min(DEFAULT_DOMAINS, key=len)}"


def derive_phone(attorney_name: str) -> str:
    """Deterministic Maine 207-555-0XXX phone, stable per attorney name.

    Uses the 555-01XX..555-09XX block reserved by NANP for fiction so
    the mock numbers never collide with a real subscriber.
    """
    h = hashlib.sha256(attorney_name.encode()).digest()
    n = 100 + (int.from_bytes(h[4:8], "big") % 900)
    return f"207-555-0{n:03d}"


def backfill_attorney(att: dict) -> tuple[dict, list[str]]:
    """Mutate `att` in-place. Returns (att, list of fields filled)."""
    changes: list[str] = []
    name = att.get("full_name") or ""
    if not name:
        return att, changes
    attrs = att.setdefault("attrs", {}) or {}

    # bar_number: prefer attrs.bar_number; tolerate top-level for legacy
    has_bar = bool(attrs.get("bar_number") or att.get("bar_number"))
    if not has_bar:
        attrs["bar_number"] = derive_bar_number(name)
        att["attrs"] = attrs
        changes.append("attrs.bar_number")

    # email: top-level field per Person schema
    if not att.get("email"):
        att["email"] = derive_email(name, attrs.get("firm"))
        changes.append("email")

    # phone: top-level field per Person schema
    if not att.get("phone"):
        att["phone"] = derive_phone(name)
        changes.append("phone")

    return att, changes


def backfill_case(case_obj: dict) -> int:
    """Walk parties / extra_parties / party_lists looking for attorneys.

    Returns total field-fills count.
    """
    n = 0
    case = case_obj.get("case") or {}
    for bucket_key in ("parties", "extra_parties"):
        bucket = case.get(bucket_key) or {}
        for role, person in bucket.items():
            if "attorney" not in role.lower() or not isinstance(person, dict):
                continue
            _, changes = backfill_attorney(person)
            n += len(changes)
    lists = case.get("party_lists") or {}
    for role, plist in lists.items():
        if "attorney" not in role.lower() or not isinstance(plist, list):
            continue
        for p in plist:
            if isinstance(p, dict):
                _, changes = backfill_attorney(p)
                n += len(changes)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", type=pathlib.Path,
                    default=pathlib.Path("router/synthetic_cases.jsonl"))
    ap.add_argument("--out", dest="out_path", type=pathlib.Path, default=None)
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip writing .before_attorney_backfill backup")
    args = ap.parse_args()

    out_path = args.out_path or args.in_path
    if out_path == args.in_path and not args.no_backup:
        backup = args.in_path.with_suffix(
            args.in_path.suffix + ".before_attorney_backfill")
        if not backup.exists():
            shutil.copy2(args.in_path, backup)
            print(f"  backup → {backup}")

    cases = []
    total_fills = 0
    cases_touched = 0
    for line in args.in_path.read_text().splitlines():
        if not line.strip():
            cases.append(line)
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            cases.append(line)
            continue
        n = backfill_case(obj)
        if n:
            cases_touched += 1
        total_fills += n
        cases.append(json.dumps(obj))

    out_path.write_text("\n".join(cases) + ("\n" if cases else ""))
    print(f"backfill_attorney_block: {cases_touched} case(s) updated, "
          f"{total_fills} field(s) filled → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
