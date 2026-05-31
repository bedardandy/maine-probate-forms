#!/usr/bin/env python3
"""Verify catalog/field_alignment.json against the schema (CI gate + review aid).

Two jobs:
  1. DRIFT GATE (fails non-zero): the shipped map must equal what
     author_field_align.py would produce from the current schema. Catches a
     stale map after fields change. Fix with `make align`.
  2. REVIEW FLAGS (warn only): fields whose NAME reads like money (an old
     currency token) but whose schema data_type is `text` — i.e. the schema and
     the name disagree. These are not errors (the schema wins, so they render
     left), but each is worth a human glance: either the name is misleading or
     the field is mis-typed and should be `currency`.

    python3 scripts/verify_field_align.py
"""
from __future__ import annotations
import csv, json, pathlib, re, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
FORMS = REPO / "repo" / "forms"
MAP = REPO / "catalog" / "field_alignment.json"

sys.path.insert(0, str(REPO / "scripts"))
import author_field_align as A  # noqa: E402

# Money-looking name tokens (mirrors the old fill_pdf heuristic) — for review only.
_MONEYISH = re.compile(
    r"(?:^|_)(?:value|val|amount|amt|fee|fees|penal_sum|penal|balance|income|"
    r"expense|expenses|salary|wage|wages|disbursement|disbursements|cost|cash|"
    r"total|owed|payment|payments)(?:$|_)", re.I)


def main() -> int:
    if not MAP.exists():
        print(f"MISSING {MAP.relative_to(REPO)} — run `make align`")
        return 1
    shipped = json.loads(MAP.read_text()).get("forms", {})
    expected = A.build()

    if shipped != expected:
        print("DRIFT: catalog/field_alignment.json is stale vs the schema.")
        forms = set(shipped) | set(expected)
        for f in sorted(forms):
            s, e = shipped.get(f, {}), expected.get(f, {})
            if s != e:
                for fid in sorted(set(s) | set(e)):
                    if s.get(fid) != e.get(fid):
                        print(f"  {f}/{fid}: shipped={s.get(fid,'left')} "
                              f"expected={e.get(fid,'left')}")
        print("\nFix: make align")
        return 1

    # Review flags: name says money, schema says text.
    flags = []
    for d in sorted(FORMS.iterdir()):
        if not (d / "fields.csv").exists():
            continue
        for fid, dt in A._data_types(d).items():
            if dt == "text" and _MONEYISH.search(fid):
                flags.append((d.name, fid))

    n = sum(len(v) for v in shipped.values())
    print(f"OK — alignment map in sync with schema: {n} non-left fields "
          f"across {len(shipped)} forms.")
    if flags:
        print(f"\n{len(flags)} review flag(s) — name reads like money but schema "
              f"types it 'text' (renders left; confirm intentional):")
        for f, fid in flags:
            print(f"  {f}/{fid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
