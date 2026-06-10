#!/usr/bin/env python3
"""Detect when maine.gov has re-issued a form out from under its geometry.

Shim over the shared ``maine-forms-engine``
(``maine_forms_engine.drift.check_upstream``); the CLI is unchanged — and
gains ``--update-manifest`` (adopt new sha256/bytes/num_pages/has_acroform
for CHANGED forms after re-deriving their geometry), which this repo's fork
lacked.

Each form's flat source PDF is pinned by SHA-256 in
``catalog/pdf_manifest.json`` — the revision its ``fill_geometry.json``
coordinates were measured against. maineprobate.net URLs are revision-stamped,
so a re-issued form usually means the pinned URL stops resolving (GONE) or,
less often, serves different bytes at the same URL (CHANGED). Either way the
shipped coordinates may no longer line up and the form needs its geometry
re-derived. Read-only by default; exits non-zero on any CHANGED/GONE, so it
works as a scheduled early-warning.

    python3 tools/check_upstream.py                 # check every form
    python3 tools/check_upstream.py --forms DE-101,DE-405
    python3 tools/check_upstream.py --json          # machine-readable report
"""
import pathlib
import sys

from maine_forms_engine.drift import check_upstream as _cu

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import verify  # noqa: E402
from build_pdf_manifest import _download  # noqa: E402,F401 — kept patchable for tests

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "catalog" / "pdf_manifest.json"

_UPDATE_HINT = ("Re-derive each affected form's geometry "
                "(fill_geometry.json), then rebuild the manifest with "
                "tools/build_pdf_manifest.py before trusting fills.")


def check_one(fid: str, entry: dict, timeout: int, retries: int) -> dict:
    """Probe one form's pinned URL and classify it against the manifest."""
    return _cu.check_one(fid, entry, timeout, retries,
                         downloader=lambda u, t, r: _download(u, t, r))


def main() -> int:
    if not verify.load_manifest(MANIFEST).get("forms"):
        print("no catalog/pdf_manifest.json yet — run tools/build_pdf_manifest.py first")
        return 2
    return _cu.main(default_manifest=MANIFEST,
                    update_hint=_UPDATE_HINT,
                    downloader=lambda u, t, r: _download(u, t, r),
                    default_timeout=40)


if __name__ == "__main__":
    sys.exit(main())
