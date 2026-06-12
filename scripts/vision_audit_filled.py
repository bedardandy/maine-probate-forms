"""Vision audit of FILLED probate forms via headless Claude Code (Opus).

JSON validation only checks values + enum membership. It cannot see whether
typed text landed inside the underline, overlapped a "$" glyph, fell into the
wrong column, or got truncated past the right margin. This script closes that
gap:

  1. For each (case, event, form_id), load the post-fix-stack JSON
     (filled_router.<event>.<form>.fixed.json) and stamp its values into the
     post-rect-fix AcroForm template (output_fused/<cat>/<form>_fused.pdf)
     using modules.form_filler.fill_form().
  2. Rasterize each page of the stamped PDF to PNG at 200 DPI.
  3. For each page, call `claude -p --model opus --output-format json` with
     a structured rendering-defect prompt; pass the image path so the headless
     session reads it via the Read tool.
  4. Parse the verdict JSON and write a row to router/vision_audit_report.tsv.

The headless invocation explicitly strips ANTHROPIC_API_KEY so the OAuth Max
subscription auth is used (else the inherited dev key 401s).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

import fitz
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from modules.form_filler import fill_form  # noqa: E402


PILOT: list[tuple[str, str, str]] = [
    # (case_id, event_tag, form_id) — covers currency-fix, vanilla, and
    # all newly-routed claim/disallowance forms.
    ("2024-CP-011493", "e1_decedent_death_date",            "DE-101(I)"),
    ("2024-CP-011493", "e2_pr_appointment_date",            "DE-405"),
    ("2024-CP-011493", "e3_claim_filing_date",              "DE-503"),
    ("2024-CP-011493", "e4_claim_disallowance_notice_date", "DE-504"),
    ("2024-CP-011493", "e4_claim_disallowance_notice_date", "PP-409"),
    ("2024-CP-011493", "e5_appointment_anniversary",        "DE-406"),
    ("2024-CP-011493", "e2_pr_appointment_date",            "N-106"),
]

DPI = 200
PER_PAGE_TIMEOUT = 300  # seconds

SYSTEM_PROMPT = """You audit a RENDERED page of a filled Maine probate court PDF.

The form has already been stamped with values via AcroForm. You are looking
for rendering defects — issues with HOW the typed values sit on the page.
You are NOT validating the values themselves. A wrong-but-readable value is
fine; a correct value that overlaps a printed "$" glyph or floats above the
underline is NOT fine.

Issue kinds to look for:
  - overlaps_glyph     typed value LITERALLY overlaps a printed char so the
                       two are unreadable or fused (e.g. typed "$18" written
                       directly on top of a printed "$"). Mere adjacency to
                       a printed "$" glyph is NORMAL — currency widgets are
                       designed so the typed digits sit just to the right
                       of a printed "$". Only flag this if pixels actually
                       overlay or there is zero visible whitespace.
  - above_underline    typed value floats noticeably above the underline
  - below_underline    typed value sits below the underline
  - truncated          value clipped at right margin or widget edge
  - wrong_column       value rendered under a different label than expected
  - illegible          rendering too small/garbled to read
  - blank_required     a visibly-labeled field has no value at all

