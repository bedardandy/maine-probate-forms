#!/usr/bin/env python3
"""OpenAI-compatible guard proxy: inspect every completion before it's returned.

Drop this in front of any OpenAI-compatible endpoint (Codex, Hermes via
vLLM/Ollama, LiteLLM, …): point the harness's ``base_url`` at this proxy, and it
forwards ``/v1/chat/completions`` upstream, runs the citation guard over the
assistant message, attaches the result + a signed attestation under
``x_citation_inspection``, and — when ``$PROXY_FAIL_CLOSED=1`` — replaces a
failing message with a refusal so bad citations can't reach the caller. This is
the provider-agnostic injection point for harnesses that don't expose hooks.

    UPSTREAM_BASE_URL=https://api.example/v1 PROXY_FAIL_CLOSED=1 \\
        python3 tools/inspect_proxy.py --port 8099
    # then set the harness base_url to http://127.0.0.1:8099/v1

Reference implementation: non-streaming (``stream:true`` requests are forwarded
unverified with a header flag). Scope the vocabulary per request with an
``X-Citation-Form`` header or ``$CITATION_GUARD_FORM``. Not legal advice.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

UPSTREAM = os.environ.get("UPSTREAM_BASE_URL") or os.environ.get(
    "ROUTER_BASE_URL", "http://127.0.0.1:8088/v1")
FAIL_CLOSED = os.environ.get("PROXY_FAIL_CLOSED") == "1"


def _message_text(body: dict) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except Exception:
        return ""
    if isinstance(content, list):                      # some servers return blocks
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content or ""


def apply_guard(resp_body: dict, *, form_id=None, fail_closed=FAIL_CLOSED,
                log_path=None, llm=False) -> dict:
    """Inspect a chat-completion response body in place and annotate it."""
    import guard
    text = _message_text(resp_body)
    if not text.strip():
        return resp_body
    res = guard.evaluate(text, form_id=form_id, llm=llm, log_path=log_path)
    resp_body["x_citation_inspection"] = {
        "blocked": bool(res["block"] and fail_closed),
        "flagged": bool(res["block"]),
        "reason": res["reason"],
        "scan": {k: res["scan"].get(k) for k in
                 ("leaked", "unresolvable", "fabricated_urls", "out_of_vocab")},
        "attestation": res.get("attestation"),
    }
    if res["block"] and fail_closed:
        try:
            resp_body["choices"][0]["message"]["content"] = (
                "[citation guard] This response was withheld pending review.\n"
                + res["reason"])
            resp_body["choices"][0]["finish_reason"] = "content_filter"
        except Exception:
            pass
    return resp_body


class _Handler(BaseHTTPRequestHandler):
    server_version = "citation-guard-proxy/1.0"

    def _send(self, code: int, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                          # quiet by default
        pass

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, b'{"error":"only /v1/chat/completions"}')
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            req = json.loads(raw)
        except Exception:
            return self._send(400, b'{"error":"invalid JSON"}')

        up = UPSTREAM.rstrip("/") + "/chat/completions"
        fwd = urllib.request.Request(up, data=raw, method="POST",
                                     headers={"Content-Type": "application/json"})
        auth = self.headers.get("Authorization")
        if auth:
            fwd.add_header("Authorization", auth)
        try:
            with urllib.request.urlopen(fwd, timeout=300) as r:
                upstream = r.read()
        except Exception as e:
            return self._send(502, json.dumps({"error": f"upstream: {e}"}).encode())

        if req.get("stream"):                           # not inspected (reference)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Citation-Inspection", "skipped-stream")
            self.send_header("Content-Length", str(len(upstream)))
            self.end_headers()
            return self.wfile.write(upstream)

        try:
            body = json.loads(upstream)
            form = self.headers.get("X-Citation-Form") or os.environ.get(
                "CITATION_GUARD_FORM") or None
            body = apply_guard(body, form_id=form,
                               llm=os.environ.get("CITATION_GUARD_LLM") == "1")
            out = json.dumps(body, ensure_ascii=False).encode("utf-8")
        except Exception:
            out = upstream                              # fail open: pass upstream through
        self._send(200, out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()
    print(f"citation-guard proxy on http://{a.host}:{a.port}/v1  -> upstream {UPSTREAM} "
          f"(fail_closed={FAIL_CLOSED})", file=sys.stderr)
    ThreadingHTTPServer((a.host, a.port), _Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
