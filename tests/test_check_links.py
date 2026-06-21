"""Offline tests for the dead-link checker (monkeypatched urlopen; no network)."""
import pathlib
import socket
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_links as cl                    # noqa: E402


class _Resp:
    def __init__(self, code, url):
        self._code, self._url = code, url

    def getcode(self):
        return self._code

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake(spec):
    """spec: 'live200' | 'http404' | 'http403' | 'head405' | 'dns' | 'timeout'."""
    def fake(req, timeout=None):
        url, method = req.full_url, req.get_method()
        if spec == "live200":
            return _Resp(200, url)
        if spec.startswith("http"):
            raise urllib.error.HTTPError(url, int(spec[4:]), "x", {}, None)
        if spec == "head405":                       # HEAD blocked, GET ok
            if method == "HEAD":
                raise urllib.error.HTTPError(url, 405, "method", {}, None)
            return _Resp(200, url)
        if spec == "dns":
            raise urllib.error.URLError(socket.gaierror("Name or service not known"))
        if spec == "timeout":
            raise urllib.error.URLError(socket.timeout("timed out"))
        raise AssertionError(spec)
    return fake


# --- classify_status: DEAD != BLOCKED -------------------------------------- #
def test_classify_status_buckets():
    assert cl.classify_status(200) == cl.LIVE
    assert cl.classify_status(301) == cl.LIVE
    assert cl.classify_status(404) == cl.DEAD
    assert cl.classify_status(410) == cl.DEAD
    assert cl.classify_status(403) == cl.BLOCKED      # blocked, not dead
    assert cl.classify_status(405) == cl.BLOCKED
    assert cl.classify_status(500) == cl.ERROR


# --- check_url over a fake transport --------------------------------------- #
def test_check_url_live(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake("live200"))
    assert cl.check_url("https://x/ok")["status"] == cl.LIVE


def test_check_url_404_is_dead(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake("http404"))
    r = cl.check_url("https://x/gone")
    assert r["status"] == cl.DEAD and r["http_code"] == 404


def test_check_url_403_is_blocked_not_dead(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake("http403"))
    assert cl.check_url("https://legislature.maine.gov/x")["status"] == cl.BLOCKED


def test_check_url_head_405_retries_as_get(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake("head405"))
    assert cl.check_url("https://x/headblocked")["status"] == cl.LIVE


def test_check_url_dns_failure_is_dead(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake("dns"))
    r = cl.check_url("https://nope.invalid/x", retries=0)
    assert r["status"] == cl.DEAD


def test_check_url_timeout_is_inconclusive(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake("timeout"))
    r = cl.check_url("https://slow/x", retries=0)
    assert r["status"] == cl.INCONCLUSIVE


# --- offline structural statute-URL check ---------------------------------- #
def test_statute_url_in_index_real_fake_and_nonstatute():
    base = "https://legislature.maine.gov/statutes/18-C/"
    assert cl.statute_url_in_index(base + "title18-Csec3-401.html") is True
    assert cl.statute_url_in_index(base + "title18-Csec99-999.html") is False
    assert cl.statute_url_in_index("https://law.justia.com/cases/maine/x") is None


def test_collect_index_urls_used_is_offline():
    urls = cl.collect_index_urls("used")
    assert any("title18-Csec3-401.html" in u for u in urls)
    assert any("courts.maine.gov" in u or "justia.com" in u for u in urls)  # a case url


def test_check_urls_dedupes(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _fake("live200"))
    out = cl.check_urls(["https://x/a", "https://x/a", "https://x/b"], workers=2)
    assert set(out) == {"https://x/a", "https://x/b"}