EXCLUSIONS — do NOT flag these as `blank_required`:
  - Wet-ink signature widgets: any unfilled space directly above or on a line
    labeled "Signature of …", "Personal Representative", "Petitioner",
    "Petitioner or Attorney for Petitioner", "Conservator", "Guardian",
    "Affiant", "Notary Public", "Register of Probate", "Judge of Probate".
    These are signed by hand AFTER printing and are expected to be empty.
  - "Dated:" widgets that sit immediately to the left of a wet-ink signature
    line (same reason — filled in pen at signing).
  - Court-clerk / judge fields: any field under headers like
    "ORDER OF THE COURT", "DECREE", "Disposition by Judge", or following
    a printed "JUDGE" / "ORDER" caption — these are completed by the court,
    not the petitioner.
  - Fields with parenthetical conditionals in the label like
    "(if Under 18)", "(if applicable)", "(if any)", "(optional)" — blank is
    an acceptable default for these.
  - Short uppercase codes printed in a corner or footer (e.g. "MARP", "MRP",
    "MARC", form-revision footer codes) — these are administrative markers
    printed by the court, not user-fillable fields. Skip.
  - **Empty rows in multi-row list tables** (e.g. "Names and addresses of
    heirs", "Interested parties", "Beneficiaries", "Real property
    inventory"). These tables provide MORE rows than the typical case
    needs; trailing blank rows are an expected affordance. Only flag the
    table as blank_required if EVERY row is empty AND the table is
    contextually required (e.g. an heirs list on a small-estate petition).
    If at least row 1 is populated, accept blank rows 2+.

For each issue, name the visible label of the affected field as you read it
on the page (e.g. "Total Amount", "County", "Date of Death"). Quote what the
field shows verbatim if readable.

Severity:
  - major: changes legal meaning, illegible, overlaps glyph, truncated info
  - minor: cosmetic drift but still readable

Return STRICTLY JSON, no prose, no code fences:
{
  "page_ok": <true|false>,
  "issues": [
    {"label": "<visible label>", "value": "<what the field shows>",
     "kind": "<one of above>", "severity": "minor|major",
     "evidence": "<one short sentence describing where on the page>"}
  ],
  "overall": "clean|minor|major"
}
"""


def find_template_pdf(form_id: str) -> pathlib.Path | None:
    """Map form_id → the same template the router uses for filling.

    Source of truth is repo/forms/<form_id>/schema.json's `source_pdf`
    field — that's the *exact* tree-renamed PDF the router stamps.
    Falls back to a glob only if the schema is missing.

    Why: glob matching is ambiguous for forms with multiple variants
    (e.g. DE-201 has both Formal and Informal (I) PDFs in output_tree/,
    and rglob('DE-201 *_tree.pdf') with a literal space picks the
    Formal variant while the schema points to Informal). When the audit
    uses a different template than production, widget rect positions
    differ — `apply_tree` renames widgets by reading-order, so an
    audit-template that diverges from production produces spurious
    "wrong_column" findings that don't reflect real production output.
    """
    schema_path = ROOT / "repo" / "forms" / form_id / "schema.json"
    if schema_path.exists():
        try:
            schema = json.loads(schema_path.read_text())
            src = schema.get("source_pdf")
            if src:
                p = ROOT / src
                if p.exists():
                    return p
        except Exception:
            pass
    candidates = list((ROOT / "output_tree").rglob(f"{form_id} *_tree.pdf"))
    if not candidates:
        candidates = list((ROOT / "output_tree").rglob(f"{form_id}*_tree.pdf"))
    return candidates[0] if candidates else None


def find_tree(form_id: str) -> pathlib.Path | None:
    p = ROOT / "trees" / f"{form_id}.yaml"
    return p if p.exists() else None


def stamp_form(case_id: str, event: str, form_id: str,
               out_pdf: pathlib.Path) -> pathlib.Path | None:
    filled_path = (ROOT / "intermediate" / "router" / case_id
                   / f"filled_router.{event}.{form_id}.fixed.json")
    template = find_template_pdf(form_id)
    tree_path = find_tree(form_id)
    if not filled_path.exists():
        print(f"    ! missing filled JSON: {filled_path.name}")
        return None
    if template is None:
        print(f"    ! no template PDF for {form_id}")
        return None
    if tree_path is None:
        print(f"    ! no tree YAML for {form_id}")
        return None

    filled = json.loads(filled_path.read_text())
    answers = filled.get("answers", {})
    tree = yaml.safe_load(tree_path.read_text())

    # Build a node-type index so we know whether to stamp text or to
    # translate into `{nid}__{value}` checkbox widgets (the convention
    # apply_tree.py uses for select_one/select_many options).
    node_types: dict[str, dict] = {}
    for n in tree.get("nodes", []):
        if isinstance(n, dict) and n.get("id"):
            node_types[n["id"]] = n

    field_data: dict[str, str] = {}
    for fid, a in answers.items():
        v = a.get("value")
        if v in (None, "", []):
            continue
        node = node_types.get(fid, {})
        ntype = node.get("type", "text")
        if ntype == "select_many":
            # value is a list of selected options; tick each {fid}__{val}
            vals = v if isinstance(v, list) else [v]
            for opt in vals:
                if opt in (None, "", False):
                    continue
                field_data[f"{fid}__{opt}"] = "Yes"
        elif ntype == "select_one":
            # value is a single string; tick {fid}__{val}
            field_data[f"{fid}__{v}"] = "Yes"
        elif ntype in ("checkbox", "boolean"):
            # bare checkbox at {fid}
            if isinstance(v, bool):
                field_data[fid] = "Yes" if v else "Off"
            else:
                sv = str(v).strip().lower()
                field_data[fid] = "Yes" if sv in ("yes", "true", "1", "on") else "Off"
        elif isinstance(v, bool):
            field_data[fid] = "Yes" if v else "Off"
        elif isinstance(v, (int, float)):
            field_data[fid] = str(v)
        else:
            field_data[fid] = str(v)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fill_form(str(template), field_data, str(out_pdf),
              tree=tree, form_id=form_id)
    return out_pdf


def render_pages(pdf_path: pathlib.Path, out_dir: pathlib.Path,
                 dpi: int = DPI) -> list[pathlib.Path]:
    doc = fitz.open(str(pdf_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    pngs: list[pathlib.Path] = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for i in range(doc.page_count):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat)
        png_path = out_dir / f"page_{i+1:02d}.png"
        pix.save(str(png_path))
        pngs.append(png_path)
    doc.close()
    return pngs


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(text: str) -> dict | None:
    """Find the outermost {...} block in the assistant's response text."""
    if not text:
        return None
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _claude_call(png_path: pathlib.Path, logf) -> tuple[dict | None, str]:
    user_prompt = (
        f"Use the Read tool to open the image at {png_path} and audit it "
        f"per the system prompt. Output JSON only."
    )
    cmd = [
        "claude", "-p", user_prompt,
        "--model", "opus",
        "--output-format", "json",
        "--allowedTools", "Read",
        "--dangerously-skip-permissions",
        "--append-system-prompt", SYSTEM_PROMPT,
        "--add-dir", str(png_path.parent),
    ]
    # Strip ANTHROPIC_API_KEY so OAuth (Max subscription) auth is used.
    env = {**os.environ}
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=PER_PAGE_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if proc.stderr:
        logf.write(f"stderr: {proc.stderr[:300]}\n")
    try:
        outer = json.loads(proc.stdout)
    except Exception:
        logf.write(f"outer JSON parse failed; stdout head: {proc.stdout[:300]}\n")
        return None, "parse_error_outer"
    return outer, "ok"


def audit_page(png_path: pathlib.Path, logf,
               max_retries: int = 4) -> dict:
    """Call claude -p with retry/backoff on rate-limit & transient errors."""
    t0 = time.time()
    backoff_s = 30  # initial; doubles each retry: 30, 60, 120, 240 = ~7.5 min
    last_err_kind = "claude_error"
    last_err_msg = ""
    for attempt in range(max_retries + 1):
        outer, status = _claude_call(png_path, logf)
        dt = time.time() - t0
        if status == "timeout":
            last_err_kind = "timeout"
            last_err_msg = f"timeout after {PER_PAGE_TIMEOUT}s"
            logf.write(f"[{png_path.name}] TIMEOUT attempt {attempt+1}\n")
        elif outer is None:
            last_err_kind = status
            last_err_msg = "no outer json"
            logf.write(f"[{png_path.name}] {status} attempt {attempt+1}\n")
        elif outer.get("is_error"):
            err_text = (outer.get("result") or "")[:300]
            last_err_msg = err_text
            # Classify so we know whether to retry.
            low = err_text.lower()
            if "rate" in low or "limit" in low or "429" in low or "529" in low or "overload" in low:
                last_err_kind = "rate_limited"
            elif "529" in str(outer.get("api_error_status", "")) or \
                 "529" in low:
                last_err_kind = "overloaded"
            else:
                last_err_kind = "claude_error"
            logf.write(f"[{png_path.name}] {last_err_kind} attempt {attempt+1}: "
                       f"{err_text[:200]}\n")
        else:
            result_text = outer.get("result", "")
            inner = _extract_json(result_text)
            if inner is None:
                logf.write(f"[{png_path.name}] inner JSON parse fail; "
                           f"head: {result_text[:300]}\n")
                last_err_kind = "parse_error_inner"
                last_err_msg = result_text[:200]
            else:
                inner["_elapsed_s"] = round(dt, 1)
                inner["_cost_usd"] = round(outer.get("total_cost_usd", 0.0), 4)
                inner["_attempts"] = attempt + 1
                return inner
        # Decide whether to retry.
        if attempt == max_retries:
            break
        if last_err_kind in ("rate_limited", "overloaded", "timeout",
                             "parse_error_outer"):
            wait = backoff_s
            logf.write(f"[{png_path.name}] sleeping {wait}s before retry\n")
            print(f"  ↳ {last_err_kind}, sleeping {wait}s (attempt "
                  f"{attempt+1}/{max_retries})", flush=True)
            time.sleep(wait)
            backoff_s = min(backoff_s * 2, 600)
        else:
            # parse_error_inner / generic claude_error: don't retry, just bail.
            break
    return {"page_ok": None, "issues": [], "overall": last_err_kind,
            "_err": last_err_msg[:200],
            "_elapsed_s": round(time.time() - t0, 1),
            "_cost_usd": 0.0,
            "_attempts": attempt + 1}


_FILL_RE = re.compile(r"filled_router\.(e\d+_[a-z_]+)\.([A-Z0-9.-]+)\.fixed\.json$")


def discover_all_fills() -> list[tuple[str, str, str]]:
    """Enumerate every (case_id, event, form_id) from intermediate/router/."""
    triples: list[tuple[str, str, str]] = []
    for p in sorted((ROOT / "intermediate" / "router").rglob("filled_router.*.fixed.json")):
        m = _FILL_RE.search(p.name)
        if not m:
            continue
        case_id = p.parent.name
        triples.append((case_id, m.group(1), m.group(2)))
    return triples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-tsv", type=pathlib.Path,
                    default=ROOT / "router" / "vision_audit_report.tsv")
    ap.add_argument("--log", type=pathlib.Path,
                    default=ROOT / "router" / "vision_audit.log")
    ap.add_argument("--workdir", type=pathlib.Path,
                    default=ROOT / "intermediate" / "vision_audit")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only run first N entries.")
    ap.add_argument("--form", type=str, default=None,
                    help="Filter to one form_id.")
    ap.add_argument("--all", action="store_true",
                    help="Run every filled_router.*.fixed.json on disk "
                         "(not the static PILOT list).")
    ap.add_argument("--resume", action="store_true",
                    help="Skip (case,event,form) triples already present in "
                         "the existing out-tsv.")
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    if args.all:
        pilot = discover_all_fills()
    else:
        pilot = PILOT
    if args.form:
        pilot = [p for p in pilot if p[2] == args.form]
    if args.resume and args.out_tsv.exists():
        done = set()
        for line in args.out_tsv.read_text().splitlines()[1:]:
            cells = line.split("\t")
            if len(cells) >= 3:
                done.add((cells[0], cells[1], cells[2]))
        pre = len(pilot)
        pilot = [t for t in pilot if t not in done]
        print(f"[resume] {pre - len(pilot)} entries already in TSV; "
              f"{len(pilot)} remain.")
    if args.limit:
        pilot = pilot[: args.limit]

    cols = ["case_id", "event", "form_id", "page", "page_ok", "overall",
            "n_issues", "elapsed_s", "cost_usd", "issues_json"]
    rows: list[dict] = []
    grand_cost = 0.0
    grand_t0 = time.time()

    # Append-mode TSV so a long run survives crashes and supports --resume.
    args.out_tsv.parent.mkdir(parents=True, exist_ok=True)
    tsv_is_new = not args.out_tsv.exists() or args.out_tsv.stat().st_size == 0
    tsv_f = open(args.out_tsv, "a", buffering=1)  # line-buffered
    if tsv_is_new:
        tsv_f.write("\t".join(cols) + "\n")

    n_total_pages = 0
    with open(args.log, "a") as logf:
        logf.write(f"\n\n===== run started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                   f"({len(pilot)} fills) =====\n")
        for idx, (case_id, event, form_id) in enumerate(pilot, 1):
            print(f"\n=== [{idx}/{len(pilot)}] {case_id} / {event} / {form_id} ===",
                  flush=True)
            wd = args.workdir / case_id / event / form_id
            stamped_pdf = wd / "filled.pdf"
            stamped = stamp_form(case_id, event, form_id, stamped_pdf)
            if stamped is None:
                continue
            pngs = render_pages(stamped, wd)
            print(f"  rendered {len(pngs)} page(s) at {DPI} DPI")
            for i, png in enumerate(pngs, 1):
                verdict = audit_page(png, logf)
                n_issues = len(verdict.get("issues", []))
                overall = verdict.get("overall")
                cost = verdict.get("_cost_usd", 0.0)
                grand_cost += cost
                n_total_pages += 1
                print(f"  page {i}: overall={overall} issues={n_issues} "
                      f"cost=${cost:.3f} attempts={verdict.get('_attempts', 1)} "
                      f"({verdict.get('_elapsed_s', 0)}s)", flush=True)
                row = {
                    "case_id": case_id, "event": event, "form_id": form_id,
                    "page": i,
                    "page_ok": verdict.get("page_ok"),
                    "overall": overall,
                    "n_issues": n_issues,
                    "elapsed_s": verdict.get("_elapsed_s", 0),
                    "cost_usd": cost,
                    "issues_json": json.dumps(verdict.get("issues", [])),
                }
                tsv_f.write("\t".join(str(row[c]) for c in cols) + "\n")
                rows.append(row)
    tsv_f.close()
    grand_dt = time.time() - grand_t0
    print(f"\nWrote {args.out_tsv} ({n_total_pages} pages this run, "
          f"{len(rows)} total in memory)")
    print(f"Total cost: ${grand_cost:.2f}, wall time: {grand_dt/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
