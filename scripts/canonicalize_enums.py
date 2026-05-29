#!/usr/bin/env python3
"""Post-process a filled probate form to canonicalize value_in tokens.

The local Qwen3.6-27B fill consistently emits near-miss tokens for
value_in fields: 'month' instead of 'monthly', 'was' / 'did' instead of
'yes', 'true' instead of 'yes', 'is_not' instead of 'no', etc. Prompt-
level rules don't fix this reliably; a deterministic post-processor does.

Mapping is layered, highest-confidence first:

  1. Stem table — handful of attorney-curated near-misses
     (true→yes, was→yes, did→yes, month→monthly, week→weekly, etc.)
  2. Substring containment — if a value_in choice is a substring of the
     bad token (case-insensitive, ignoring _/-/space), pick that choice.
     e.g. 'has_minor_children' contains 'minor' → 'minor_only'.
  3. RapidFuzz token-set ratio ≥ 85 against the choices — catches
     inflectional drift and typos.

Wrong-type fallback: if the field's data_type is currency and the value
is a non-numeric yes/no answer ('no', 'none', 'n/a'), set to ''.

Usage:
    python3 scripts/canonicalize_enums.py \
        --schema repo/forms/AF-105/schema.json \
        --filled intermediate/fact_eval/AF-105/filled_1.v3.json \
        --out    intermediate/fact_eval/AF-105/filled_1.v3.canon.json \
        --report intermediate/fact_eval/AF-105/canon_changes.tsv

    --dry-run prints changes to stdout without writing files.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

try:
    from rapidfuzz import fuzz
    HAVE_FUZZ = True
except ImportError:
    HAVE_FUZZ = False

VALUE_IN_RE = re.compile(r"^value_in\(([^)]+)\)$")

# Hand-curated near-miss map. Keys are lowercased, punctuation-stripped
# bad tokens. Values are the canonical choice that the bad token most
# plausibly represents.
#
# Boolean truthy → 'yes'; falsy → 'no'.
# Period nouns → adverb forms.
STEM_MAP = {
    # Truthy
    "true":               "yes",
    "t":                  "yes",
    "1":                  "yes",
    "y":                  "yes",
    "affirmative":        "yes",
    "was":                "yes",
    "wasyes":             "yes",
    "did":                "yes",
    "does":               "yes",
    "do":                 "yes",
    "is":                 "yes",
    "are":                "yes",
    "has":                "yes",
    "have":               "yes",
    "had":                "yes",
    "intendtoshare":      "yes",
    "intends":            "yes",
    "intend":             "yes",
    "consents":           "yes",
    "consent":            "yes",
    "agrees":             "yes",
    "agree":              "yes",
    "approve":            "yes",
    "approves":           "yes",
    "approved":           "yes",
    "granted":            "yes",
    "allowed":            "yes",
    # Falsy
    "false":              "no",
    "f":                  "no",
    "0":                  "no",
    "n":                  "no",
    "negative":           "no",
    "isnot":              "no",
    "wasnot":             "no",
    "didnot":             "no",
    "doesnot":            "no",
    "donot":              "no",
    "havenotrequested":   "no",
    "havenot":            "no",
    "hasnot":             "no",
    "noobjection":        "no",
    "refuses":            "no",
    "refuse":             "no",
    "declined":           "no",
    "denied":             "no",
    "rejects":            "no",
    "objects":            "yes",   # context-specific: usually maps to a
                                   # 'did_object'/'yes' affirmative form
    # Period nouns → adverbial enums
    "month":              "monthly",
    "months":             "monthly",
    "permonth":           "monthly",
    "week":               "weekly",
    "weeks":              "weekly",
    "perweek":            "weekly",
    "year":               "annually",
    "years":              "annually",
    "annual":             "annually",
    "peryear":            "annually",
    "annum":              "annually",
    "perannum":           "annually",
    "biweek":             "biweekly",
    "biweeks":            "biweekly",
    "everyotherweek":     "biweekly",
    "semimonth":          "semi_monthly",
    "twiceamonth":        "semi_monthly",
    "twicemonthly":       "semi_monthly",
    # Common falsy currency
    "no":                 "0",        # for currency fields, see clear-currency rule
    "none":               "0",
    "na":                 "0",
    "nothing":            "0",
    "zero":               "0",
    # Composite tokens the substring/fuzzy layers don't reach because the
    # bad form contains the choice plus extra prefix text, e.g.
    # 'has_minor_children' normalizes to 'hasminorchildren' which neither
    # contains nor is contained by 'minoronly'.
    "hasminorchildren":   "minor_only",
    "hasminorchild":      "minor_only",
    "minorchildren":      "minor_only",
    "hasadultchildren":   "adult_only",
    "adultchildren":      "adult_only",
    "notdue":             "not_required",
    "notowed":            "not_required",
    # bond_demand enums (DE-502): "demands_bond" is a Qwen-invented
    # rewrite of "demanded" that the substring/fuzzy layers miss
    # because the bad form contains 'bond' which isn't in any choice.
    "demandsbond":        "demanded",
    "demandbond":         "demanded",
    "demanding":          "demanded",
    "demand":             "demanded",
}

CURRENCY_TYPES = {"currency", "dollar", "money"}

# Mirrors validate_filled.py CURRENCY_RE — a value passing this is a clean
# scalar currency and needs no rescue.
CURRENCY_VALID_RE = re.compile(
    r"^-?\$?\d{1,3}(,\d{3})*(\.\d{1,2})?$|^-?\$?\d+(\.\d{1,2})?$")
# Loose extractor: pick the first dollar amount embedded in prose.
CURRENCY_EXTRACT_RE = re.compile(r"\$?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\$\d+(?:\.\d{1,2})?")


def normalize(s: str) -> str:
    """Strip punctuation/whitespace/case for stem lookup."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def value_in_choices(field: dict) -> list[str] | None:
    for v in field.get("validators", []):
        if isinstance(v, str):
            m = VALUE_IN_RE.match(v)
            if m:
                return [c.strip() for c in m.group(1).split(",") if c.strip()]
    return None


