"""Verify a source PDF against the SHA-256 recorded in catalog/pdf_manifest.json.

This library fills *flat* (non-AcroForm) PDFs by drawing text at the coordinates
in ``fill_geometry.json``. Those coordinates were measured against one specific
revision of each form, whose bytes are pinned in the manifest. If maine.gov
re-issues a form and its layout shifts, the coordinates no longer line up and a
fill lands text in the wrong place. Verifying the source PDF against the manifest
before drawing catches that — instead of silently producing a misaligned fill.

The manifest is keyed by form id under ``"forms"``, e.g.
``{"forms": {"DE-101": {"sha256": …, "bytes": …, "num_pages": …, "url": …}}}``.
Build/refresh it with ``tools/build_pdf_manifest.py``.
"""
import hashlib
import json
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MANIFEST = _ROOT / "catalog" / "pdf_manifest.json"


class BlankRevisionWarning(UserWarning):
    """The source PDF does not match the manifest hash (non-fatal)."""


class BlankRevisionError(RuntimeError):
    """The source PDF does not match the manifest hash (strict mode)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(manifest_path=None):
    path = pathlib.Path(manifest_path) if manifest_path else _MANIFEST
    if not path.exists():
        return {"forms": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_entry(form_id, manifest=None):
    man = manifest if manifest is not None else load_manifest()
    return man.get("forms", {}).get(form_id)


def verify_bytes(form_id, data: bytes, manifest=None):
    """Check raw PDF ``data`` for ``form_id`` against the manifest.

    Returns ``(ok, detail)``. ``ok`` is ``True`` only when a manifest entry with
    a SHA-256 exists and ``data`` matches it byte-for-byte.
    """
    entry = manifest_entry(form_id, manifest)
    if entry is None:
        return False, f"{form_id}: not in pdf_manifest.json (run tools/build_pdf_manifest.py)"
    expected = entry.get("sha256")
    if not expected:
        return False, f"{form_id}: manifest has no sha256 to verify against"
    if entry.get("bytes") is not None and len(data) != entry["bytes"]:
        return False, (f"{form_id}: source PDF size {len(data)} != manifest {entry['bytes']} — "
                       "the geometry was built against a different revision")
    got = sha256_bytes(data)
    if got != expected:
        return False, (f"{form_id}: source PDF SHA-256 mismatch — the form's coordinates were "
                       f"built against a different revision (got {got[:12]}…, manifest "
                       f"{expected[:12]}…); re-derive fill_geometry for this form")
    return True, f"{form_id}: source PDF verified against manifest"


def verify_pdf(form_id, pdf_path, manifest=None):
    """Like :func:`verify_bytes` but reads ``pdf_path`` from disk."""
    p = pathlib.Path(pdf_path)
    if not p.exists():
        return False, f"{form_id}: source PDF not found ({p})"
    return verify_bytes(form_id, p.read_bytes(), manifest)


def guard_pdf(form_id, pdf_path, mode="warn", manifest=None) -> bool:
    """Fill-time guard. ``mode`` is ``"warn"`` (default), ``"strict"``, or ``"off"``.

    ``warn`` emits :class:`BlankRevisionWarning` and returns ``False`` on
    mismatch; ``strict`` raises :class:`BlankRevisionError`; ``off`` skips the
    check. A clean verify always returns ``True``. If no manifest exists yet, the
    guard is a no-op (returns ``True``) so it never blocks a repo without one.
    """
    if mode == "off":
        return True
    if not _MANIFEST.exists() and manifest is None:
        return True
    ok, detail = verify_pdf(form_id, pdf_path, manifest)
    if ok:
        return True
    if mode == "strict":
        raise BlankRevisionError(detail)
    import warnings
    warnings.warn(detail, BlankRevisionWarning, stacklevel=3)
    return False
