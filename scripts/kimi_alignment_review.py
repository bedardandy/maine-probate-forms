"""Run Kimi K2.6 (multimodal) over output AcroForm PDFs to flag alignment,
naming, or missing-field issues per page.

Usage:
  scripts/kimi_alignment_review.py           # review all 104 output PDFs
  scripts/kimi_alignment_review.py -n 8      # first 8 PDFs only
  scripts/kimi_alignment_review.py --form DE-104   # single form

Reads OPENROUTER_API_KEY from env or ~/.config/maine-forms-loop/openrouter.env.
Writes per-form JSON reports to reports/kimi-alignment/<form_id>.json and a
summary table to reports/kimi-alignment/SUMMARY.md.

Resumes: skips form_ids that already have a report unless --rerun.
"""
from __future__ import annotations

import argparse
import base64
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
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

logger = logging.getLogger("kimi_alignment")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_PRIMARY = "moonshotai/kimi-k2.6"
MODEL_FALLBACK = "moonshotai/kimi-k2.5"
RENDER_DPI = 150
TIMEOUT = 240.0
REPORT_DIR = ROOT / "reports" / "kimi-alignment"

SYSTEM_PROMPT = """You audit AcroForm field placement on Maine probate court PDF pages.

For each page you receive:
  • a rendered image at the indicated DPI
  • a list of fields with snake_case name + bbox in PDF points (origin top-left, y-down)

Look at every field and identify problems in three categories:
  1. ALIGNMENT — the bbox does not cover the intended writeable region
     (e.g. sits over body text, off the underline, mis-overlaps a checkbox).
  2. NAMING — the snake_case name does not match the visible label or role
     (e.g. a date line named 'attorney_phone'; a county field named 'name').
  3. MISSING — a visibly writeable region (underline after a label, blank
     line, small checkbox square) has no field at that position.

Be concrete. Cite the field by its name. For MISSING issues, describe the
location ("under heading 'Heirs', row 4" or "right of label 'Date Served'").

Return ONLY a single JSON object, no prose, no fences:
{
  "fields_reviewed": <int>,
  "overall": "ok" | "minor_issues" | "major_issues",
  "issues": [
    {"type": "alignment"|"naming"|"missing", "field_name": "<or empty for missing>", "details": "<one short sentence>"}
  ]
}

If everything looks fine, return overall="ok" and issues=[]. Don't invent
issues to fill the array."""


def _load_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".config" / "maine-forms-loop" / "openrouter.env"
    if env_file.exists():
        for ln in env_file.read_text().splitlines():
            m = re.match(r"\s*export\s+OPENROUTER_API_KEY=(.+)", ln)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    raise RuntimeError("OPENROUTER_API_KEY not set and no openrouter.env found")


def _render_png(page: fitz.Page, dpi: int) -> bytes:
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def _build_field_list(page: fitz.Page) -> tuple[str, int]:
    widgets = list(page.widgets() or [])
    if not widgets:
        return "(no fields on this page)", 0
    lines = [f"Page has {len(widgets)} fields:"]
    for w in widgets:
        r = w.rect
        ftype = w.field_type_string or "text"
        lines.append(
            f"- {w.field_name!r}  type={ftype}  "
            f"rect=[{r.x0:.1f},{r.y0:.1f},{r.x1:.1f},{r.y1:.1f}]"
        )
    return "\n".join(lines), len(widgets)


def _call_kimi(
    client: httpx.Client,
    api_key: str,
    field_list_text: str,
    image_b64: str,
    page_w_pt: float,
    page_h_pt: float,
    model: str = MODEL_PRIMARY,
) -> dict:
    user_text = (
        f"Render: {RENDER_DPI} DPI. Page size in PDF points: "
        f"{page_w_pt:.0f} x {page_h_pt:.0f}.\n\n{field_list_text}"
    )
    body = {
        "model": model,
        "max_tokens": 32768,
        "temperature": 0.1,
        # Kimi K2.6 emits a lot of reasoning before answering. 23-field
        # N-115 hit empty-content at 16K, so budget needs to be generous.
        # Don't try to suppress reasoning — that produces empty content in
        # our experiments.
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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/probate-forms",
        "X-Title": "probate-forms-alignment-review",
    }
    r = client.post(OPENROUTER_URL, headers=headers, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _parse_response(j: dict) -> dict:
    raw = (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    raw = re.sub(r"```(?:json)?\s*\n?", "", raw).strip()
    # Find first {...} block
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"_parse_error": True, "_raw": raw[:400]}
    try:
        parsed = json.loads(m.group())
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([\]}])", r"\1", m.group())
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return {"_parse_error": True, "_raw": raw[:400]}
    return parsed


