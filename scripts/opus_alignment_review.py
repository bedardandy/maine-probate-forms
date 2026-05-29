"""Run Claude Opus 4.7 (vision) over output AcroForm PDFs to flag alignment,
naming, or missing-field issues per page.

Mirrors the Kimi K2.6 reviewer (scripts/kimi_alignment_review.py) so reports
share the same JSON schema, but invokes `claude -p` headless against the
user's Max subscription instead of OpenRouter.

Usage:
  scripts/opus_alignment_review.py                # review all 104 output PDFs
  scripts/opus_alignment_review.py -n 5           # first 5 PDFs only
  scripts/opus_alignment_review.py --form DE-104  # single form by id substring
  scripts/opus_alignment_review.py -j 4           # 4 PDFs in parallel

Reports go to reports/opus-alignment/<form_id>.json + SUMMARY.md.
Resumes by default: skips form_ids that already have a report unless --rerun.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("opus_alignment")

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
MODEL = "opus"
RENDER_DPI = 200
TIMEOUT_SEC = 600
REPORT_DIR = Path(os.environ.get("OPUS_REPORT_DIR", ROOT / "reports" / "opus-alignment"))
# OPUS_TILE_MODE: "none" (default, single page image) or "vsplit2"
# (split into top+bottom halves with 10% overlap so each half gets full
# image-token budget — better fidelity on tall pages).
TILE_MODE = os.environ.get("OPUS_TILE_MODE", "none")
TILE_OVERLAP_PCT = 0.10

SYSTEM_PROMPT = """You audit AcroForm field placement on Maine probate court PDF pages.

For each page you receive:
  - a rendered image at the indicated DPI (the user will give you a path; Read it)
  - a list of fields with snake_case name + bbox in PDF points (origin top-left, y-down)

Look at every field and identify problems in three categories:
  1. ALIGNMENT - the bbox does not cover the intended writeable region
     (e.g. sits over body text, off the underline, mis-overlaps a checkbox).
  2. NAMING - the snake_case name does not match the visible label or role
     (e.g. a date line named 'attorney_phone'; a county field named 'name').
  3. MISSING - a visibly writeable region (underline after a label, blank
     line, small checkbox square) has no field at that position.

Be concrete. Cite the field by its name. For MISSING issues, describe the
location ("under heading 'Heirs', row 4" or "right of label 'Date Served'").

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
  • move:     {"action": "move",   "to_rect": [x0, y0, x1, y1]}
  • delete:   {"action": "delete"}
  • add:      {"action": "add", "rect": [x0, y0, x1, y1], "name": "snake_case", "type": "text"|"check"|"sig"}

Coordinates are PDF points in the same system as the field rects you were
given. Only emit `fix` when:
  - rename: you are sure of the correct role-based snake_case name
  - move:   you can read the underline endpoints for the correct position
  - delete: the field overlays printed text and is clearly spurious
  - add:    a missing field has a clear underline/checkbox boundary