def canonicalize_one(bad: str, choices: list[str]) -> tuple[str, str] | None:
    """Try to map `bad` to one of `choices`.

    Returns (canonical, method) on success, or None.
    """
    if bad in choices:
        return None  # already canonical

    norm_bad = normalize(bad)
    norm_choice = {normalize(c): c for c in choices}

    # Layer 0: case/punct-only difference
    if norm_bad in norm_choice:
        return (norm_choice[norm_bad], "norm")

    # Layer 1: stem map, but only if the mapped target is in choices
    stem = STEM_MAP.get(norm_bad)
    if stem is not None:
        for c in choices:
            if normalize(c) == normalize(stem):
                return (c, "stem")

    # Layer 2: substring containment
    # Bad-token contains a choice → choice is the answer
    # e.g. 'has_minor_children' contains 'minor' → 'minor_only' (because
    # 'minor_only' contains 'minor' too). Pick the LONGEST choice whose
    # normalized form appears as a substring of the normalized bad token,
    # or vice versa.
    contained = []
    for c_norm, c in norm_choice.items():
        if not c_norm or c_norm in ("yes", "no"):
            continue  # too generic; handled by stem map only
        if c_norm in norm_bad or norm_bad in c_norm:
            contained.append((len(c_norm), c))
    if contained:
        contained.sort(reverse=True)
        return (contained[0][1], "substring")

    # Layer 3: fuzzy
    if HAVE_FUZZ:
        best, best_score = None, 0
        for c in choices:
            score = fuzz.token_set_ratio(bad.lower(), c.lower())
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= 85:
            return (best, f"fuzz:{best_score}")

    return None


def field_data_type(field: dict) -> str:
    return (field.get("data_type") or "").lower()


