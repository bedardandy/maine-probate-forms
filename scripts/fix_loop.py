"""Fix loop: apply structured fixes from audit report → re-audit → repeat.

Reads issues with a `fix` field from an audit report and applies the high-
confidence ones (rename, delete, move) to the corresponding PDF. Re-runs the
audit. Stops when the issue count stops dropping or after max-iters.

Usage:
  scripts/fix_loop.py --form PP-205                    # one form, default settings
  scripts/fix_loop.py --form PP-205 --max-iters 3
  scripts/fix_loop.py --form PP-205 --auditor opus     # default
  scripts/fix_loop.py --form PP-205 --auditor local    # use local model

Each iteration writes:
  output_fix_loop/iter_N/<cat>/<form>.pdf      - PDF after applying fixes
  reports/fix-loop-iter-N/<form>.json          - audit report on that PDF
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Reuse text patterns from apply_naming_fixes for fallback
sys.path.insert(0, str(ROOT))
from scripts.apply_naming_fixes import extract_rename  # noqa: E402


def issue_count(report_path: pathlib.Path) -> int:
    if not report_path.exists():
        return -1
    d = json.loads(report_path.read_text())
    return sum(len(pg.get("issues", [])) for pg in d.get("pages", []))


def collect_fixes(report_path: pathlib.Path) -> list[dict]:
    """Pull every actionable structured fix from the report.

    Each returned dict: {action, page, current_name, ...action-specific...}
    Falls back to text-extracted rename when `fix` field is absent (legacy reports).
    """
    out = []
    if not report_path.exists():
        return out
    d = json.loads(report_path.read_text())
    for pg in d.get("pages", []):
        pno = pg.get("page_number", 0)
        for issue in pg.get("issues", []):
            current = issue.get("field_name", "").strip()
            details = issue.get("details", "")
            fix = issue.get("fix")
            # Structured fix takes precedence
            if fix and isinstance(fix, dict):
                action = fix.get("action")
                if action == "rename" and fix.get("to") and current:
                    out.append({"action": "rename", "page": pno,
                                "current": current, "to": fix["to"]})
                elif action == "delete" and current:
                    out.append({"action": "delete", "page": pno, "current": current})
                elif action == "move" and current and fix.get("to_rect"):
                    out.append({"action": "move", "page": pno, "current": current,
                                "to_rect": fix["to_rect"]})
                elif action == "add" and fix.get("rect") and fix.get("name"):
                    out.append({"action": "add", "page": pno,
                                "rect": fix["rect"], "name": fix["name"],
                                "type": fix.get("type", "text")})
                continue
            # Fallback: extract rename from natural-language details
            if issue.get("type") == "naming" and current:
                suggested = extract_rename(details)
                if suggested and suggested != current:
                    out.append({"action": "rename", "page": pno,
                                "current": current, "to": suggested})
    return out


def apply_fixes(pdf_path: pathlib.Path, fixes: list[dict],
                out_path: pathlib.Path) -> dict:
    """Apply fixes to a PDF; write to out_path. Returns counts."""
    d = fitz.open(pdf_path)
    counts = {"rename": 0, "delete": 0, "move": 0, "add": 0,
              "skipped_collision": 0, "skipped_not_found": 0}
    # Collision-detection set
    existing = set()
    for page in d:
        for w in page.widgets() or []:
            existing.add(w.field_name)

    by_page: dict[int, list[dict]] = {}
    for fx in fixes:
        by_page.setdefault(fx["page"], []).append(fx)

    for pno, page_fixes in by_page.items():
        if pno >= d.page_count:
            counts["skipped_not_found"] += len(page_fixes)
            continue
        page = d[pno]
        widgets = list(page.widgets() or [])

        for fx in page_fixes:
            action = fx["action"]
            if action == "rename":
                target = next((w for w in widgets
                               if w.field_name == fx["current"]), None)
                if target is None:
                    counts["skipped_not_found"] += 1
                    continue
                new_name = fx["to"]
                if new_name in existing and new_name != fx["current"]:
                    counts["skipped_collision"] += 1
                    continue
                target.field_name = new_name
                target.update()
                existing.discard(fx["current"])
                existing.add(new_name)
                counts["rename"] += 1
            elif action == "delete":
                target = next((w for w in widgets
                               if w.field_name == fx["current"]), None)
                if target is None:
                    counts["skipped_not_found"] += 1
                    continue
                # PyMuPDF deletes via page.delete_widget
                page.delete_widget(target)
                existing.discard(fx["current"])
                widgets = list(page.widgets() or [])  # refresh
                counts["delete"] += 1
            elif action == "move":
                target = next((w for w in widgets
                               if w.field_name == fx["current"]), None)
                if target is None:
                    counts["skipped_not_found"] += 1
                    continue
                r = fx["to_rect"]
                target.rect = fitz.Rect(r[0], r[1], r[2], r[3])
                target.update()
                counts["move"] += 1
            elif action == "add":
                wd = fitz.Widget()
                t = fx.get("type", "text")
                if t == "check":
                    wd.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
                elif t == "sig":
                    wd.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE
                else:
                    wd.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                wd.field_name = fx["name"]
                r = fx["rect"]
                wd.rect = fitz.Rect(r[0], r[1], r[2], r[3])
                wd.border_color = (0.5, 0.5, 0.5)
                wd.border_width = 0.5
                wd.text_fontsize = 10
                page.add_widget(wd)
                existing.add(fx["name"])
                counts["add"] += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    d.save(out_path, deflate=True)
    d.close()
    return counts


def run_audit(pdf_root: pathlib.Path, form: str, report_dir: pathlib.Path,
              auditor: str) -> int:
    """Invoke the audit script for one form. Returns exit code."""
    report_dir.mkdir(parents=True, exist_ok=True)
    if auditor == "opus":
        script = "scripts/opus_alignment_review.py"
        env_var = "OPUS_REPORT_DIR"
    else:
        script = "scripts/local_alignment_review.py"
        env_var = "AUDIT_REPORT_DIR"
    env = {**os.environ, env_var: str(report_dir)}
    cmd = [
        ".venv/bin/python3", script,
        "--root", str(pdf_root),
        "--form", form,
        "--rerun",
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
    if res.returncode != 0:
        print(f"  audit failed: {res.stderr.strip()[-200:]}")
    return res.returncode


def find_pdf(pdf_root: pathlib.Path, form: str) -> pathlib.Path | None:
    for p in pdf_root.rglob("*.pdf"):
        if form in p.name:
            return p
    return None


def find_report(report_dir: pathlib.Path, form: str) -> pathlib.Path | None:
    for p in report_dir.rglob("*.json"):
        if form in p.name and p.name != "SUMMARY.md":
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True, help="form id substring")
    ap.add_argument("--start-pdf-root", default="output_fused",
                    help="initial PDF directory")
    ap.add_argument("--start-report-dir", default="reports/opus-alignment-fused-full",
                    help="initial audit report directory (used as iteration 0)")
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--auditor", choices=["opus", "local"], default="opus")
    args = ap.parse_args()

    start_pdf_root = ROOT / args.start_pdf_root
    pdf = find_pdf(start_pdf_root, args.form)
    if pdf is None:
        print(f"No PDF matching '{args.form}' under {start_pdf_root}")
        return 1

    rel = pdf.relative_to(start_pdf_root)
    initial_report = find_report(ROOT / args.start_report_dir, args.form)
    if initial_report is None:
        print(f"No initial audit report; running iteration 0 audit")
        report_dir_0 = ROOT / "reports" / "fix-loop-iter-0"
        run_audit(start_pdf_root, args.form, report_dir_0, args.auditor)
        initial_report = find_report(report_dir_0, args.form)
        if initial_report is None:
            print("Initial audit produced no report. Aborting.")
            return 1

    initial_count = issue_count(initial_report)
    print(f"\n=== Fix-loop on '{args.form}' ===")
    print(f"Initial issue count: {initial_count}")

    current_pdf = pdf
    current_report = initial_report

    history = [(0, initial_count, "baseline")]
    for it in range(1, args.max_iters + 1):
        fixes = collect_fixes(current_report)
        if not fixes:
            print(f"\niter {it}: no fixes available, stopping.")
            break
        out_pdf = ROOT / "output_fix_loop" / f"iter_{it}" / rel
        counts = apply_fixes(current_pdf, fixes, out_pdf)
        print(f"\niter {it}: applied — "
              f"rename={counts['rename']}  delete={counts['delete']}  "
              f"move={counts['move']}  add={counts['add']}  "
              f"(skipped: {counts['skipped_collision']} collisions, "
              f"{counts['skipped_not_found']} not-found)")

        # Re-audit
        report_dir = ROOT / "reports" / f"fix-loop-iter-{it}"
        run_audit(out_pdf.parent.parent, args.form, report_dir, args.auditor)
        new_report = find_report(report_dir, args.form)
        if new_report is None:
            print(f"  iter {it}: re-audit produced no report. Stopping.")
            break
        new_count = issue_count(new_report)
        delta = new_count - history[-1][1]
        print(f"  iter {it}: issue count {history[-1][1]} → {new_count} ({delta:+d})")
        history.append((it, new_count, f"applied {sum(counts[k] for k in ['rename','delete','move','add'])} fixes"))

        # Convergence check: stop if no improvement
        if new_count >= history[-2][1] - 1:
            print(f"  iter {it}: no significant improvement; stopping.")
            break
        current_pdf = out_pdf
        current_report = new_report

    print("\n=== Summary ===")
    for it, n, note in history:
        print(f"  iter {it}: {n} issues  ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
