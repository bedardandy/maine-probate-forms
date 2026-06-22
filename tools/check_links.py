#!/usr/bin/env python3
"""Dead-link detection for the citation authorities.

Self-contained (stdlib only — no ``maine_forms_engine``): classify whether a URL
is LIVE / DEAD / BLOCKED / ERROR / INCONCLUSIVE. The crucial distinction is that a
403 (legislature.maine.gov blocks non-browser User-Agents) or a 405 (HEAD not
allowed) is **not** a dead link — only ``404`` / ``410`` and a DNS failure
(NXDOMAIN) are DEAD. Only DEAD fails a build; BLOCKED/INCONCLUSIVE are reported
but never fatal, so CI behind a bot filter doesn't go permanently red.

Three consumers: this module's CLI audits the citation database; the inspector
flags a placeholder whose authority URL is dead (via
``fetch_statute_text.link_status``); ``citation_scan`` flags fabricated URLs in
free text (via :func:`statute_url_in_index`, which is fully offline).

    python3 tools/check_links.py --scope used           # used cites + cases + cross-refs
    python3 tools/check_links.py --scope all --check    # full index; exit nonzero only on DEAD
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import functools
import json
import pathlib
import re
import socket
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

IDX = ROOT / "docs" / "statute-reference" / "_index"
REPORT = ROOT / "catalog" / "link_health.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"   # legislature.maine.gov 403s bare UAs
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

LIVE, DEAD, BLOCKED, ERROR, INCONCLUSIVE = (
    "live", "dead", "blocked", "error", "inconclusive")


def classify_status(code: int) -> str:
    """Map an HTTP status code to a link verdict (DEAD ≠ BLOCKED)."""
    if 200 <= code < 400:
        return LIVE                 # 2xx, or a redirect we didn't follow
    if code in (404, 410):
        return DEAD
    if code in (401, 403, 405, 429):
        return BLOCKED
    if 500 <= code < 600:
        return ERROR
    return INCONCLUSIVE


class _HeadRequest(urllib.request.Request):
    def get_method(self) -> str:        # noqa: D401
        return "HEAD"


def _probe(url: str, method: str, timeout: int):
    cls = _HeadRequest if method == "HEAD" else urllib.request.Request
    with urllib.request.urlopen(cls(url, headers=_HEADERS), timeout=timeout) as r:
        return r.getcode(), r.geturl()


def check_url(url: str, *, timeout: int = 20, retries: int = 2) -> dict:
    """Probe one URL. HEAD first (cheap); if HEAD is rejected (400/403/405/501),
    retry as GET. Only inconclusive failures (timeout / conn reset) are retried;
    a DNS failure is reported DEAD immediately. Returns
    ``{url, status, http_code, final_url, detail}`` and never raises."""
    last_detail = "unreachable"
    for attempt in range(retries + 1):
        try:
            try:
                code, final = _probe(url, "HEAD", timeout)
            except urllib.error.HTTPError as e:
                if e.code in (400, 403, 405, 501):     # HEAD blocked -> try GET
                    code, final = _probe(url, "GET", timeout)
                else:
                    raise
            return {"url": url, "status": classify_status(code),
                    "http_code": code, "final_url": final, "detail": "ok"}
        except urllib.error.HTTPError as e:
            return {"url": url, "status": classify_status(e.code),
                    "http_code": e.code, "final_url": url, "detail": f"HTTP {e.code}"}
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, socket.gaierror):
                return {"url": url, "status": DEAD, "http_code": None,
                        "final_url": url, "detail": f"DNS failure: {reason}"}
            last_detail = f"{type(reason).__name__}: {reason}"
        except Exception as e:                          # noqa: BLE001
            last_detail = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return {"url": url, "status": INCONCLUSIVE, "http_code": None,
            "final_url": url, "detail": last_detail}


def check_urls(urls, *, timeout: int = 20, retries: int = 2, workers: int = 8,
               checker=check_url) -> dict:
    """Concurrently probe a list of URLs (deduped). ``checker`` is injectable."""
    uniq = list(dict.fromkeys(u for u in urls if u))
    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(checker, u, timeout=timeout, retries=retries): u
                for u in uniq}
        for fut in concurrent.futures.as_completed(futs):
            u = futs[fut]
            try:
                results[u] = fut.result()
            except Exception as e:                      # noqa: BLE001
                results[u] = {"url": u, "status": INCONCLUSIVE, "http_code": None,
                              "final_url": u, "detail": f"{type(e).__name__}: {e}"}
    return results


# --- offline structural check: is a statute URL real per the index? ---------- #
@functools.lru_cache(maxsize=None)
def _load(name: str) -> dict:
    return json.loads((IDX / name).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def _all_index_urls() -> frozenset:
    urls = set()
    for blob, key in (("18c-sections.json", "sections"),
                      ("cross-refs.json", "cross_refs"),
                      ("caselaw.json", "cases")):
        for meta in _load(blob)[key].values():
            if meta.get("url"):
                urls.add(meta["url"])
    return frozenset(urls)


_RE_18C_URL = re.compile(r"title18-Csec([0-9A-Za-z\-]+)\.html", re.IGNORECASE)
_RE_MRS_URL = re.compile(r"/statutes/(\d+(?:-[A-Z])?)/title[^/]*sec([0-9A-Za-z\-]+)\.html",
                         re.IGNORECASE)


def statute_url_in_index(url: str):
    """Offline: ``True``/``False`` whether a legislature.maine.gov statute URL's
    section is in the trusted index; ``None`` when the URL isn't a statute URL we
    can parse. A URL for a section not in the index is fabricated/dead for sure."""
    if not url or "legislature.maine.gov" not in url:
        return None
    if url in _all_index_urls():
        return True
    m = _RE_18C_URL.search(url)
    if m:
        return m.group(1) in _load("18c-sections.json")["sections"]
    m2 = _RE_MRS_URL.search(url)
    if m2:
        cite = f"{m2.group(1)} M.R.S. §{m2.group(2)}"
        return cite in _load("cross-refs.json")["cross_refs"]
    return None


# --- citation-DB audit ------------------------------------------------------- #
def collect_index_urls(scope: str = "used") -> dict:
    """``{url: [cites]}`` for the authority links. ``scope='used'`` limits statute
    URLs to the cites the forms actually reference; ``'all'`` is the full index."""
    sections = _load("18c-sections.json")["sections"]
    xref = _load("cross-refs.json")["cross_refs"]
    cases = _load("caselaw.json")["cases"]
    urls: dict[str, set] = {}

    def add(url, cite):
        if url:
            urls.setdefault(url, set()).add(cite)

    if scope == "all":
        for sec, meta in sections.items():
            add(meta.get("url"), f"18-C §{sec}")
    else:
        import build_statute_text_manifest as b      # local: avoids import cycle
        for cite in b.used_cites():
            if cite.startswith("18-C §"):
                meta = sections.get(cite[len("18-C §"):])
                if meta:
                    add(meta.get("url"), cite)
            elif cite in xref:
                add(xref[cite].get("url"), cite)
    for cite, meta in xref.items():                  # cross-refs + cases are small
        add(meta.get("url"), cite)
    for cid, meta in cases.items():
        add(meta.get("url"), meta.get("cite", cid))
    return {u: sorted(c) for u, c in urls.items()}


def audit(scope: str = "used", *, checker=check_url, workers: int = 8) -> dict:
    url_cites = collect_index_urls(scope)
    results = check_urls(list(url_cites), checker=checker, workers=workers)
    by_status: dict[str, list] = {}
    for u, r in results.items():
        r["cites"] = url_cites.get(u, [])
        by_status.setdefault(r["status"], []).append(u)
    return {"scope": scope, "checked": len(results),
            "by_status": {k: len(v) for k, v in sorted(by_status.items())},
            "dead": sorted(by_status.get(DEAD, [])),
            "results": results}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", choices=["used", "all"], default="used")
    ap.add_argument("--check", action="store_true",
                    help="don't write the report; exit nonzero only on a DEAD link")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rep = audit(a.scope, workers=a.workers)
    rep["generated"] = datetime.date.today().isoformat()
    if a.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(f"link audit [{a.scope}]: {rep['by_status']} "
              f"({rep['checked']} urls)")
        for u in rep["dead"]:
            cites = ", ".join(rep["results"][u]["cites"])
            print(f"  DEAD  {u}  (cites: {cites})")
    if not a.check:
        REPORT.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    return 1 if rep["dead"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
