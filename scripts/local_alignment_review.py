"""Local-model alignment review — uses any OpenAI-compatible vision endpoint.

Defaults to a local llama-router (qwen3.6-35b on localhost:8083) but configurable
via env vars so you can target any other endpoint (vLLM, OpenRouter, etc.).

Env vars:
  AUDIT_BASE_URL  — OpenAI-compatible /v1/chat/completions root (default: http://localhost:8083/v1)
  AUDIT_MODEL     — model name on the endpoint (default: qwen3.6-35b)
  AUDIT_API_KEY   — bearer token if endpoint requires one (default: empty for local)
  AUDIT_REPORT_DIR — output directory (default: reports/local-alignment)

Usage:
  scripts/local_alignment_review.py                      # all PDFs under output/
  scripts/local_alignment_review.py --root output_fused  # different root
  scripts/local_alignment_review.py --form PP-205        # filter to one form
  scripts/local_alignment_review.py -j 4                 # 4 parallel PDFs
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import fitz
import httpx

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE_URL = os.environ.get("AUDIT_BASE_URL", "http://localhost:8083/v1")
DEFAULT_MODEL = os.environ.get("AUDIT_MODEL", "qwen3.6-35b")
DEFAULT_API_KEY = os.environ.get("AUDIT_API_KEY", "")
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR", ROOT / "reports" / "local-alignment"))

RENDER_DPI = 150
TIMEOUT = float(os.environ.get("AUDIT_TIMEOUT", "1800"))
# Toggle whether to suppress Qwen3-family reasoning output. Default: think.
# Set AUDIT_NO_THINK=1 to disable; with thinking off MAX_TOKENS can drop too.
DISABLE_THINKING = os.environ.get("AUDIT_NO_THINK", "0") == "1"
MAX_TOKENS = int(os.environ.get(
    "AUDIT_MAX_TOKENS",
    "8192" if DISABLE_THINKING else "65536"))

SYSTEM_PROMPT = """You audit AcroForm field placement on Maine probate court PDF pages.

For each page you receive:
  - a rendered image at the indicated DPI
  - a list of fields with snake_case name + bbox in PDF points (origin top-left, y-down)

Look at every field and identify problems in three categories:
  1. ALIGNMENT - the bbox does not cover the intended writeable region
     (e.g. sits over body text, off the underline, mis-overlaps a checkbox).
  2. NAMING - the snake_case name does not match the visible label or role
     (e.g. a date line named 'attorney_phone'; a county field named 'name').
  3. MISSING - a visibly writeable region (underline after a label, blank
     line, small checkbox square) has no field at that position.

Be concrete. Cite the field by its name. For MISSING issues, describe the location.

Return ONLY a single JSON object, no prose, no fences:
{
  "fields_reviewed": <int>,
  "overall": "ok" | "minor_issues" | "major_issues",
  "issues": [
    {
      "type": "alignment"|"naming"|"missing",
      "field_name": "<or empty for missing>",
      "details": "<one short sentence>",
      "fix": <one of the structured-fix shapes below, or null if no clean fix>
    }
  ]
}

Structured-fix shapes — include `fix` only when a high-confidence action is
unambiguous from the page; otherwise omit it or set null:
  • rename:   {"action": "rename", "to": "snake_case_name"}
  • move:     {"action": "move", "to_rect": [x0, y0, x1, y1]}
  • delete:   {"action": "delete"}
  • add:      {"action": "add", "rect": [x0, y0, x1, y1], "name": "snake_case", "type": "text"|"check"|"sig"}

Coordinates are PDF points in the same system as the field rects shown.

If everything looks fine, return overall="ok" and issues=[].
"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("local-audit")


def _render_png(page: fitz.Page, dpi: int) -> bytes:
    return page.get_pixmap(dpi=dpi).tobytes("png")


def _build_field_list(page: fitz.Page) -> tuple[str, int]:
    widgets = list(page.widgets() or [])
    if not widgets:
        return "", 0
    lines = [f"Page has {len(widgets)} fields:"]
    for w in widgets:
        r = w.rect
        ftype = w.field_type_string or "text"
        lines.append(
            f"- {w.field_name!r}  type={ftype}  "
            f"rect=[{r.x0:.1f},{r.y0:.1f},{r.x1:.1f},{r.y1:.1f}]"
        )
    return "\n".join(lines), len(widgets)


