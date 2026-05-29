"""Local alignment review v2 — checklist harness.

Built from Opus baseline analysis (1,460 issues, 79 forms):
  - 71% of issues are NAMING (per-field judgment)
  - issues-per-field rate: median 0.43, p25 0.29, p75 0.62
  - 0% of alignment issues come back with structured to_rect

So instead of opt-in "list problems," v2 forces a verdict for every input field.
The model returns N rows for N fields. Skip = error. "ok" is per-field, not global.
A return below the p10 issue rate triggers an automatic re-audit.

Output schema is identical to v1 (pages → issues[]) so recursive_improvement.py
consumes it without changes. v2 internally converts per-field verdicts to
v1-shaped issue records.

Env vars (all from v1 plus a v2-specific knob):
  AUDIT_BASE_URL, AUDIT_MODEL, AUDIT_API_KEY, AUDIT_REPORT_DIR
  AUDIT_NO_THINK   — set to 1 to disable Qwen3-family thinking
  AUDIT_MAX_TOKENS — default 8192 (no-think) / 65536 (think)
  AUDIT_TIMEOUT    — seconds, default 1800
  AUDIT_RECALL_FLOOR — fraction of fields that must be flagged on forms
                       with >MIN_FIELDS fields, else re-audit. Default 0.10.
  AUDIT_MIN_FIELDS — only check recall floor when n_fields >= this. Default 20.
  AUDIT_NEARBY_TEXT — set to 1 to inject per-field local-text context for
                      grounding rename suggestions to the row, not the page.
                      Default 1.

Usage matches v1.
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
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR",
                                 ROOT / "reports" / "local-alignment-v2"))

RENDER_DPI = 150
TIMEOUT = float(os.environ.get("AUDIT_TIMEOUT", "1800"))
DISABLE_THINKING = os.environ.get("AUDIT_NO_THINK", "0") == "1"
MAX_TOKENS = int(os.environ.get(
    "AUDIT_MAX_TOKENS",
    "8192" if DISABLE_THINKING else "65536"))

# Re-audit thresholds: from Opus corpus, p10 issue rate is ~0.10.
# Forms with >= MIN_FIELDS that flag less than RECALL_FLOOR get one retry
# with a stricter system prompt.
RECALL_FLOOR = float(os.environ.get("AUDIT_RECALL_FLOOR", "0.10"))
MIN_FIELDS_FOR_FLOOR = int(os.environ.get("AUDIT_MIN_FIELDS", "20"))

# Per-field row-local text context. Default ON: Opus sanity (2026-05-08) showed
# 75% of post-fix issues were naming bugs from globally-scanned Qn labels in
# tabular forms. Anchoring the rename pass to row-local text fixes this.
NEARBY_TEXT_ENABLED = os.environ.get("AUDIT_NEARBY_TEXT", "1") == "1"
NEARBY_X_PAD = float(os.environ.get("AUDIT_NEARBY_X_PAD", "260"))
NEARBY_Y_PAD = float(os.environ.get("AUDIT_NEARBY_Y_PAD", "28"))
NEARBY_MAX_SPANS = int(os.environ.get("AUDIT_NEARBY_MAX_SPANS", "20"))

SYSTEM_PROMPT_V2 = """You audit AcroForm field placement on a Maine probate court PDF page.

INPUT: a rendered page image plus an ordered list of N existing fields, each with
its current name and bbox in PDF points (origin top-left, y-down).

TASK: For EVERY field in the input list, return one verdict row, in the same
order as the input. The number of rows you return MUST equal N. Skipping a
field is an error.

OUTPUT — return ONLY this JSON object, no prose, no fences:

{
  "page_number": <int>,
  "field_audits": [
    {
      "name": "<exact name from the input list>",
      "verdict": "ok" | "rename" | "misaligned" | "duplicate" | "spurious",
      "rename_to": "<new snake_case name>" | null,
      "shift": "left" | "right" | "up" | "down" | "wider" | "narrower" | null,
      "details": "<one sentence anchoring to a specific visible label or text>"
    },
    ... one entry per input field, same order ...
  ],
  "missing_fields": [
    {
      "near": "<label or anchor text the missing field is next to>",
      "details": "<one sentence: where on the page, e.g. 'underline after Telephone:' >"
    }
  ]
}

