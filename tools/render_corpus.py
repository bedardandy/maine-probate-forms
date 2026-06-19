#!/usr/bin/env python3
"""Render overlay PNGs for every page of every form, for human/agent review.

For each form this draws every fill_geometry rect on the official PDF (via
tools/stress_render.py) so a reviewer can scroll the whole corpus and spot text
that overprints a label, runs off a rule, or sits on the wrong line. This is the
eyeball companion to the automated gate in tests/test_render_all_forms.py and
tests/test_geometry_audit_baseline.py.

    python3 tools/render_corpus.py --out-dir /tmp/corpus_probe
    python3 tools/render_corpus.py --forms DE-301,AF-105 --dpi 140

Writes <out-dir>/<FORM>/<FORM>_pN.png and an index.txt manifest. Network is used
to fetch flat sources (cached, SHA-verified); a form whose source can't be
fetched is reported and skipped.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from stress_render import render  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/tmp/corpus_probe")
    ap.add_argument("--forms", help="comma-separated subset; default all")
    ap.add_argument("--dpi", type=int, default=120)
    args = ap.parse_args()

    if args.forms:
        forms = [f.strip() for f in args.forms.split(",") if f.strip()]
    else:
        forms = sorted(p.name for p in (ROOT / "repo" / "forms").iterdir()
                       if p.is_dir())

    out_root = pathlib.Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest, pages, failed = [], 0, []
    for form_id in forms:
        try:
            paths = render(form_id, None, out_root / form_id.replace("/", "_"),
                           args.dpi)
        except Exception as exc:  # offline / bad source
            failed.append(f"{form_id}: {exc}")
            continue
        pages += len(paths)
        manifest.extend(str(p) for p in paths)
    (out_root / "index.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(f"rendered {pages} pages for {len(forms) - len(failed)} forms "
          f"-> {out_root}")
    if failed:
        print("skipped:")
        for line in failed:
            print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