def process(schema: dict, filled: dict) -> tuple[dict, list[tuple]]:
    """Return (new_filled, changes). changes is list of tuples for the TSV."""
    new_filled = json.loads(json.dumps(filled))  # deep copy
    answers = new_filled.get("answers") or {}
    changes = []

    for f in schema.get("fields", []):
        fid = f["field_id"]
        a = answers.get(fid)
        if a is None:
            continue
        val = a.get("value") if isinstance(a, dict) else a
        if val is None or val == "":
            continue
        val_str = str(val)

        # ── value_in canonicalization ────────────────────────────────
        choices = value_in_choices(f)
        if choices and val_str not in choices:
            mapped = canonicalize_one(val_str, choices)
            if mapped is not None:
                new_val, method = mapped
                if isinstance(a, dict):
                    a["value"] = new_val
                    a.setdefault("canon_provenance", []).append(
                        {"from": val_str, "to": new_val, "method": method})
                else:
                    answers[fid] = new_val
                changes.append((fid, val_str, new_val, method,
                                "value_in", "|".join(choices)))
                continue

        # ── currency-typed field with non-numeric yes/no answer ──────
        if field_data_type(f) in CURRENCY_TYPES:
            # Forms already print a "$" glyph adjacent to the currency
            # widget; if the model emitted "$N,NNN.NN", the rendering
            # shows "$$N,NNN.NN". Strip any leading "$" (and surrounding
            # whitespace) unconditionally for currency-typed fields.
            stripped = val_str.lstrip()
            if stripped.startswith("$"):
                new_val = stripped[1:].lstrip()
                if isinstance(a, dict):
                    a["value"] = new_val
                    a.setdefault("canon_provenance", []).append(
                        {"from": val_str, "to": new_val,
                         "method": "strip-currency-prefix"})
                else:
                    answers[fid] = new_val
                changes.append((fid, val_str, new_val,
                                "strip-currency-prefix", "currency", ""))
                val_str = new_val
                if not val_str:
                    continue

            if normalize(val_str) in {"no", "none", "na", "nothing"}:
                if isinstance(a, dict):
                    a["value"] = ""
                    a.setdefault("canon_provenance", []).append(
                        {"from": val_str, "to": "", "method": "clear-currency"})
                else:
                    answers[fid] = ""
                changes.append((fid, val_str, "", "clear-currency",
                                "currency", ""))
                continue

            # Currency field but value isn't a clean scalar (likely prose):
            # try to extract the first explicit $-amount; otherwise clear.
            if not CURRENCY_VALID_RE.match(val_str.strip()):
                m = CURRENCY_EXTRACT_RE.search(val_str)
                new_val = m.group(0).lstrip("$") if m else ""
                method = "extract-currency" if m else "clear-currency-prose"
                if isinstance(a, dict):
                    a["value"] = new_val
                    a.setdefault("canon_provenance", []).append(
                        {"from": val_str, "to": new_val, "method": method})
                else:
                    answers[fid] = new_val
                changes.append((fid, val_str, new_val, method,
                                "currency", ""))

    return new_filled, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True)
    ap.add_argument("--filled", required=True)
    ap.add_argument("--out", default=None,
                    help="Output path. Defaults to <filled>.canon.json.")
    ap.add_argument("--report", default=None,
                    help="TSV change log. Defaults to stderr summary only.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print changes and exit without writing.")
    args = ap.parse_args()

    schema = json.loads(Path(args.schema).read_text())
    filled = json.loads(Path(args.filled).read_text())

    new_filled, changes = process(schema, filled)

    if not HAVE_FUZZ:
        print("WARN: rapidfuzz not installed; fuzzy layer disabled.",
              file=sys.stderr)

    if args.dry_run:
        for r in changes:
            print("\t".join(map(str, r)))
        print(f"-- would write {len(changes)} change(s)", file=sys.stderr)
        return

    out_path = args.out or args.filled.replace(".json", ".canon.json")
    Path(out_path).write_text(json.dumps(new_filled, indent=2))

    if args.report:
        with open(args.report, "w") as fh:
            fh.write("field_id\tfrom\tto\tmethod\tkind\tchoices\n")
            for r in changes:
                fh.write("\t".join(map(str, r)) + "\n")

    print(f"wrote {out_path}  ({len(changes)} change(s))", file=sys.stderr)


if __name__ == "__main__":
    main()
