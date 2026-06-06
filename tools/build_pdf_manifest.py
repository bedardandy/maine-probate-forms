#!/usr/bin/env python3
"""Bootstrap (or refresh) catalog/pdf_manifest.json — the per-form PDF anchor.

Unlike the court / corporation libraries, this repo had no SHA-256 anchor for
its source PDFs: the fill path fetches each form's flat PDF from
``metadata.json.source_url`` and draws text at the coordinates in
``fill_geometry.json``. If maine.gov re-issues a form, the geometry no longer
lines up and a fill silently lands text in the wrong place. This tool records
the bytes the geometry was built against so drift can be detected.

maineprobate.net URLs are revision-stamped (the revision is in the filename), so
the same URL serves the same bytes — downloading now reproduces the build-time
revision. As a self-check, each download's page count and page size are compared
to the ``n_pages`` / ``page_size`` recorded in the form's ``fill_geometry.json``;
a mismatch means the geometry was built against a *different* PDF than the URL
now serves (pre-existing drift) and is reported, not silently written.

    python3 tools/build_pdf_manifest.py            # all forms
    python3 tools/build_pdf_manifest.py --forms DE-101,DE-405
    python3 tools/build_pdf_manifest.py --check    # report only; do not write

Writes ``catalog/pdf_manifest.json`` keyed by form id (the shape
``tools/check_upstream.py`` and ``tools/verify.py`` read).
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time
import urllib.request

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_URLS = ROOT / "catalog" / "source_urls.json"
MANIFEST = ROOT / "catalog" / "pdf_manifest.json"
FORMS = ROOT / "repo" / "forms"
USER_AGENT = "maine-probate-forms/build_pdf_manifest (+https://www.maineprobate.net)"


def _download(url: str, timeout: int, retries: int) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last


def _geometry_expectation(form_id: str):
    """Return (n_pages, page_size) the geometry was built against, or (None, None)."""
    gp = FORMS / form_id / "fill_geometry.json"
    if not gp.exists():
        return None, None
    g = json.loads(gp.read_text())
    return g.get("n_pages"), g.get("page_size")


def build_one(form_id: str, url: str, timeout: int, retries: int) -> dict:
    data = _download(url, timeout, retries)
    if data[:5] != b"%PDF-":
        return {"form_id": form_id, "error": "response is not a PDF (error/HTML page)"}
    doc = fitz.open(stream=data, filetype="pdf")
    num_pages = doc.page_count
    pw, ph = (round(doc[0].rect.width), round(doc[0].rect.height)) if num_pages else (None, None)
    entry = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "num_pages": num_pages,
        "url": url,
    }
    # Self-check against the geometry the repo ships for this form.
    exp_pages, exp_size = _geometry_expectation(form_id)
    warn = None
    if exp_pages is not None and exp_pages != num_pages:
        warn = f"geometry expects {exp_pages} pages, URL serves {num_pages}"
    elif exp_size and pw is not None and (abs(exp_size[0] - pw) > 2 or abs(exp_size[1] - ph) > 2):
        warn = f"geometry page_size {exp_size}, URL serves [{pw}, {ph}]"
    return {"form_id": form_id, "entry": entry, "warn": warn}


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap catalog/pdf_manifest.json")
    ap.add_argument("--forms", help="comma list (default: all in source_urls.json)")
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--check", action="store_true", help="report only; do not write the manifest")
    args = ap.parse_args()

    urls = json.loads(SOURCE_URLS.read_text())["forms"]
    ids = ([f.strip() for f in args.forms.split(",")] if args.forms else sorted(urls))
    unknown = [f for f in ids if f not in urls]
    if unknown:
        print(f"unknown form ids (not in source_urls.json): {', '.join(unknown)}")
        return 2

    forms, warns, fails = {}, [], []
    for i, fid in enumerate(ids, 1):
        try:
            r = build_one(fid, urls[fid], args.timeout, args.retries)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(ids)}] FAIL  {fid}: {e}")
            fails.append(fid)
            continue
        if r.get("error"):
            print(f"  [{i}/{len(ids)}] FAIL  {fid}: {r['error']}")
            fails.append(fid)
            continue
        forms[fid] = r["entry"]
        flag = f"  ⚠ {r['warn']}" if r["warn"] else ""
        if r["warn"]:
            warns.append((fid, r["warn"]))
        print(f"  [{i}/{len(ids)}] ok    {fid}: {r['entry']['bytes']}B "
              f"{r['entry']['num_pages']}pg {r['entry']['sha256'][:12]}…{flag}")

    print(f"\nhashed {len(forms)} / {len(ids)} forms; "
          f"{len(warns)} geometry mismatch(es); {len(fails)} download failure(s)")
    if warns:
        print("geometry mismatches (URL serves a different PDF than the geometry was built on):")
        for fid, w in warns:
            print(f"  {fid}: {w}")
    if fails:
        print("failed:", ", ".join(fails))

    if args.check:
        print("\n--check: not writing the manifest")
        return 1 if fails else 0

    # Merge into any existing manifest (a partial --forms run updates in place).
    manifest = {"forms": {}}
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text())
        manifest.setdefault("forms", {})
    manifest["forms"].update(forms)
    manifest["count"] = len(manifest["forms"])
    manifest["source"] = "maineprobate.net (per-form source_url; revision-stamped filenames)"
    manifest["note"] = ("SHA-256 of the flat source PDF each form's fill_geometry was built "
                        "against. Verified at fill time and by tools/check_upstream.py.")
    manifest["forms"] = {k: manifest["forms"][k] for k in sorted(manifest["forms"])}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {MANIFEST.relative_to(ROOT)} ({manifest['count']} forms)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
