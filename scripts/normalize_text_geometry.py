#!/usr/bin/env python3
"""C: normalize single-line text-field heights for consistent spacing.

Analysis (scripts that produced the rule):
  - single-line text rects (height <= 24) vary widely (median ~16, stdev ~4.8);
  - their BOTTOM edge sits on the form's underline (offset to nearest vector
    underline: median 0.0, stdev 0.8 across ~1000 matched rects).

So we anchor on the bottom edge (= underline) and set a uniform height. In
viewers that vertically-center single-line fields (e.g. Acrobat, which
regenerates appearances because fill_pdf sets NeedAppearances), this keeps filled
text sitting just above the line at a consistent gap instead of floating in tall
boxes. Multiline boxes (height > 24) and checkbox/option widgets are untouched.

Backs up each fill_geometry.json to <file>.preC.bak (once) before editing.

    python3 scripts/normalize_text_geometry.py          # apply to all forms
    python3 scripts/normalize_text_geometry.py --check   # report, write nothing
"""
from __future__ import annotations
import argparse, json, os, pathlib, shutil

REPO = pathlib.Path(__file__).resolve().parent.parent
FORMS = REPO / "repo" / "forms"
TARGET_H = 13.0
MIN_H = 9.0        # below this: leave (tiny markers)
MAX_SINGLE = 24.0  # above this: treated as multiline elsewhere -> leave
EPS = 0.6          # don't rewrite rects already at target


def normalize_form(form_dir: pathlib.Path, apply: bool) -> int:
    p = form_dir / "fill_geometry.json"
    geo = json.loads(p.read_text())
    changed = 0
    for fid, fd in geo["fields"].items():
        for w in fd.get("widgets", []):
            x0, y0, x1, y1 = w["rect"]
            h = y1 - y0
            if MIN_H <= h <= MAX_SINGLE and abs(h - TARGET_H) > EPS:
                w["rect"] = [round(x0, 1), round(y1 - TARGET_H, 1),
                             round(x1, 1), round(y1, 1)]
                changed += 1
    if changed and apply:
        bak = p.with_suffix(".json.preC.bak")
        if not bak.exists():
            shutil.copy(p, bak)
        p.write_text(json.dumps(geo, indent=1) + "\n")
    return changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    total = 0; forms = 0
    for d in sorted(FORMS.iterdir()):
        if not d.is_dir() or not (d / "fill_geometry.json").exists():
            continue
        c = normalize_form(d, apply=not args.check)
        if c:
            forms += 1
            print(f"  {d.name}: {c} text widget(s) normalized to {TARGET_H:.0f}pt")
        total += c
    verb = "would normalize" if args.check else "normalized"
    print(f"\n{verb} {total} single-line text widgets across {forms} forms "
          f"(height->{TARGET_H:.0f}pt, anchored at underline).")


if __name__ == "__main__":
    main()
