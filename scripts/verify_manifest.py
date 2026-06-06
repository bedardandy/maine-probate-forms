#!/usr/bin/env python3
"""CI gate for catalog/pdf_manifest.json — the source-PDF drift anchor.

Pure stdlib, offline (no network, no PDFs). Two checks:

1. Structure — the manifest covers every form in ``catalog/source_urls.json``,
   each entry has a 64-hex sha256, a positive byte size, and a URL, and the
   entries are sorted by form id.
2. Self-test — ``tools/verify.py``'s byte verifier accepts a matching PDF and
   rejects a swapped one (so the fill-time guard provably fires).

Exit non-zero on any failure.
"""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import verify  # noqa: E402

SOURCE_URLS = ROOT / "catalog" / "source_urls.json"
MANIFEST = ROOT / "catalog" / "pdf_manifest.json"


def check_structure() -> list[str]:
    errs: list[str] = []
    if not MANIFEST.exists():
        return [f"missing {MANIFEST.relative_to(ROOT)} (run tools/build_pdf_manifest.py)"]
    man = json.loads(MANIFEST.read_text())
    forms = man.get("forms", {})
    urls = json.loads(SOURCE_URLS.read_text())["forms"]

    missing = [f for f in urls if f not in forms]
    if missing:
        errs.append(f"{len(missing)} form(s) in source_urls.json but not in manifest: "
                    f"{', '.join(sorted(missing)[:8])}")
    extra = [f for f in forms if f not in urls]
    if extra:
        errs.append(f"{len(extra)} manifest form(s) not in source_urls.json: "
                    f"{', '.join(sorted(extra)[:8])}")
    for fid, e in forms.items():
        sha = e.get("sha256", "")
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            errs.append(f"{fid}: sha256 is not 64 hex chars")
        if not isinstance(e.get("bytes"), int) or e["bytes"] <= 0:
            errs.append(f"{fid}: bytes is not a positive int")
        if not str(e.get("url", "")).startswith("http"):
            errs.append(f"{fid}: url is missing or not http(s)")
    if list(forms) != sorted(forms):
        errs.append("manifest forms are not sorted by id")
    return errs


def check_selftest() -> list[str]:
    blank = b"%PDF-1.7 the revision the geometry was built against\n"
    man = {"forms": {"T": {"sha256": hashlib.sha256(blank).hexdigest(),
                           "bytes": len(blank), "url": "https://x/T"}}}
    errs: list[str] = []
    ok, _ = verify.verify_bytes("T", blank, manifest=man)
    if not ok:
        errs.append("verify_bytes rejected a matching PDF")
    ok, _ = verify.verify_bytes("T", b"a different revision", manifest=man)
    if ok:
        errs.append("verify_bytes accepted a swapped PDF")
    try:
        verify.guard_pdf("T", __file__, mode="strict", manifest=man)  # this .py is not the PDF
        errs.append("guard_pdf strict did not raise on mismatch")
    except verify.BlankRevisionError:
        pass
    return errs


def main() -> int:
    errs = check_structure() + check_selftest()
    if errs:
        print("pdf_manifest verification FAILED:")
        for e in errs:
            print(f"  - {e}")
        return 1
    man = json.loads(MANIFEST.read_text())
    print(f"pdf_manifest OK: {len(man['forms'])} forms anchored, structure + guard self-test pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