If everything looks fine, return overall="ok" and issues=[]. Don't invent
issues to fill the array."""


def _render_png(page: fitz.Page, dpi: int, out_path: Path) -> None:
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(str(out_path))


def _render_tiles(page: fitz.Page, dpi: int, mode: str,
                  tmpd: Path, pno: int) -> list[tuple[Path, tuple[float, float]]]:
    """Render `page` into one or more PNGs.

    Returns list of (png_path, (y0_pdf, y1_pdf)) — y-range each tile covers
    in PDF points. mode="none" → single full-page tile.
    mode="vsplit2" → two tiles (top + bottom) with TILE_OVERLAP_PCT overlap.
    """
    page_h = page.rect.height
    if mode != "vsplit2":
        out = tmpd / f"page_{pno:02d}.png"
        _render_png(page, dpi, out)
        return [(out, (0.0, page_h))]

    # Render full page once at hi-DPI, then crop top and bottom halves with overlap.
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    full_w, full_h = pix.width, pix.height
    overlap_px = int(full_h * TILE_OVERLAP_PCT)
    half = full_h // 2
    top_y1_px = half + overlap_px
    bot_y0_px = half - overlap_px
    top_y1_pdf = top_y1_px / zoom
    bot_y0_pdf = bot_y0_px / zoom

    import io
    from PIL import Image
    img = Image.frombytes("RGB", (full_w, full_h), pix.samples)
    top_path = tmpd / f"page_{pno:02d}_top.png"
    bot_path = tmpd / f"page_{pno:02d}_bot.png"
    img.crop((0, 0, full_w, top_y1_px)).save(top_path)
    img.crop((0, bot_y0_px, full_w, full_h)).save(bot_path)
    return [
        (top_path, (0.0, top_y1_pdf)),
        (bot_path, (bot_y0_pdf, page_h)),
    ]


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


def _call_opus(
    tiles: list[tuple[Path, tuple[float, float]]],
    field_list_text: str,
    page_w_pt: float,
    page_h_pt: float,
) -> tuple[dict, dict]:
    if len(tiles) == 1:
        png_path, _ = tiles[0]
        tile_block = f"Read {png_path} to see the rendered page."
    else:
        lines = ["This page has been split into multiple tiles for higher fidelity.",
                 "Read EVERY tile listed below; each covers the indicated y-range",
                 "in PDF points (origin top-left, y-down). Tiles overlap slightly",
                 "so widgets near the boundary appear on both — count each issue",
                 "once. Coordinates you emit must be in original PDF points."]
        for png_path, (y0, y1) in tiles:
            lines.append(f"  - {png_path}  covers y={y0:.0f}..{y1:.0f}")
        tile_block = "\n".join(lines)

    user_text = (
        f"{tile_block}\n\n"
        f"Render: {RENDER_DPI} DPI. Page size in PDF points: "
        f"{page_w_pt:.0f} x {page_h_pt:.0f}.\n\n{field_list_text}\n\n"
        "Audit per the rules in the system prompt and return only the JSON object."
    )
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    add_dirs = list({str(p.parent) for p, _ in tiles})
    cmd = [
        CLAUDE_BIN, "-p",
        "--model", MODEL,
        "--system-prompt", SYSTEM_PROMPT,
        "--allowedTools", "Read",
        "--allow-dangerously-skip-permissions",
    ]
    for d in add_dirs:
        cmd += ["--add-dir", d]
    cmd += [
        "--output-format", "json",
        "--disable-slash-commands",
        user_text,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"non-JSON envelope from claude: {e}; head={proc.stdout[:300]}")
    return envelope, envelope.get("usage", {}) or {}


def _parse_result_text(raw: str) -> dict:
    raw = re.sub(r"```(?:json)?\s*\n?", "", raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"_parse_error": True, "_raw": raw[:400]}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([\]}])", r"\1", m.group())
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"_parse_error": True, "_raw": raw[:400]}


def review_pdf(pdf_path: Path) -> dict:
    doc = fitz.open(pdf_path)
    pages_report: list[dict] = []
    total_issues = 0
    total_fields = 0
    api_calls = 0
    api_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="opus_review_") as tmpd:
        tmpd_path = Path(tmpd)
        for pno, page in enumerate(doc):
            field_list_text, n_fields = _build_field_list(page)
            if n_fields == 0:
                pages_report.append({"page": pno, "fields": 0, "skipped": True})
                continue
            tiles = _render_tiles(page, RENDER_DPI, TILE_MODE, tmpd_path, pno)
            try:
                envelope, usage = _call_opus(
                    tiles, field_list_text,
                    page.rect.width, page.rect.height,
                )
                api_calls += 1
                if envelope.get("is_error"):
                    api_errors.append(
                        f"page {pno+1}: claude is_error: "
                        f"{envelope.get('result', '')[:200]}"
                    )
                    pages_report.append({
                        "page": pno, "fields": n_fields,
                        "_error": envelope.get("result", "")[:200],
                    })
                    continue
                parsed = _parse_result_text(envelope.get("result", ""))
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
                    "tokens_in": usage.get("input_tokens"),
                    "tokens_out": usage.get("output_tokens"),
                    "cache_read": usage.get("cache_read_input_tokens"),
                    "duration_ms": envelope.get("duration_ms"),
                })
                total_issues += len(issues)
                total_fields += n_fields
            except (subprocess.TimeoutExpired, RuntimeError) as e:
                api_errors.append(f"page {pno+1}: {type(e).__name__}: {e}")
                pages_report.append({"page": pno, "fields": n_fields, "_error": str(e)[:300]})
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
    stem = pdf_path.stem
    return stem[:-len("_fillable")] if stem.endswith("_fillable") else stem


def _review_one(pdf: Path, idx: int, n: int, rerun: bool) -> tuple[str, dict]:
    fid = _form_id_from_pdf(pdf)
    out_path = REPORT_DIR / f"{fid}.json"
    if out_path.exists() and not rerun:
        data = json.loads(out_path.read_text())
        logger.info("[%d/%d] %s - cached", idx, n, fid)
        return fid, data
    logger.info("[%d/%d] %s - calling Opus", idx, n, fid)
    t0 = time.time()
    data = review_pdf(pdf)
    data["form_id"] = fid
    data["elapsed_sec"] = round(time.time() - t0, 1)
    out_path.write_text(json.dumps(data, indent=2))
    logger.info("[%d/%d] %s - %d issues, %ds",
                idx, n, fid, data["total_issues"], int(data["elapsed_sec"]))
    return fid, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="output", help="Root dir of fillable PDFs")
    ap.add_argument("-n", "--limit", type=int, default=None,
                    help="Stop after N PDFs (default: all)")
    ap.add_argument("--form", default=None,
                    help="Filter to a single form by id substring")
    ap.add_argument("--rerun", action="store_true",
                    help="Overwrite existing per-form reports")
    ap.add_argument("-j", "--jobs", type=int, default=1,
                    help="Parallel PDFs (default 1; 4 is reasonable)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    root = Path(args.root)
    pdfs = sorted(p for p in root.rglob("*.pdf") if "previews" not in p.parts)
    if args.form:
        pdfs = [p for p in pdfs if args.form in p.stem]
    if not pdfs:
        print(f"No PDFs in {root}"); return 1
    if args.limit is not None:
        pdfs = pdfs[: args.limit]

    summary: list[tuple[str, dict]] = []
    if args.jobs <= 1:
        for i, pdf in enumerate(pdfs, 1):
            summary.append(_review_one(pdf, i, len(pdfs), args.rerun))
    else:
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futures = {
                ex.submit(_review_one, pdf, i, len(pdfs), args.rerun): pdf
                for i, pdf in enumerate(pdfs, 1)
            }
            for fut in cf.as_completed(futures):
                summary.append(fut.result())

    summary.sort(key=lambda x: x[0])

    summary_md = REPORT_DIR / "SUMMARY.md"
    lines = [
        "# Claude Opus 4.7 alignment review",
        "",
        f"Forms reviewed: {len(summary)}.  Alignment + naming + missing audit per page.",
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
    print(f"\nReports -> {REPORT_DIR}/  ({len(summary)} forms, {total_issues_all} issues)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
