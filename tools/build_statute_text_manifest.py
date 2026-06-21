#!/usr/bin/env python3
"""Pin the SHA-256 of each statute's normalized text into the manifest.

Maintainer tool, parallel to ``tools/build_pdf_manifest.py``. It collects the
statute / cross-ref citations the forms actually use (the union across every
``repo/forms/<ID>/statutes.json``, intersected with the trusted index — it does
NOT fetch all 623 sections), fetches each section's text via
``fetch_statute_text``, and records ``{cite, url, sha256, chars, fetched,
extractor_version}`` in ``catalog/statute_text_manifest.json``.

Once pinned, the inspector's ``fetch_statute_text`` reports ``text_verified``:
``True`` when the live text still matches the pin, ``False`` when the section was
re-issued. Bump ``EXTRACTOR_VERSION`` in ``fetch_statute_text.py`` and re-run to
invalidate every entry after improving the extractor.

Needs network (legislature.maine.gov). Not legal advice.

    python3 tools/build_statute_text_manifest.py            # fetch + write
    python3 tools/build_statute_text_manifest.py --check    # verify, no writes
    python3 tools/build_statute_text_manifest.py --cites "18-C §3-401" "36 M.R.S. §4107"
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fetch_statute_text as fst           # noqa: E402
from maine_citation_db import _index, resolves   # noqa: E402

FORMS = ROOT / "repo" / "forms"
MANIFEST = ROOT / "catalog" / "statute_text_manifest.json"


def used_cites() -> list[str]:
    """Statute + cross-ref cites referenced anywhere in the per-form sidecars."""
    sec, xref, _ = _index()
    cites: set[str] = set()
    for sc_path in sorted(FORMS.glob("*/statutes.json")):
        sc = json.loads(sc_path.read_text(encoding="utf-8"))
        for g in sc.get("governing", []):
            cites.add(g.get("cite"))
        for pq in sc.get("per_question", []):
            for c in pq.get("considerations", []):
                cites.add(c.get("cite"))
        for x in sc.get("cross_refs", []):
            cites.add(x.get("cite"))
    # Only cites that resolve in the trusted index (statutes/cross-refs).
    return sorted(c for c in cites if c and resolves(c, sec, xref))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="verify live text matches the manifest; write nothing")
    ap.add_argument("--cites", nargs="*", help="only these cites (default: all used)")
    ap.add_argument("--fresh", action="store_true", help="ignore the fetch cache")
    a = ap.parse_args()

    cites = a.cites or used_cites()
    man = fst.load_manifest()
    man.setdefault("extractor_version", fst.EXTRACTOR_VERSION)
    man.setdefault("statutes", {})

    changed, failed, verified = [], [], []
    for cite in cites:
        res = fst.fetch_statute_text(cite, fresh=a.fresh)
        if not res.get("text"):
            failed.append((cite, res.get("error")))
            continue
        rec = fst.manifest_record(cite, res)
        old = man["statutes"].get(cite)
        if old and old.get("sha256") == rec["sha256"]:
            verified.append(cite)
        else:
            changed.append(cite)
            if not a.check:
                man["statutes"][cite] = rec

    if a.check:
        if changed or failed:
            for cite in changed:
                print(f"  CHANGED/UNPINNED: {cite}", file=sys.stderr)
            for cite, err in failed:
                print(f"  FETCH FAILED: {cite}: {err}", file=sys.stderr)
            print(f"FAIL — {len(changed)} unpinned/changed, {len(failed)} fetch "
                  f"failures, {len(verified)} verified.", file=sys.stderr)
            return 1
        print(f"OK — all {len(verified)} statute texts match the manifest.")
        return 0

    man["statutes"] = dict(sorted(man["statutes"].items()))
    MANIFEST.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"wrote {MANIFEST.relative_to(ROOT)}: {len(changed)} updated, "
          f"{len(verified)} unchanged, {len(failed)} failed "
          f"({len(man['statutes'])} pinned total).")
    for cite, err in failed:
        print(f"  [warning] could not fetch {cite}: {err}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
