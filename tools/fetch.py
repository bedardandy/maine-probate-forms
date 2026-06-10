#!/usr/bin/env python3
"""Fetch (and cache) a form's blank flat source PDF from its official source_url.

The blank PDFs are public records on maineprobate.net and are not redistributed
in this repo; every consumer (CLI fill, MCP fill_form, HTTP /fill, the enhance
pipeline) needs the same fetch: read `repo/forms/<ID>/metadata.json.source_url`,
download with the project's User-Agent, sanity-check the bytes are a PDF, and
cache. A cached copy is reused only when it still matches the SHA-256 pinned in
``catalog/pdf_manifest.json`` (or when the form has no manifest entry); a stale
cache triggers a re-download.

    from fetch import fetch_source
    src = fetch_source("DE-101")          # -> pathlib.Path to the cached PDF
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.request

import verify

ROOT = pathlib.Path(__file__).resolve().parent.parent
USER_AGENT = "Mozilla/5.0"
CACHE = pathlib.Path(os.environ.get("MPF_SOURCE_CACHE", "/tmp/probate_source_cache"))


def source_url(form_id: str, root: pathlib.Path = ROOT) -> str:
    meta_path = root / "repo" / "forms" / form_id / "metadata.json"
    if not meta_path.exists():
        raise RuntimeError(f"unknown form {form_id!r} (no metadata.json)")
    m = json.loads(meta_path.read_text())
    url = m.get("source_url") or m.get("source_pdf")
    if not url:
        raise RuntimeError(f"no source_url for {form_id}")
    return url


def fetch_source(form_id: str, fresh: bool = False, url: str | None = None,
                 cache_dir: pathlib.Path | None = None,
                 root: pathlib.Path = ROOT, timeout: int = 60) -> pathlib.Path:
    """Return a local path to the blank source PDF for ``form_id``.

    Cached under ``CACHE`` (override with $MPF_SOURCE_CACHE or ``cache_dir``).
    ``fresh=True`` forces a re-download. A cached file that no longer matches the
    manifest SHA-256 is re-fetched rather than reused.
    """
    cache = pathlib.Path(cache_dir) if cache_dir else CACHE
    cache.mkdir(parents=True, exist_ok=True)
    dst = cache / f"{form_id}.pdf"
    if dst.exists() and dst.stat().st_size > 800 and not fresh:
        ok, _ = verify.verify_pdf(form_id, dst)
        if ok or verify.manifest_entry(form_id) is None:
            return dst
    rq = urllib.request.Request(url or source_url(form_id, root),
                                headers={"User-Agent": USER_AGENT})
    data = urllib.request.urlopen(rq, timeout=timeout).read()
    if data[:5] != b"%PDF-":
        raise RuntimeError(f"fetched source for {form_id} is not a PDF")
    dst.write_bytes(data)
    return dst
