#!/usr/bin/env python3
"""Detect when maine.gov has re-issued a form out from under its geometry.

Each form's flat source PDF is pinned by SHA-256 in
``catalog/pdf_manifest.json`` — the revision its ``fill_geometry.json``
coordinates were measured against. maineprobate.net URLs are revision-stamped,
so a re-issued form usually means the pinned URL stops resolving (GONE) or, less
often, serves different bytes at the same URL (CHANGED). Either way the shipped
coordinates may no longer line up and the form needs its geometry re-derived.

This tool re-downloads each pinned URL, hashes it, and compares to the manifest.
It is read-only (nothing is written) and exits non-zero on any CHANGED/GONE, so
it works as a scheduled early-warning.

    python3 tools/check_upstream.py                 # check every form
    python3 tools/check_upstream.py --forms DE-101,DE-405
    python3 tools/check_upstream.py --json          # machine-readable report

To adopt new revisions, re-derive the affected forms' geometry, then rebuild the
manifest with ``tools/build_pdf_manifest.py``.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import verify  # noqa: E402
from build_pdf_manifest import _download  # reuse the downloader  # noqa: E402

MANIFEST = ROOT / "catalog" / "pdf_manifest.json"


def check_one(fid: str, entry: dict, timeout: int, retries: int) -> dict:
    url = entry.get("url")
    if not url:
        return {"form_id": fid, "status": "NO_URL"}
    try:
        data = _download(url, timeout, retries)
    except Exception as e:  # noqa: BLE001
        return {"form_id": fid, "status": "GONE", "detail": str(e)[:160]}
    if data[:5] != b"%PDF-":
        return {"form_id": fid, "status": "GONE", "detail": "response is not a PDF"}
    got = verify.sha256_bytes(data)
    if got == entry.get("sha256"):
        return {"form_id": fid, "status": "ok", "sha256": got, "bytes": len(data)}
    return {"form_id": fid, "status": "CHANGED", "expected_sha256": entry.get("sha256"),
            "got_sha256": got, "expected_bytes": entry.get("bytes"),
            "got_bytes": len(data), "url": url}


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect upstream revisions of the source PDFs")
    ap.add_argument("--forms", help="comma-separated form ids (default: all)")
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--json", action="store_true", help="emit a JSON report")
    args = ap.parse_args()

    manifest = verify.load_manifest(MANIFEST)
    forms = manifest.get("forms", {})
    if not forms:
        print("no catalog/pdf_manifest.json yet — run tools/build_pdf_manifest.py first")
        return 2

    if args.forms:
        want = [f.strip() for f in args.forms.split(",") if f.strip()]
        unknown = [f for f in want if f not in forms]
        if unknown:
            print(f"unknown form ids (not in manifest): {', '.join(unknown)}")
            return 2
        ids = want
    else:
        ids = sorted(forms)

    results = [check_one(f, forms[f], args.timeout, args.retries) for f in ids]
    changed = [r for r in results if r["status"] == "CHANGED"]
    gone = [r for r in results if r["status"] == "GONE"]
    ok = [r for r in results if r["status"] == "ok"]

    if args.json:
        print(json.dumps({"ok": len(ok), "changed": changed, "gone": gone}, indent=2))
    else:
        for r in results:
            if r["status"] == "ok":
                continue
            if r["status"] == "CHANGED":
                print(f"  CHANGED  {r['form_id']}: URL serves different bytes — "
                      f"{r['got_sha256'][:12]}… (was {(r['expected_sha256'] or '')[:12]}…), "
                      f"{r['got_bytes']}B (was {r['expected_bytes']}). Re-derive geometry.")
            elif r["status"] == "GONE":
                print(f"  GONE     {r['form_id']}: {r.get('detail', 'download failed')} "
                      f"— revision may have been withdrawn; check source_urls.json")
            else:
                print(f"  {r['status']:<8} {r['form_id']}")
        print(f"\nok={len(ok)} changed={len(changed)} gone={len(gone)} checked={len(results)}")

    return 1 if (changed or gone) else 0


if __name__ == "__main__":
    sys.exit(main())
