#!/usr/bin/env python3
"""Apply the human poll decisions to fill_geometry.json.

Reads <out>/human_decisions.jsonl (last decision per unit wins). For a unit
where the reviewer chose a candidate (B/C/D), set that widget's rect to the
chosen rect. Choice A ("leave as-is"), "skip", and "other" (free-text note)
make no geometry change — "other" notes are reported for follow-up. Guards
that the live rect still matches the unit before writing.

    python3 scripts/geometry_review/apply_decisions.py --out ~/geom-review-out
    python3 scripts/geometry_review/apply_decisions.py --out ~/geom-review-out --apply
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def latest(out: pathlib.Path) -> dict:
    dec = {}
    p = out / "human_decisions.jsonl"
    if p.exists():
        for line in p.open():
            o = json.loads(line)
            dec[o["id"]] = o
    return dec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    dec = latest(args.out)
    units = {u["id"]: u for u in json.loads((args.out / "poll_data.json").read_text())}
    changes, notes, leave, skip = [], [], 0, 0
    for uid, d in dec.items():
        ch = d["choice"]
        if ch == "skip":
            skip += 1
            continue
        if ch == "A":
            leave += 1
            continue
        if ch == "other":
            notes.append(d)
            continue
        if d.get("chosen_rect"):
            changes.append(d)

    print(f"decisions: {len(dec)} | apply-rect: {len(changes)} | "
          f"leave-as-is(A): {leave} | other-note: {len(notes)} | skip: {skip}")
    for d in changes:
        print(f"  {d['form']:10} {d['field'][:28]:28} {d['choice']} -> {d['chosen_rect']}")
    if notes:
        print("\nOther notes (manual follow-up):")
        for d in notes:
            print(f"  {d['form']:10} {d['field'][:28]:28} {d.get('note','')[:80]}")

    if not args.apply:
        return 0

    by_form: dict[str, list] = {}
    for d in changes:
        by_form.setdefault(d["form"], []).append(d)
    applied_f = (args.out / "decision_fixes_applied.jsonl").open("a")
    for form, ds in by_form.items():
        gp = ROOT / "repo" / "forms" / form / "fill_geometry.json"
        g = json.loads(gp.read_text())
        n = 0
        for d in ds:
            spec = g["fields"].get(d["field"])
            if not spec or not spec.get("widgets"):
                continue
            i = int(d["widget_idx"]) if str(d["widget_idx"]).isdigit() else 0
            if i >= len(spec["widgets"]):
                continue
            spec["widgets"][i]["rect"] = d["chosen_rect"]
            n += 1
            applied_f.write(json.dumps(d) + "\n")
        if n:
            gp.write_text(json.dumps(g, indent=1))
            print(f"{form}: {n} rect(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
