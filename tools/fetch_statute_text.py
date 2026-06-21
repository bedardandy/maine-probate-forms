#!/usr/bin/env python3
"""Fetch (and cache) the verbatim text of a Maine statute section, for the
citation inspector.

Mirrors ``tools/fetch.py`` (download + cache) and ``tools/verify.py`` (SHA-256
manifest), with one critical difference: statute HTML pages are NOT byte-stable.
Unlike the revision-stamped source PDFs on maineprobate.net, the statutory text on
legislature.maine.gov is wrapped in navigation / header / footer / analytics
markup that changes independently of the law. So we pin the SHA-256 of the
*normalized extracted text*, not the raw HTML bytes, in
``catalog/statute_text_manifest.json``. The one fragile thing — pulling the
section body out of the page — is quarantined in :func:`_extract_statute_text`
and frozen-fixture tested.

legislature.maine.gov returns HTTP 403 to non-browser User-Agents, so we send a
browser-like UA (the descriptive UA ``build_pdf_manifest.py`` uses is rejected by
this host).

    from fetch_statute_text import fetch_statute_text
    res = fetch_statute_text("18-C §3-401")   # -> {cite, url, text, text_verified, sha256}

``text_verified`` is ``True`` when the extracted text matches the manifest pin,
``False`` when it differs (the statute was re-issued), and ``None`` when the cite
is not pinned yet. Not legal advice.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html as _html
import json
import os
import pathlib
import re
import sys
import urllib.request
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
IDX = ROOT / "docs" / "statute-reference" / "_index"
MANIFEST = ROOT / "catalog" / "statute_text_manifest.json"
CACHE = pathlib.Path(os.environ.get("MPF_STATUTE_CACHE", "/tmp/probate_statute_cache"))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"   # legislature.maine.gov 403s bare UAs
EXTRACTOR_VERSION = 1


# --------------------------------------------------------------------------- #
# Citation -> URL (over the trusted index)                                    #
# --------------------------------------------------------------------------- #
def _idx(name: str) -> dict:
    return json.loads((IDX / name).read_text(encoding="utf-8"))


def statute_url(cite: str) -> str | None:
    """Resolve a cite to its legislature.maine.gov URL via the trusted index."""
    cite = (cite or "").strip()
    xref = _idx("cross-refs.json")["cross_refs"]
    if cite in xref:
        return xref[cite].get("url")
    if cite.startswith("18-C §"):
        sec = cite[len("18-C §"):]
        sections = _idx("18c-sections.json")["sections"]
        if sec in sections:
            return sections[sec].get("url")
    return None


# --------------------------------------------------------------------------- #
# HTML -> normalized statute text (the one fragile, isolated, tested function) #
# --------------------------------------------------------------------------- #
class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "head", "nav", "header", "footer",
             "noscript", "form", "button", "select"}
    _BREAK = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif self._skip == 0 and tag in self._BREAK:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif self._skip == 0 and tag in self._BREAK:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def _normalize(text: str) -> str:
    text = _html.unescape(text)
    text = re.sub(r"[\u00a0\u2009\u202f\u200b]", " ", text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _section_token(cite: str) -> str | None:
    m = re.search(r"§\s*([0-9A-Za-z\-]+)", cite or "")
    return m.group(1) if m else None


def _extract_statute_text(html_text: str, cite: str) -> str:
    """Pull the section body out of a legislature.maine.gov statute page.

    Strips scripts/nav/header/footer, normalizes whitespace, then narrows to the
    region starting at the ``§<section>`` heading and ending at the next *different*
    section heading or a known footer marker. Falls back to the whole normalized
    body when the section marker is not found, so a layout change degrades to
    "too much text" rather than "no text".
    """
    parser = _TextExtractor()
    parser.feed(html_text or "")
    body = _normalize(parser.text())
    sec = _section_token(cite)
    if not sec:
        return body
    start = re.search(r"§\s*" + re.escape(sec) + r"\b", body)
    if not start:
        return body
    tail = body[start.start():]
    end = len(tail)
    nxt = re.search(r"\n[^\n]*§\s*(?!" + re.escape(sec) + r"\b)[0-9]", tail)
    if nxt:
        end = nxt.start()
    for marker in ("The Revisor's Office", "Office of the Revisor",
                   "This page is maintained", "Data for this page"):
        fi = tail.find(marker)
        if 0 < fi < end:
            end = fi
    return tail[:end].strip()


# --------------------------------------------------------------------------- #
# Manifest (SHA-256 of the normalized text)                                   #
# --------------------------------------------------------------------------- #
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_manifest(path=None) -> dict:
    p = pathlib.Path(path) if path else MANIFEST
    if not p.exists():
        return {"extractor_version": EXTRACTOR_VERSION, "statutes": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def manifest_entry(cite: str, manifest=None) -> dict | None:
    man = manifest if manifest is not None else load_manifest()
    return man.get("statutes", {}).get(cite)


# --------------------------------------------------------------------------- #
# Fetch + cache                                                               #
# --------------------------------------------------------------------------- #
def _download(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def fetch_statute_text(cite: str, *, url: str | None = None, fresh: bool = False,
                       cache_dir=None, timeout: int = 60, manifest=None) -> dict:
    """Return ``{cite, url, text, text_verified, sha256}`` for a statute section.

    Cached under ``CACHE`` (override with ``$MPF_STATUTE_CACHE`` or ``cache_dir``).
    A cached copy is reused only when it still matches the manifest SHA (or the
    cite is not pinned). On a fetch failure returns ``text=None`` with an
    ``error`` — the caller treats that as an unresolved citation rather than
    silently substituting a weaker authority.
    """
    cite = (cite or "").strip()
    url = url or statute_url(cite)
    if not url:
        return {"cite": cite, "url": None, "text": None, "text_verified": None,
                "sha256": None, "error": f"no url for cite {cite!r} in the index"}

    cache = pathlib.Path(cache_dir) if cache_dir else CACHE
    cache.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z]+", "_", cite).strip("_") or "cite"
    dst = cache / f"{safe}.txt"
    entry = manifest_entry(cite, manifest)

    text = None
    if dst.exists() and not fresh:
        text = dst.read_text(encoding="utf-8")
        if entry and entry.get("sha256") and sha256_text(text) != entry["sha256"]:
            text = None                     # cache no longer matches the pin

    if text is None:
        try:
            html_text = _download(url, timeout=timeout)
        except Exception as e:
            return {"cite": cite, "url": url, "text": None, "text_verified": None,
                    "sha256": None, "error": f"fetch failed: {type(e).__name__}: {e}"}
        text = _extract_statute_text(html_text, cite)
        dst.write_text(text, encoding="utf-8")

    sha = sha256_text(text)
    if entry and entry.get("sha256"):
        text_verified = sha == entry["sha256"]
    else:
        text_verified = None                # not pinned yet
    return {"cite": cite, "url": url, "text": text,
            "text_verified": text_verified, "sha256": sha}


def manifest_record(cite: str, res: dict) -> dict:
    """Manifest entry for a freshly fetched cite (used by the manifest builder)."""
    return {
        "cite": cite,
        "url": res.get("url"),
        "sha256": res.get("sha256"),
        "chars": len(res.get("text") or ""),
        "fetched": datetime.date.today().isoformat(),
        "extractor_version": EXTRACTOR_VERSION,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cite", help='e.g. "18-C §3-401" or "36 M.R.S. §4107"')
    ap.add_argument("--fresh", action="store_true", help="ignore the cache")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = fetch_statute_text(a.cite, fresh=a.fresh)
    if a.json:
        out = dict(res)
        if out.get("text"):
            out["text_preview"] = out.pop("text")[:400]
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif res.get("text"):
        print(f"{res['cite']}  (text_verified={res['text_verified']}, "
              f"sha {res['sha256'][:12]}…)\n{res['url']}\n")
        print(res["text"][:1200])
    else:
        print(f"{res['cite']}: {res.get('error')}", file=sys.stderr)
    return 0 if res.get("text") else 1


if __name__ == "__main__":
    raise SystemExit(main())
