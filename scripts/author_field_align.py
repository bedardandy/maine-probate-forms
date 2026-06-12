#!/usr/bin/env python3
"""Author the per-field text-justification map (catalog/field_alignment.json).

Justification is a *field property*, but the old approach guessed it at fill time
from the field name alone (a "caption" substring + a currency-token regex). That
heuristic mis-fires both ways:

  - false positives: a currency token embedded before a text qualifier
    (`penal_sum_words`, `*_expenses_details`, `*_income_period`) -> wrongly right.
  - collisions: `interest` means *legal interest in the estate* (heir / devisee /
    creditor — prose), NOT money, so a name rule can't tell `petitioner_interest`
    (text) from `petitioner_interest_value` (currency).

The schema already answers this authoritatively: each field carries a declared
`data_type` (`currency`, `number`, `date`, `text`, ...). So we derive
justification from the *declared type*, not the name:

    data_type in {currency, number}  -> right   (values line up in columns)
    field_id contains "caption"      -> center  (layout convention; no type signal)
    everything else                  -> left    (the implicit default)

Only non-left fields are written, so the file stays small. `tools/fill_pdf.py`
reads it (falling back to its name heuristic for any form/field not listed);
`scripts/verify_field_align.py` re-derives it and flags name/type disagreements
(e.g. a field named `*_amount` that the schema types `text`) for human review.

    python3 scripts/author_field_align.py            # write catalog/field_alignment.json
    python3 scripts/author_field_align.py --check     # report, write nothing
"""
from __future__ import annotations
import argparse, csv, json, pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
FORMS = REPO / "repo" / "forms"
OUT = REPO / "catalog" / "field_alignment.json"

RIGHT_TYPES = {"currency", "number"}


def _data_types(form_dir: pathlib.Path) -> dict[str, str]:
    """field_id -> declared data_type, from the form's fields.csv."""
    csv_path = form_dir / "fields.csv"
    if not csv_path.exists():
        return {}
    with csv_path.open() as fh:
        return {row["field_id"]: (row.get("data_type") or "").strip().lower()
                for row in csv.DictReader(fh)}


def classify(fid: str, data_type: str) -> str:
    """Return 'center' | 'right' | 'left' for one field."""
    # `estate_of_decedent` is the "Estate of ____" case-caption line on the
    # DE-301 family — same layout convention as *_caption fields.
    if "caption" in fid.lower() or fid == "estate_of_decedent":
        return "center"
    if data_type in RIGHT_TYPES:
        return "right"
    return "left"


def build() -> dict:
    out: dict[str, dict[str, str]] = {}
    for d in sorted(FORMS.iterdir()):
        gp = d / "fill_geometry.json"
        if not d.is_dir() or not gp.exists():
            continue
        dtypes = _data_types(d)
        per: dict[str, str] = {}
        for fid in json.loads(gp.read_text())["fields"]:
            a = classify(fid, dtypes.get(fid, ""))
            if a != "left":
                per[fid] = a
        if per:
            out[d.name] = dict(sorted(per.items()))
    return dict(sorted(out.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    data = build()
    n = sum(len(v) for v in data.values())
    centers = sum(1 for v in data.values() for a in v.values() if a == "center")
    rights = n - centers
    if args.check:
        print(f"would write {n} non-left fields ({centers} center, {rights} right) "
              f"across {len(data)} forms")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "_note": "Per-field text justification (left is the implicit default and "
                 "is omitted). Right is derived from the schema data_type "
                 "(currency/number); center from a 'caption' field name. Authored "
                 "by scripts/author_field_align.py; verified by "
                 "scripts/verify_field_align.py; consumed by tools/fill_pdf.py.",
        "forms": data,
    }, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(REPO)}: {n} non-left fields "
          f"({centers} center, {rights} right) across {len(data)} forms")


if __name__ == "__main__":
    main()
