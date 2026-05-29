"""Recursive self-improvement loop across all forms.

For each form:
  1. Audit the current PDF
  2. Apply structured + text-extracted fixes from the audit
  3. Re-audit; if issue count drops, repeat
  4. Stop when converged (<min_issues OR no improvement for 2 iters)
  5. Flag for human review if final count > review_threshold

State is persisted to state/recursive_improvement.json so the loop can resume
after interruption.

Usage:
  scripts/recursive_improvement.py                          # all 79, opus auditor
  scripts/recursive_improvement.py --auditor local
  scripts/recursive_improvement.py --form PP-205            # single form
  scripts/recursive_improvement.py --max-iters 5
  scripts/recursive_improvement.py --resume                 # skip already-done forms
  scripts/recursive_improvement.py --review-threshold 5     # ≤5 issues = done

Outputs per form:
  output_recursive/<cat>/<stem>/iter_N.pdf
  output_recursive/<cat>/<stem>/final.pdf            (best iteration)
  reports/recursive/<stem>/iter_N.json
  reports/recursive/<stem>/final.json
  state/recursive_improvement.json                   (queue state)
  reports/human_review_queue.md                      (forms needing human eyes)
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
import time
from dataclasses import dataclass, field, asdict

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.apply_naming_fixes import extract_rename  # noqa: E402

STATE_FILE = ROOT / "state" / "recursive_improvement.json"
REALIGN_HELPER = ROOT / "scripts" / "realign_crop.py"
REALIGN_VENV_PYTHON = ROOT / ".venv-commonforms" / "bin" / "python3"
REALIGN_DPI = 300         # Patch D — crop DPI for FFDetr (vs page DPI 144)
REALIGN_PADDING = 0.3     # crop is widget rect expanded by this fraction in each dir
REALIGN_MIN_IOU = 0.05    # accept detection if it overlaps the widget by at least this
# PyMuPDF widget enum: 2=CHECKBOX, 5=RADIOBUTTON, 6=SIGNATURE, 7=TEXT.
REALIGN_TYPE_MAP = {
    2: "ChoiceButton",
    5: "ChoiceButton",
    6: "Signature",
    7: "TextBox",
}
OUTPUT_ROOT = ROOT / "output_recursive"
REPORT_ROOT = ROOT / "reports" / "recursive"
HUMAN_REVIEW_DOC = ROOT / "reports" / "human_review_queue.md"

# Safety patches (A+B+C) — see PB-007 trial: iter 2 +12 regression from
# unchecked deletes. Tune via CLI flags below; defaults stay conservative.
HIGH_CONFIDENCE_DELETE_HINTS = re.compile(
    r"\b(spurious|duplicate|extra(?:neous)?|stray|orphan|orphaned|"
    r"unused|leftover|misplaced|redundant|garbage)\b",
    re.IGNORECASE,
)
DEFAULT_MAX_DELETE_PER_ITER = 1
DEFAULT_MAX_ADD_PER_ITER = 2
DEFAULT_REGRESSION_THRESHOLD = 5


@dataclass
class FormState:
    cat: str
    name: str
    stem: str
    iters: list[dict] = field(default_factory=list)
    status: str = "queued"  # queued | running | converged | needs_review | done | rollback
    final_issues: int = -1
    initial_issues: int = -1


def load_state() -> dict[str, FormState]:
    if STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text())
        return {k: FormState(**v) for k, v in raw.items()}
    return {}


def save_state(state: dict[str, FormState]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({k: asdict(v) for k, v in state.items()}, indent=2))


def list_targets() -> list[FormState]:
    """Discover all forms that have a fused PDF + initial audit report."""
    fused_root = ROOT / "output_fused"
    targets = []
    for pdf in sorted(fused_root.rglob("*.pdf")):
        cat = pdf.parent.name
        targets.append(FormState(cat=cat, name=pdf.name, stem=pdf.stem))
    return targets


# ── audit + fix logic ────────────────────────────────────────────────────


def issue_count(report_path: pathlib.Path) -> int:
    if not report_path.exists():
        return -1
    try:
        d = json.loads(report_path.read_text())
    except json.JSONDecodeError:
        return -1
    return sum(len(pg.get("issues", [])) for pg in d.get("pages", []))


def collect_fixes(report_path: pathlib.Path) -> list[dict]:
    """Pull every actionable fix from the report — structured first, fallback to text."""
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
            if fix and isinstance(fix, dict):
                action = fix.get("action")
                if action == "rename" and fix.get("to") and current:
                    out.append({"action": "rename", "page": pno,
                                "current": current, "to": fix["to"]})
                elif action == "delete" and current:
                    # Patch A — only keep deletes with explicit confidence cue.
                    reason = " ".join([str(fix.get("reason") or ""),
                                       str(fix.get("rationale") or ""),
                                       details])
                    if HIGH_CONFIDENCE_DELETE_HINTS.search(reason):
                        out.append({"action": "delete", "page": pno,
                                    "current": current})
                    # else: drop — would-be deletes without a cue are too risky.
                elif action == "move" and current and fix.get("to_rect"):
                    out.append({"action": "move", "page": pno, "current": current,
                                "to_rect": fix["to_rect"]})
                elif action == "add" and fix.get("rect") and fix.get("name"):
                    out.append({"action": "add", "page": pno,
                                "rect": fix["rect"], "name": fix["name"],
                                "type": fix.get("type", "text")})
                continue
            if issue.get("type") == "naming" and current:
                suggested = extract_rename(details)
                if suggested and suggested != current:
                    out.append({"action": "rename", "page": pno,
                                "current": current, "to": suggested})
            elif issue.get("type") == "alignment" and current:
                # Patch D — auditors emit 0% to_rect; route alignment issues
                # through a CommonForms crop realigner at apply time.
                out.append({"action": "realign", "page": pno,
                            "current": current})
    return out


def _realign_widget_rect(pdf_path: pathlib.Path, page_no: int,
                         widget_name: str,
                         widget_rect: list[float] | None = None,
                         widget_type: int | None = None) -> tuple[list[float], str] | tuple[None, None]:
    """Realign backend. Try Patch E (geometric line-anchored snap) first, then
    fall back to Patch D (FFDetr crop). Returns (new_rect, source) or (None, None).
    """
    # Patch E — geometric snap to vector lines or text-underscore runs.
    try:
        from scripts.geometric_snap import snap_widget_rect as _snap
        snapped = _snap(pdf_path, page_no, widget_name,
                        widget_rect=widget_rect, widget_type=widget_type)
        if snapped is not None:
            return snapped, "patch_e"
    except Exception:
        pass

    # Patch D fallback — FFDetr crop.
    new_rect = _realign_widget_rect_ffdetr(pdf_path, page_no, widget_name)
    if new_rect is not None:
        return new_rect, "patch_d"
    return None, None


def _realign_widget_rect_ffdetr(pdf_path: pathlib.Path, page_no: int,
                                widget_name: str) -> list[float] | None:
    """Patch D — render a crop around the named widget, run FFDetr, return a
    new rect in PDF points. Returns None if no plausible detection found.
    """
    if not REALIGN_VENV_PYTHON.exists() or not REALIGN_HELPER.exists():
        return None
    import tempfile
    d = fitz.open(pdf_path)
    try:
        if page_no >= d.page_count:
            return None
        page = d[page_no]
        target = next((w for w in (page.widgets() or [])
                       if w.field_name == widget_name), None)
        if target is None:
            return None
        wt = REALIGN_TYPE_MAP.get(target.field_type)
        rect = target.rect
        ph = page.rect.height
        pw = page.rect.width
        # Pad crop in PDF points; clamp to page.
        pad_x = max(rect.width, 24) * REALIGN_PADDING
        pad_y = max(rect.height, 12) * REALIGN_PADDING
        cx0 = max(0.0, rect.x0 - pad_x)
        cy0 = max(0.0, rect.y0 - pad_y)
        cx1 = min(pw, rect.x1 + pad_x)
        cy1 = min(ph, rect.y1 + pad_y)
        crop_rect = fitz.Rect(cx0, cy0, cx1, cy1)
        # Render crop at REALIGN_DPI.
        mat = fitz.Matrix(REALIGN_DPI / 72, REALIGN_DPI / 72)
        pix = page.get_pixmap(matrix=mat, clip=crop_rect, alpha=False)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(pix.tobytes("png"))
            crop_png = pathlib.Path(tmp.name)
    finally:
        d.close()

    try:
        res = subprocess.run(
            [str(REALIGN_VENV_PYTHON), str(REALIGN_HELPER), str(crop_png)],
            capture_output=True, text=True, timeout=120,
        )
        if res.returncode != 0:
            return None
        # Helper logs to stderr; JSON is the LAST stdout line.
        stdout_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        if not stdout_lines:
            return None
        dets = json.loads(stdout_lines[-1])
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    finally:
        try: crop_png.unlink()
        except OSError: pass

    if not dets:
        return None

    # Translate each detection from crop image px → PDF points (within crop).
    # crop_rect is in PDF points; pix is REALIGN_DPI/72 scale of that.
    scale = REALIGN_DPI / 72.0
    crop_w_pts = crop_rect.width
    crop_h_pts = crop_rect.height
    # Detection x0/y0/x1/y1 are in image px of the crop PNG.
    candidates = []
    for det in dets:
        if wt and det.get("type") != wt:
            continue  # require type match when we know the existing type
        dx0 = det["x0"] / scale + crop_rect.x0
        dy0 = det["y0"] / scale + crop_rect.y0
        dx1 = det["x1"] / scale + crop_rect.x0
        dy1 = det["y1"] / scale + crop_rect.y0
        # IoU vs original widget rect.
        ix0 = max(rect.x0, dx0); iy0 = max(rect.y0, dy0)
        ix1 = min(rect.x1, dx1); iy1 = min(rect.y1, dy1)
        inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        union = (rect.width * rect.height
                 + (dx1 - dx0) * (dy1 - dy0) - inter)
        iou = inter / union if union > 0 else 0.0
        candidates.append((iou, dx0, dy0, dx1, dy1))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best = candidates[0]
    if best[0] < REALIGN_MIN_IOU:
        return None
    return [best[1], best[2], best[3], best[4]]


def enforce_destructive_caps(fixes: list[dict],
                             max_delete: int,
                             max_add: int) -> tuple[list[dict], dict]:
    """Patch B — limit per-iteration destructive ops. Renames/moves pass through.
    Returns (kept_fixes, dropped_counts)."""
    kept: list[dict] = []
    dropped = {"delete": 0, "add": 0}
    deletes_used = 0
    adds_used = 0
    for fx in fixes:
        if fx["action"] == "delete":
            if deletes_used < max_delete:
                kept.append(fx); deletes_used += 1
            else:
                dropped["delete"] += 1
        elif fx["action"] == "add":
            if adds_used < max_add:
                kept.append(fx); adds_used += 1
            else:
                dropped["add"] += 1
        else:
            kept.append(fx)
    return kept, dropped


def apply_fixes(pdf_path: pathlib.Path, fixes: list[dict],
                out_path: pathlib.Path) -> dict:
    d = fitz.open(pdf_path)
    counts = {"rename": 0, "delete": 0, "move": 0, "add": 0,
              "realign": 0, "realign_e": 0, "realign_d": 0, "realign_skipped": 0,
              "skipped_collision": 0, "skipped_not_found": 0}
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
                target = next((w for w in widgets if w.field_name == fx["current"]), None)
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
                target = next((w for w in widgets if w.field_name == fx["current"]), None)
                if target is None:
                    counts["skipped_not_found"] += 1
                    continue
                page.delete_widget(target)
                existing.discard(fx["current"])
                widgets = list(page.widgets() or [])
                counts["delete"] += 1
            elif action == "move":
                target = next((w for w in widgets if w.field_name == fx["current"]), None)
                if target is None:
                    counts["skipped_not_found"] += 1
                    continue
                r = fx["to_rect"]
                target.rect = fitz.Rect(r[0], r[1], r[2], r[3])
                target.update()
                counts["move"] += 1
            elif action == "realign":
                # Patch E (geometric) → Patch D (FFDetr) cascade.
                target = next((w for w in widgets if w.field_name == fx["current"]), None)
                if target is None:
                    counts["skipped_not_found"] += 1
                    continue
                new_rect, source = _realign_widget_rect(
                    pdf_path, pno, fx["current"],
                    widget_rect=list(target.rect), widget_type=target.field_type,
                )
                if new_rect is None:
                    counts["realign_skipped"] += 1
                    continue
                target.rect = fitz.Rect(*new_rect)
                target.update()
                counts["realign"] += 1
                if source == "patch_e":
                    counts["realign_e"] += 1
                elif source == "patch_d":
                    counts["realign_d"] += 1
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


def run_audit(pdf_path: pathlib.Path, report_dir: pathlib.Path,
              auditor: str, base_url: str | None = None,
              model: str | None = None,
              force_stem: str | None = None) -> int:
    """Audit a single PDF. Stages it into a temp dir so --root scans only this file.

    If force_stem is provided, the staged file uses that stem (so the audit
    report is named consistently across iterations even if iter PDFs have
    iter-N suffixes).
    """
    import tempfile
    report_dir.mkdir(parents=True, exist_ok=True)
    if auditor == "opus":
        script = "scripts/opus_alignment_review.py"
        env_var = "OPUS_REPORT_DIR"
    else:
        # Allow opting into the v2 checklist harness via env var.
        if os.environ.get("AUDIT_HARNESS_VERSION") == "v2":
            script = "scripts/local_alignment_review_v2.py"
        else:
            script = "scripts/local_alignment_review.py"
        env_var = "AUDIT_REPORT_DIR"
    env = {**os.environ, env_var: str(report_dir)}
    if base_url:
        env["AUDIT_BASE_URL"] = base_url
    if model:
        env["AUDIT_MODEL"] = model
    with tempfile.TemporaryDirectory() as td:
        staged_name = (force_stem + ".pdf") if force_stem else pdf_path.name
        staged = pathlib.Path(td) / staged_name
        staged.symlink_to(pdf_path.resolve())
        cmd = [
            ".venv/bin/python3", script,
            "--root", str(td),
            "--rerun",
        ]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
        return res.returncode


def find_report(report_dir: pathlib.Path, stem: str) -> pathlib.Path | None:
    """Find report file matching stem (with or without _fused suffix etc)."""
    base = stem.replace("_fused", "")
    for p in report_dir.rglob("*.json"):
        if base in p.stem:
            return p
    return None


# ── per-form orchestrator ────────────────────────────────────────────────


def improve_form(form: FormState, args, state: dict) -> None:
    cat, name, stem = form.cat, form.name, form.stem
    print(f"\n=== {stem} ===")

    # iter 0 = baseline (use existing audit if available, else fresh audit)
    initial_pdf = ROOT / "output_fused" / cat / name
    initial_report_dir = ROOT / args.start_report_dir
    initial_report = find_report(initial_report_dir, stem)
    if initial_report is None or args.fresh_baseline:
        print("  iter 0: running baseline audit")
        report_dir_0 = REPORT_ROOT / stem
        run_audit(initial_pdf, report_dir_0, args.auditor,
                  args.base_url, args.model, force_stem=stem)
        initial_report = find_report(report_dir_0, stem)
        if initial_report is None:
            print("  baseline audit failed")
            form.status = "rollback"
            save_state(state)
            return

    initial_count = issue_count(initial_report)
    if initial_count < 0:
        print("  could not parse baseline audit")
        form.status = "rollback"
        save_state(state)
        return

    form.initial_issues = initial_count
    form.iters.append({"iter": 0, "issues": initial_count, "fixes_applied": 0})
    print(f"  iter 0 (baseline): {initial_count} issues")

    if initial_count <= args.review_threshold:
        print(f"  already <= threshold ({args.review_threshold}); marking converged.")
        form.status = "converged"
        form.final_issues = initial_count
        save_state(state)
        return

    current_pdf = initial_pdf
    current_report = initial_report
    best_count = initial_count
    no_improve_streak = 0

    best_pdf = current_pdf  # Patch C — track best PDF for rollback.

    for it in range(1, args.max_iters + 1):
        fixes = collect_fixes(current_report)
        if not fixes:
            print(f"  iter {it}: no actionable fixes; stopping")
            break

        # Patch B — cap destructive ops per iteration.
        fixes, dropped = enforce_destructive_caps(
            fixes, args.max_delete_per_iter, args.max_add_per_iter)
        if dropped["delete"] or dropped["add"]:
            print(f"  iter {it}: capped destructive ops — "
                  f"dropped {dropped['delete']} deletes, {dropped['add']} adds")

        out_pdf = OUTPUT_ROOT / cat / stem / f"iter_{it}.pdf"
        counts = apply_fixes(current_pdf, fixes, out_pdf)
        n_applied = sum(counts[k] for k in
                        ("rename", "delete", "move", "add", "realign"))
        print(f"  iter {it}: applied {n_applied} fixes "
              f"(rename={counts['rename']} delete={counts['delete']} "
              f"move={counts['move']} add={counts['add']} "
              f"realign={counts['realign']}/{counts['realign'] + counts['realign_skipped']}"
              f" [E={counts['realign_e']} D={counts['realign_d']}])")

        if n_applied == 0:
            print(f"  iter {it}: nothing applied; stopping")
            break

        report_dir = REPORT_ROOT / stem / f"iter_{it}"
        run_audit(out_pdf, report_dir, args.auditor,
                  args.base_url, args.model, force_stem=stem)
        new_report = find_report(report_dir, stem)
        if new_report is None:
            print(f"  iter {it}: audit failed; stopping")
            break

        new_count = issue_count(new_report)
        delta = new_count - best_count
        form.iters.append({"iter": it, "issues": new_count,
                          "fixes_applied": n_applied,
                          "deltas": {"issues": delta}})
        print(f"  iter {it}: {best_count} → {new_count} ({delta:+d})")

        # Patch C — auto-rollback on big regression.
        if new_count > best_count + args.regression_threshold:
            print(f"  iter {it}: regression of +{new_count - best_count} > "
                  f"{args.regression_threshold}; rolling back to best "
                  f"({best_count} issues)")
            form.status = "rollback"
            form.final_issues = best_count
            # Promote the best PDF/report (not this iter's) as final.
            (OUTPUT_ROOT / cat / stem).mkdir(parents=True, exist_ok=True)
            (REPORT_ROOT / stem).mkdir(parents=True, exist_ok=True)
            if best_pdf != initial_pdf:
                shutil.copy(best_pdf, OUTPUT_ROOT / cat / stem / "final.pdf")
            shutil.copy(current_report, REPORT_ROOT / stem / "final.json")
            save_state(state)
            return

        if new_count <= args.review_threshold:
            print(f"  iter {it}: ≤ threshold — converged")
            form.status = "converged"
            form.final_issues = new_count
            shutil.copy(out_pdf, OUTPUT_ROOT / cat / stem / "final.pdf")
            shutil.copy(new_report, REPORT_ROOT / stem / "final.json")
            save_state(state)
            return

        if new_count < best_count - 1:  # genuine improvement (not noise)
            best_count = new_count
            best_pdf = out_pdf
            current_pdf = out_pdf
            current_report = new_report
            no_improve_streak = 0
        else:
            no_improve_streak += 1
            if no_improve_streak >= 2:
                print(f"  iter {it}: no improvement for 2 iters; stopping")
                break

    # Did not converge — flag for review
    form.final_issues = best_count
    form.status = "needs_review" if best_count > args.review_threshold else "converged"
    if (OUTPUT_ROOT / cat / stem / f"iter_{len(form.iters)-1}.pdf").exists():
        shutil.copy(OUTPUT_ROOT / cat / stem / f"iter_{len(form.iters)-1}.pdf",
                    OUTPUT_ROOT / cat / stem / "final.pdf")
    if current_report is not None and current_report.exists():
        (REPORT_ROOT / stem).mkdir(parents=True, exist_ok=True)
        shutil.copy(current_report, REPORT_ROOT / stem / "final.json")
    print(f"  result: {form.status}, final issues = {best_count}")
    save_state(state)


# ── human-review report ──────────────────────────────────────────────────


def write_human_review_doc(state: dict[str, FormState]) -> None:
    HUMAN_REVIEW_DOC.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for stem, form in sorted(state.items(), key=lambda kv: -kv[1].final_issues):
        if form.status not in ("needs_review", "rollback"):
            continue
        rows.append({
            "form": stem,
            "status": form.status,
            "initial": form.initial_issues,
            "final": form.final_issues,
            "iters": len(form.iters),
        })
    md = ["# Forms needing human review\n",
          "Forms below either didn't converge below the issue threshold or",
          "rolled back due to errors. Each entry links to its final PDF + report.\n"]
    if not rows:
        md.append("(none — all forms converged)")
    else:
        md.append(f"| form | status | initial | final | iters |")
        md.append("|---|---|---:|---:|---:|")
        for r in rows:
            md.append(f"| {r['form']} | {r['status']} | "
                      f"{r['initial']} | {r['final']} | {r['iters']} |")
    HUMAN_REVIEW_DOC.write_text("\n".join(md))


# ── driver ───────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", default=None, help="filter to single form substring")
    ap.add_argument("--max-iters", type=int, default=4)
    ap.add_argument("--review-threshold", type=int, default=5,
                    help="issue count <= this → converged; > this → needs_review")
    ap.add_argument("--auditor", choices=["opus", "local"], default="opus")
    ap.add_argument("--base-url", default=None,
                    help="local model base URL (only with --auditor local)")
    ap.add_argument("--model", default=None,
                    help="local model name (only with --auditor local)")
    ap.add_argument("--start-report-dir", default="reports/opus-alignment-fused-full",
                    help="directory of baseline audit reports")
    ap.add_argument("--resume", action="store_true",
                    help="skip forms already converged/done")
    ap.add_argument("--fresh-baseline", action="store_true",
                    help="re-audit the baseline even if an existing report exists")
    ap.add_argument("--max-delete-per-iter", type=int,
                    default=DEFAULT_MAX_DELETE_PER_ITER,
                    help="Patch B — cap on delete ops per iteration")
    ap.add_argument("--max-add-per-iter", type=int,
                    default=DEFAULT_MAX_ADD_PER_ITER,
                    help="Patch B — cap on add ops per iteration")
    ap.add_argument("--regression-threshold", type=int,
                    default=DEFAULT_REGRESSION_THRESHOLD,
                    help="Patch C — issue count delta that triggers auto-rollback")
    args = ap.parse_args()

    state = load_state()
    targets = list_targets()

    queue = []
    for t in targets:
        if args.form and args.form not in t.name:
            continue
        if t.stem in state:
            existing = state[t.stem]
            if args.resume and existing.status in ("converged", "done"):
                continue
            queue.append(existing)
        else:
            state[t.stem] = t
            queue.append(t)

    print(f"Queue: {len(queue)} forms")
    for form in queue:
        form.status = "running"
        save_state(state)
        try:
            improve_form(form, args, state)
        except Exception as e:
            print(f"  ERROR on {form.stem}: {type(e).__name__}: {e}")
            form.status = "rollback"
            save_state(state)
        write_human_review_doc(state)

    # Final summary
    by_status = {}
    for form in state.values():
        by_status[form.status] = by_status.get(form.status, 0) + 1
    print(f"\n=== Final state ===")
    for s, n in sorted(by_status.items()):
        print(f"  {s}: {n}")
    print(f"\nHuman review queue: {HUMAN_REVIEW_DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