def review_pdf(pdf_path: Path, api_key: str) -> dict:
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
                pages_report.append({"page": pno, "fields": 0, "skipped": True})
                continue
            png = _render_png(page, RENDER_DPI)
            b64 = base64.standard_b64encode(png).decode("utf-8")
            usage = {}
            try:
                resp = _call_kimi(
                    client, api_key, field_list_text, b64,
                    page.rect.width, page.rect.height,
                )
                api_calls += 1
                parsed = _parse_response(resp)
                usage = resp.get("usage", {})
                if "_parse_error" in parsed:
                    api_errors.append(f"page {pno+1}: parse failure")
                    pages_report.append({
                        "page": pno, "fields": n_fields,
                        "_raw": parsed.get("_raw", "")[:200],
                    })
                    continue
                issues = parsed.get("issues", [])
                pages_report.append({
                    "page": pno,
                    "fields": n_fields,
                    "fields_reviewed": parsed.get("fields_reviewed", n_fields),
                    "overall": parsed.get("overall", "ok"),
                    "issues": issues,
                    "tokens_in": usage.get("prompt_tokens"),
                    "tokens_out": usage.get("completion_tokens"),
                })
                total_issues += len(issues)
                total_fields += n_fields
            except httpx.HTTPError as e:
                api_errors.append(f"page {pno+1}: {type(e).__name__}: {e}")
                pages_report.append({"page": pno, "fields": n_fields, "_error": str(e)})
    doc.close()
    return {
        "pdf": str(pdf_path),
        "pages": pages_report,
        "total_fields": total_fields,
        "total_issues": total_issues,
        "api_calls": api_calls,
        "api_errors": api_errors,
    }


def _form_id_from_pdf(pdf_path: Path) -> str:
    # output/<category>/<source_stem>_fillable.pdf → <source_stem>
    stem = pdf_path.stem
    return stem[:-len("_fillable")] if stem.endswith("_fillable") else stem


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="output", help="Root directory of fillable PDFs")
    ap.add_argument("-n", "--limit", type=int, default=None,
                    help="Stop after N PDFs (default: all)")
    ap.add_argument("--form", default=None,
                    help="Filter to a single form by id substring")
    ap.add_argument("--rerun", action="store_true",
                    help="Overwrite existing per-form reports")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    api_key = _load_api_key()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    root = Path(args.root)
    pdfs = sorted(p for p in root.rglob("*.pdf") if "previews" not in p.parts)
    if args.form:
        pdfs = [p for p in pdfs if args.form in p.stem]
    if not pdfs:
        print(f"No PDFs in {root}"); return 1
    if args.limit is not None:
        pdfs = pdfs[: args.limit]

    summary = []
    for i, pdf in enumerate(pdfs, 1):
        fid = _form_id_from_pdf(pdf)
        out_path = REPORT_DIR / f"{fid}.json"
        if out_path.exists() and not args.rerun:
            data = json.loads(out_path.read_text())
            logger.info("[%d/%d] %s — cached", i, len(pdfs), fid)
        else:
            logger.info("[%d/%d] %s — calling Kimi", i, len(pdfs), fid)
            t0 = time.time()
            data = review_pdf(pdf, api_key)
            data["form_id"] = fid
            data["elapsed_sec"] = round(time.time() - t0, 1)
            out_path.write_text(json.dumps(data, indent=2))
        summary.append((fid, data))

    # ── write summary ────────────────────────────────────────────────────
    summary_md = REPORT_DIR / "SUMMARY.md"
    lines = [
        "# Kimi K2.6 alignment review",
        "",
        f"Forms reviewed: {len(summary)}.  IoU + naming + missing audit per page.",
        "",
        "| form_id | pages | fields | issues | API calls | errors |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    total_issues_all = 0
    total_fields_all = 0
    for fid, data in summary:
        n_pages = sum(1 for p in data["pages"] if not p.get("skipped"))
        ti = data["total_issues"]
        total_issues_all += ti
        total_fields_all += data["total_fields"]
        lines.append(
            f"| {fid} | {n_pages} | {data['total_fields']} | {ti} | "
            f"{data['api_calls']} | {len(data['api_errors'])} |"
        )
    lines += [
        "",
        f"**Total fields reviewed**: {total_fields_all}",
        f"**Total issues flagged**: {total_issues_all}",
        "",
        "## Top issue types",
        "",
    ]
    type_counts: dict[str, int] = {}
    for _, data in summary:
        for p in data["pages"]:
            for iss in p.get("issues", []) or []:
                t = iss.get("type", "?")
                type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- **{t}**: {c}")

    summary_md.write_text("\n".join(lines))
    print(f"\nReports → {REPORT_DIR}/  ({len(summary)} forms, {total_issues_all} issues)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