PROCEDURE — apply IN THIS ORDER for every field:

  Step 1 — NAMING.  Read the visible label nearest to the field's bbox.
            Does the snake_case name reflect that label? If not, verdict =
            "rename" and supply rename_to. Examples of common naming bugs:
              * generic auto-names (`text_p0_5`, `field_3`, `signature`,
                `check_p0`, `signature_2`) — name from the adjacent label
              * wrong context (`decedent_name` on a guardianship-only form)
              * wrong specific role (`decision_date` for a field that is
                actually the recipient name on a 'mailed to ___' line)
              * uninformative numeric suffix (`petition_type_2` — encode
                what `_2` represents, e.g. `petition_type_successor`)
              * column-header confusion (a `relationship_to_minor` row in
                a table whose header actually says 'Relationship to
                Respondent' — match the header)
              * tabular row mislabel (a field on row Q1 named `q4_*`
                because the model pulled the label from somewhere else
                on the page — see ROW ANCHORING below)

            ROW ANCHORING — when the input includes a `nearby_text:` line
            for a field, that block is the text within ~28pt vertically
            and ~260pt horizontally of the bbox, labeled by relative
            position: `row-left` / `row-right` (same row), `above-*` /
            `below-*` (other rows / headers). Snippets are sorted with
            same-row first.

              * The Qn / question-number / row label for THIS field MUST
                come from a `row-*` snippet. If the row only contains
                "1." but elsewhere on the page there is "Q4. Child
                support", the field is row Q1, NOT Q4. Use the row-local
                number.
              * `above-*` snippets are headers / column titles (use them
                for the column noun: `parent_1` vs `parent_2`).
              * `below-*` snippets describe the next row, not this one.
                Don't pull the question label from below.
              * If a field's nearby_text shows a clear row-local question
                (e.g. "row-left: '11.', row-left: 'Home value'"), and the
                current name says `q3_*`, that is a clear rename.

  Step 2 — ALIGNMENT.  Only after deciding naming, ask: does the bbox sit
            on the intended writeable region? Real alignment problems vary;
            DO NOT default to "shift=up" for many fields in a row. Inspect
            each bbox individually. Common alignment bugs:
              * overshoots/undershoots the visible underline horizontally
              * spans the wrong row in a numbered question table
              * sits over a label or column header instead of the input
              * full-page-width rect over a heading, not an underline
              * narrow rect with no underline beneath it at all
            For a misalignment with no clear single direction, use the
            shift that BEST describes the dominant correction needed.

  Step 3 — DUPLICATE / SPURIOUS.  If two fields cover the same writeable
            region, the lower-quality one is "duplicate". If a field has
            no writeable region at all (just covers body text or a
            heading), it is "spurious".

  Step 4 — MISSING.  Scan for visible underlines, blanks, and small
            checkbox squares with no widget at that position. Add them
            to missing_fields with a label anchor.

CALIBRATION (corpus statistics from prior audits, n=79 forms, 1460 issues):

  Type mix:    naming 71%   alignment 19%   missing 10%
  Issue rate:  median 0.43 issues per field   (p25 0.29, p75 0.62, max 1.00)

  If your verdict mix departs strongly from the corpus (e.g. 0% rename, or
  90% misaligned), you are almost certainly missing the harder naming work.
  Naming is 3.8× more common than alignment — prefer "rename" over
  "misaligned" when in doubt.

  A return of 0 (or near-0) non-"ok" verdicts on a form with > 20 fields
  is almost always an audit failure — re-examine before submitting.

ANTI-PATTERNS to avoid:
  * Returning the same `details` text or same `shift` for many fields in
    a row — that is pattern-matching, not perception. Each verdict must
    cite a specific label or text element on the page.
  * Skipping naming because the form looks "ok at first glance" — most
    fields with non-trivial names have a naming defect somewhere.
  * Marking every field "ok" — see the calibration baseline.

Return exactly N field_audits entries.
"""

STRICTER_SUFFIX = """

NOTE: This is a re-audit. The prior pass returned implausibly few non-"ok"
verdicts for a form with this many fields. Look closer at each name relative
to its visible label — most fields on this page likely have a naming or
alignment problem. Trust the calibration: ~43% of fields need a fix.
"""

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("local-audit-v2")


def _render_png(page: fitz.Page, dpi: int) -> bytes:
    return page.get_pixmap(dpi=dpi).tobytes("png")


def _local_text_context(page: fitz.Page, rect: fitz.Rect,
                        text_dict: dict | None = None) -> str:
    """Pull text spans within ±NEARBY_*_PAD of the widget. Each span is
    labeled by its position relative to the widget bbox so the model can
    distinguish 'label to my left in the same row' from 'header above'
    from 'unrelated text far below'. Spans are sorted by Manhattan
    distance to the widget center and capped at NEARBY_MAX_SPANS.
    Pass text_dict to amortize page.get_text() across all widgets on the page."""
    td = text_dict if text_dict is not None else page.get_text("dict")
    ctx = fitz.Rect(rect.x0 - NEARBY_X_PAD, rect.y0 - NEARBY_Y_PAD,
                    rect.x1 + NEARBY_X_PAD, rect.y1 + NEARBY_Y_PAD)
    wcx, wcy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    items: list[tuple[float, str]] = []
    for block in td.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bb = span.get("bbox")
                if not bb or len(bb) != 4:
                    continue
                sx0, sy0, sx1, sy1 = bb
                if sx1 < ctx.x0 or sx0 > ctx.x1:
                    continue
                if sy1 < ctx.y0 or sy0 > ctx.y1:
                    continue
                text = (span.get("text") or "").strip()
                # Skip pure underscore/whitespace runs — those are anchor lines, not labels.
                if not text or all(ch == "_" or ch.isspace() for ch in text):
                    continue
                cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
                # Vertical relation
                if sy1 < rect.y0 - 2:
                    rel_y = "above"
                elif sy0 > rect.y1 + 2:
                    rel_y = "below"
                else:
                    rel_y = "row"
                # Horizontal relation
                if sx1 < rect.x0 - 2:
                    rel_x = "left"
                elif sx0 > rect.x1 + 2:
                    rel_x = "right"
                else:
                    rel_x = "over"
                # Sort by row-priority: same row first, then above (header),
                # then below; within band, by absolute distance.
                row_bonus = {"row": 0.0, "above": 100.0,
                             "below": 200.0}[rel_y]
                dist = row_bonus + abs(cx - wcx) + abs(cy - wcy)
                items.append((dist, f"{rel_y}-{rel_x}: {text!r}"))
    items.sort(key=lambda t: t[0])
    return " | ".join(s for _, s in items[:NEARBY_MAX_SPANS])


def _build_field_list(page: fitz.Page) -> tuple[str, int, list[str]]:
    widgets = list(page.widgets() or [])
    if not widgets:
        return "", 0, []
    names = [w.field_name for w in widgets]
    lines = [f"Page has {len(widgets)} fields. Return exactly {len(widgets)} field_audits entries:"]
    text_dict = page.get_text("dict") if NEARBY_TEXT_ENABLED else None
    for i, w in enumerate(widgets):
        r = w.rect
        ftype = w.field_type_string or "text"
        lines.append(
            f"  {i+1}. name={w.field_name!r}  type={ftype}  "
            f"rect=[{r.x0:.1f},{r.y0:.1f},{r.x1:.1f},{r.y1:.1f}]"
        )
        if NEARBY_TEXT_ENABLED:
            ctx = _local_text_context(page, r, text_dict=text_dict)
            if ctx:
                lines.append(f"     nearby_text: {ctx}")
    return "\n".join(lines), len(widgets), names


def _call_model(client: httpx.Client, base_url: str, api_key: str, model: str,
                field_list_text: str, image_b64: str, page_w_pt: float,
                page_h_pt: float, system_prompt: str) -> dict:
    user_text = (
        f"Render: {RENDER_DPI} DPI. Page size in PDF points: "
        f"{page_w_pt:.0f} x {page_h_pt:.0f}.\n\n{field_list_text}"
    )
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
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
        body["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = base_url.rstrip("/") + "/chat/completions"
    r = client.post(url, headers=headers, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _parse_response(j: dict) -> dict:
    msg = (j.get("choices") or [{}])[0].get("message", {}) or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""

    def try_parse(text: str) -> dict | None:
        cleaned = re.sub(r"```(?:json)?\s*\n?", "", text).strip()
        # Match the largest balanced object — works even with nested arrays/objects.
        depth = 0
        start = -1
        last_obj = None
        for i, ch in enumerate(cleaned):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    blob = cleaned[start:i+1]
                    try:
                        last_obj = json.loads(blob)
                    except json.JSONDecodeError:
                        try:
                            last_obj = json.loads(re.sub(r",\s*([\]}])", r"\1", blob))
                        except json.JSONDecodeError:
                            pass
                    start = -1
        return last_obj

    parsed = try_parse(content) if content else None
    if parsed is None and reasoning:
        parsed = try_parse(reasoning)
    if parsed is None:
        return {"_parse_error": True, "_raw": (content or reasoning)[:400]}
    return parsed


def _audits_to_issues(audits: list[dict], missing: list[dict],
                      page_no: int) -> list[dict]:
    """Translate v2 per-field verdicts to v1 issue records consumed by
    scripts/recursive_improvement.py."""
    issues = []
    for a in audits or []:
        v = a.get("verdict", "ok")
        if v == "ok":
            continue
        name = a.get("name", "").strip()
        details = a.get("details", "").strip()
        if v == "rename" and a.get("rename_to") and name:
            issues.append({
                "type": "naming", "field_name": name, "details": details,
                "fix": {"action": "rename", "to": a["rename_to"]},
            })
        elif v == "misaligned" and name:
            shift = a.get("shift") or ""
            full_details = f"{details} (shift hint: {shift})" if shift else details
            issues.append({
                "type": "alignment", "field_name": name,
                "details": full_details, "fix": None,
            })
        elif v == "duplicate" and name:
            issues.append({
                "type": "alignment", "field_name": name,
                "details": f"Duplicate (redundant) widget: {details}",
                "fix": {"action": "delete", "reason": "duplicate redundant"},
            })
        elif v == "spurious" and name:
            issues.append({
                "type": "alignment", "field_name": name,
                "details": f"Spurious widget: {details}",
                "fix": {"action": "delete", "reason": "spurious extraneous"},
            })
    for m in missing or []:
        details = m.get("details") or m.get("near") or ""
        if not details:
            continue
        issues.append({
            "type": "missing", "field_name": "",
            "details": details, "fix": None,
        })
    return issues


def _audit_page(client, base_url, api_key, model, field_list_text,
                image_b64, page_w_pt, page_h_pt, n_fields,
                system_prompt) -> tuple[dict, int]:
    """Run one audit attempt. Returns (parsed_response, n_audit_rows)."""
    resp = _call_model(client, base_url, api_key, model, field_list_text,
                       image_b64, page_w_pt, page_h_pt, system_prompt)
    parsed = _parse_response(resp)
    audits = parsed.get("field_audits") or []
    return parsed, len(audits)


def review_pdf(pdf_path: Path, base_url: str, api_key: str, model: str) -> dict:
    doc = fitz.open(pdf_path)
    pages_report: list[dict] = []
    total_issues = 0
    total_fields = 0
    api_calls = 0
    api_errors: list[str] = []
    rerun_pages = 0

    with httpx.Client() as client:
        for pno, page in enumerate(doc):
            field_list_text, n_fields, field_names = _build_field_list(page)
            if n_fields == 0:
                pages_report.append({"page_number": pno, "fields": 0, "issues": []})
                continue
            png = _render_png(page, RENDER_DPI)
            b64 = base64.standard_b64encode(png).decode("utf-8")

            try:
                parsed, n_rows = _audit_page(
                    client, base_url, api_key, model,
                    field_list_text, b64, page.rect.width, page.rect.height,
                    n_fields, SYSTEM_PROMPT_V2,
                )
                api_calls += 1
                if "_parse_error" in parsed:
                    api_errors.append(f"page {pno+1}: parse failure")
                    pages_report.append({
                        "page_number": pno, "fields": n_fields, "issues": [],
                        "_raw": parsed.get("_raw", "")[:200],
                    })
                    continue

                audits = parsed.get("field_audits") or []
                missing = parsed.get("missing_fields") or []

                # Recall floor: if implausibly few non-ok verdicts on a non-trivial
                # page, re-audit once with stricter prompt.
                non_ok = sum(1 for a in audits if a.get("verdict", "ok") != "ok")
                rate = non_ok / max(1, n_fields)
                if (n_fields >= MIN_FIELDS_FOR_FLOOR
                        and rate < RECALL_FLOOR):
                    log.info(f"  page {pno+1}: rerun ({non_ok}/{n_fields} flagged "
                             f"= {rate:.2f} < floor {RECALL_FLOOR})")
                    parsed_b, _ = _audit_page(
                        client, base_url, api_key, model,
                        field_list_text, b64, page.rect.width, page.rect.height,
                        n_fields, SYSTEM_PROMPT_V2 + STRICTER_SUFFIX,
                    )
                    api_calls += 1
                    rerun_pages += 1
                    if "_parse_error" not in parsed_b:
                        audits_b = parsed_b.get("field_audits") or []
                        missing_b = parsed_b.get("missing_fields") or []
                        non_ok_b = sum(1 for a in audits_b
                                       if a.get("verdict", "ok") != "ok")
                        if non_ok_b > non_ok:
                            audits = audits_b
                            missing = missing_b

                issues = _audits_to_issues(audits, missing, pno)
                pages_report.append({
                    "page_number": pno,
                    "fields": n_fields,
                    "fields_reviewed": len(audits),
                    "issues": issues,
                    "field_audits": audits,
                    "missing_fields": missing,
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
        "harness": "v2-checklist",
        "thinking": not DISABLE_THINKING,
        "pages": pages_report,
        "total_issues": total_issues,
        "total_fields": total_fields,
        "api_calls": api_calls,
        "rerun_pages": rerun_pages,
        "errors": api_errors,
    }


def _form_id_from_pdf(pdf_path: Path) -> str:
    name = pdf_path.stem
    for suffix in ("_fused", "_fillable", "_commonforms", "_staged"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _review_one(pdf: Path, base_url: str, api_key: str, model: str, rerun: bool):
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
    log.info(f"{form_id} - {report['total_issues']} issues, "
             f"{report['rerun_pages']} reruns, {elapsed:.0f}s")
    return form_id, report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="output", help="root dir of fillable PDFs")
    ap.add_argument("-n", "--limit", type=int, default=None)
    ap.add_argument("--form", default=None,
                    help="filter to PDFs whose name matches substring")
    ap.add_argument("--rerun", action="store_true",
                    help="overwrite existing reports")
    ap.add_argument("-j", "--jobs", type=int, default=1)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--api-key", default=DEFAULT_API_KEY)
    args = ap.parse_args()

    try:
        r = httpx.get(args.base_url.rstrip("/") + "/models", timeout=5)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Endpoint unreachable: {args.base_url} ({e})")
        return 2

    log.info(f"Audit endpoint: {args.base_url}  model: {args.model}")
    log.info(f"Harness: v2-checklist  thinking: {not DISABLE_THINKING}  "
             f"max_tokens: {MAX_TOKENS}  recall_floor: {RECALL_FLOOR}")

    root = Path(args.root)
    pdfs = sorted([p for p in root.rglob("*.pdf")
                   if not p.name.startswith(".")])
    if args.form:
        pdfs = [p for p in pdfs if args.form in p.name]
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        log.error("no PDFs to review")
        return 1
    log.info(f"Reviewing {len(pdfs)} PDFs (jobs={args.jobs})")

    if args.jobs <= 1:
        for pdf in pdfs:
            _review_one(pdf, args.base_url, args.api_key, args.model, args.rerun)
    else:
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(_review_one, pdf, args.base_url,
                                args.api_key, args.model, args.rerun)
                    for pdf in pdfs]
            for f in cf.as_completed(futs):
                f.result()

    log.info(f"Reports -> {REPORT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