def _call_model(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    model: str,
    field_list_text: str,
    image_b64: str,
    page_w_pt: float,
    page_h_pt: float,
) -> dict:
    user_text = (
        f"Render: {RENDER_DPI} DPI. Page size in PDF points: "
        f"{page_w_pt:.0f} x {page_h_pt:.0f}.\n\n{field_list_text}"
    )
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                    {"type": "text", "text": user_text},
                ],
            },
        ],
    }
    if DISABLE_THINKING:
        # Qwen3-family — recognized by both vLLM and llama.cpp servers.
        body["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = base_url.rstrip("/") + "/chat/completions"
    r = client.post(url, headers=headers, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _parse_response(j: dict) -> dict:
    """Parse JSON answer from response. If `content` is empty (Qwen3.5+ with the
    qwen3 reasoning parser routes the entire response to `reasoning_content`
    when thinking hits max_tokens without a closing `</think>` tag — see
    vLLM issues #35221, #38894, #40816), fall back to `reasoning_content`.
    """
    msg = (j.get("choices") or [{}])[0].get("message", {}) or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""

    def try_parse(text: str) -> dict | None:
        cleaned = re.sub(r"```(?:json)?\s*\n?", "", text).strip()
        # Find the LAST {...} block — for reasoning_content, the answer JSON
        # typically appears at the very end after long deliberation.
        candidates = list(re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, re.DOTALL))
        if not candidates:
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            candidates = [m] if m else []
        for m in reversed(candidates):
            blob = m.group()
            try:
                return json.loads(blob)
            except json.JSONDecodeError:
                fixed = re.sub(r",\s*([\]}])", r"\1", blob)
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    continue
        return None

    parsed = try_parse(content) if content else None
    if parsed is None and reasoning:
        parsed = try_parse(reasoning)
    if parsed is None:
        # Tertiary fallback: extract issues from natural-language reasoning trace.
        # When the model never closes the JSON but described problems in prose,
        # regex out lines that look like 'field X' + 'should/missing/aligned' etc.
        text = reasoning or content
        if text:
            drafted = _draft_issues_from_trace(text)
            if drafted:
                return {
                    "fields_reviewed": 0,
                    "overall": "drafted_from_trace",
                    "issues": drafted,
                    "_drafted_from_trace": True,
                }
        return {"_parse_error": True, "_raw": (content or reasoning)[:400]}
    return parsed


_TRACE_FIELD_PATTERN = re.compile(
    r"['\"`]?(?P<name>[a-z][a-z0-9_]{2,})['\"`]?\s+"
    r"(?:is|appears to be|sits|should be|seems|covers|extends|overlaps|misses)",
    re.IGNORECASE,
)
_TRACE_TYPE_HINTS = {
    "alignment": ("align", "off", "shift", "covers", "overlap", "wrong position", "not on"),
    "naming":    ("named", "should be named", "rename", "misnamed", "name does not", "name is"),
    "missing":   ("no field", "missing", "not present", "should have a field", "lacks"),
}


def _draft_issues_from_trace(text: str) -> list[dict]:
    """Salvage rough issue records from natural-language reasoning when JSON fails."""
    issues = []
    seen = set()
    for line in re.split(r"(?<=[\.!?])\s+|\n", text):
        line = line.strip()
        if len(line) < 20 or len(line) > 400:
            continue
        m = _TRACE_FIELD_PATTERN.search(line)
        if not m:
            continue
        field_name = m.group("name")
        lower = line.lower()
        type_ = "alignment"
        for t, hints in _TRACE_TYPE_HINTS.items():
            if any(h in lower for h in hints):
                type_ = t
                break
        key = (field_name, type_)
        if key in seen:
            continue
        seen.add(key)
        issues.append({
            "type": type_,
            "field_name": field_name,
            "details": line[:200],
        })
        if len(issues) > 30:
            break
    return issues


def review_pdf(pdf_path: Path, base_url: str, api_key: str, model: str) -> dict:
    doc = fitz.open(pdf_path)
    pages_report: list[dict] = []
    total_issues = 0
    total_fields = 0
    api_calls = 0
    api_errors: list[str] = []
    with httpx.Client() as client:
        for pno, page in enumerate(doc):
            field_list_text, n_fields = _build_field_list(page)
            if n_fields == 0:
                pages_report.append({"page_number": pno, "fields": 0, "issues": []})
                continue
            png = _render_png(page, RENDER_DPI)
            b64 = base64.standard_b64encode(png).decode("utf-8")
            try:
                resp = _call_model(client, base_url, api_key, model,
                                   field_list_text, b64,
                                   page.rect.width, page.rect.height)
                api_calls += 1
                parsed = _parse_response(resp)
                if "_parse_error" in parsed:
                    api_errors.append(f"page {pno+1}: parse failure")
                    pages_report.append({
                        "page_number": pno, "fields": n_fields, "issues": [],
                        "_raw": parsed.get("_raw", "")[:200],
                    })
                    continue
                issues = parsed.get("issues", [])
                pages_report.append({
                    "page_number": pno,
                    "fields": n_fields,
                    "fields_reviewed": parsed.get("fields_reviewed", n_fields),
                    "overall": parsed.get("overall", "ok"),
                    "issues": issues,
                })
                total_issues += len(issues)
                total_fields += n_fields
            except httpx.HTTPError as e:
                api_errors.append(f"page {pno+1}: {type(e).__name__}: {e}")
                pages_report.append({"page_number": pno, "fields": n_fields,
                                     "issues": [], "_error": str(e)})
    doc.close()
    return {
        "pdf": str(pdf_path),
        "model": model,
        "pages": pages_report,
        "total_issues": total_issues,
        "total_fields": total_fields,
        "api_calls": api_calls,
        "errors": api_errors,
    }


def _form_id_from_pdf(pdf_path: Path) -> str:
    name = pdf_path.stem
    # Strip suffixes like _fused, _fillable, _commonforms, _staged
    for suffix in ("_fused", "_fillable", "_commonforms", "_staged"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _review_one(pdf: Path, base_url: str, api_key: str, model: str, rerun: bool) -> tuple[str, dict]:
    form_id = _form_id_from_pdf(pdf)
    out_path = REPORT_DIR / f"{pdf.stem}.json"
    if out_path.exists() and not rerun:
        return form_id, {"skipped": True}
    t0 = time.time()
    try:
        report = review_pdf(pdf, base_url, api_key, model)
    except Exception as e:
        return form_id, {"_error": f"{type(e).__name__}: {e}"}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    elapsed = time.time() - t0
    log.info(f"{form_id} - {report['total_issues']} issues, {elapsed:.0f}s")
    return form_id, report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="output", help="root dir of fillable PDFs")
    ap.add_argument("-n", "--limit", type=int, default=None)
    ap.add_argument("--form", default=None, help="filter to PDFs whose name matches substring")
    ap.add_argument("--rerun", action="store_true", help="overwrite existing reports")
    ap.add_argument("-j", "--jobs", type=int, default=1)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    args = ap.parse_args()

    # Endpoint healthcheck
    try:
        r = httpx.get(args.base_url.rstrip("/") + "/models", timeout=5)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Endpoint {args.base_url} unreachable: {e}")
        log.error(
            "Start the local model first. Examples:\n"
            "  sudo systemctl start llama-router  # qwen3.6-35b on localhost:8083\n"
            "  AUDIT_BASE_URL=http://localhost:8088/v1 AUDIT_MODEL=qwen3.6-27b-vllm  # alt endpoint"
        )
        return 2

    log.info(f"Audit endpoint: {args.base_url}  model: {args.model}")

    root = Path(args.root)
    pdfs = sorted(root.rglob("*.pdf"))
    if args.form:
        pdfs = [p for p in pdfs if args.form in p.name]
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        log.error(f"No PDFs found under {root}")
        return 1

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Reviewing {len(pdfs)} PDFs (jobs={args.jobs})")

    if args.jobs <= 1:
        for pdf in pdfs:
            _review_one(pdf, args.base_url, args.api_key, args.model, args.rerun)
    else:
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = [ex.submit(_review_one, pdf, args.base_url, args.api_key, args.model, args.rerun)
                    for pdf in pdfs]
            for _ in cf.as_completed(futs):
                pass

    log.info(f"Reports -> {REPORT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
