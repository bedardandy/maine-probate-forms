"""Layer-1 end-to-end test.

Runs the section-aware naming changes through the full pipeline on a
curated set of forms, writes new fillable PDFs to output_layer1/, then
runs Opus alignment review and compares baseline-vs-Layer1 issue counts.

Designed to survive disconnect: launch via
  setsid nohup python scripts/test_layer1_pipeline.py > /tmp/layer1_test.log 2>&1 < /dev/null &
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── Override config paths BEFORE importing pipeline modules ───────────────
import config

LAYER1_INTERMEDIATE = ROOT / "intermediate_layer1"
LAYER1_OUTPUT = ROOT / "output_layer1"
LAYER1_REPORTS = ROOT / "reports" / "opus-alignment-layer1"
DELTA_REPORT = ROOT / "reports" / "layer1-deltas.md"
BASELINE_REPORTS = ROOT / "reports" / "opus-alignment"
BASELINE_INTERMEDIATE = ROOT / "intermediate"

config.INTERMEDIATE_DIR = LAYER1_INTERMEDIATE
config.ANALYSIS_DIR = LAYER1_INTERMEDIATE / "analysis"
config.DETECTION_DIR = LAYER1_INTERMEDIATE / "detection"
config.VALIDATION_DIR = LAYER1_INTERMEDIATE / "validation"
config.NAMING_DIR = LAYER1_INTERMEDIATE / "naming"
config.OUTPUT_DIR = LAYER1_OUTPUT

for d in (
    config.ANALYSIS_DIR,
    config.DETECTION_DIR,
    config.VALIDATION_DIR,
    config.NAMING_DIR,
    config.OUTPUT_DIR,
    LAYER1_REPORTS,
):
    d.mkdir(parents=True, exist_ok=True)

# ── Now safe to import modules that read config.X lazily ──────────────────
from modules.field_detector import detect_all_forms  # noqa: E402
from modules.vlm_validator import validate_all_forms  # noqa: E402
from modules.taxonomy import name_all_forms  # noqa: E402
from modules.acroform_writer import write_all_forms  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("layer1")

# Form IDs picked across the issue patterns: GS-001 (high naming-defect
# count from sample), AD-001 (adoption category), DE-201 (estate, complex),
# PP-205 (joined petition w/ many sections), N-115 (Kimi reference form).
TARGET_FORM_IDS = [
    "GS-001",
    "AD-001",
    "DE-201(I)",
    "PP-205",
    "N-115",
]


def _resolve_form_ids() -> list[str]:
    """Match each target prefix to the actual analysis filename stem."""
    available = {p.stem for p in (BASELINE_INTERMEDIATE / "analysis").glob("*.json")}
    resolved = []
    for prefix in TARGET_FORM_IDS:
        # Prefer exact match, otherwise first prefix match.
        if prefix in available:
            resolved.append(prefix)
            continue
        matches = sorted(s for s in available if s.startswith(prefix))
        if matches:
            resolved.append(matches[0])
            log.info("Resolved %s -> %s", prefix, matches[0])
        else:
            log.warning("No analysis JSON for prefix %s", prefix)
    return resolved


def copy_baseline_analyses(form_ids: list[str]) -> None:
    src = BASELINE_INTERMEDIATE / "analysis"
    dst = config.ANALYSIS_DIR
    for fid in form_ids:
        s = src / f"{fid}.json"
        d = dst / f"{fid}.json"
        if not s.exists():
            log.warning("Missing baseline analysis: %s", s)
            continue
        shutil.copy2(s, d)
        log.info("Copied analysis %s", fid)


def run_pipeline(form_ids: list[str]) -> None:
    log.info("=" * 60)
    log.info("STAGE 3: detect (force=True) — %d forms", len(form_ids))
    log.info("=" * 60)
    detect_all_forms(form_ids=form_ids, force=True)

    log.info("=" * 60)
    log.info("STAGE 4: VLM validate — %d forms", len(form_ids))
    log.info("=" * 60)
    validate_all_forms(form_ids=form_ids)

    log.info("=" * 60)
    log.info("STAGE 5: name (force=True) — %d forms", len(form_ids))
    log.info("=" * 60)
    name_all_forms(form_ids=form_ids, force=True)

    log.info("=" * 60)
    log.info("STAGE 6: write AcroForm (force=True) — %d forms", len(form_ids))
    log.info("=" * 60)
    write_all_forms(form_ids=form_ids, force=True)


def run_opus_review(form_ids: list[str]) -> None:
    log.info("=" * 60)
    log.info("Opus alignment review on output_layer1/")
    log.info("=" * 60)
    env = os.environ.copy()
    env["OPUS_REPORT_DIR"] = str(LAYER1_REPORTS)
    env.pop("ANTHROPIC_API_KEY", None)  # use OAuth/Max subscription
    for fid in form_ids:
        log.info("  reviewing %s", fid)
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "opus_alignment_review.py"),
            "--root", str(LAYER1_OUTPUT),
            "--form", fid,
            "--rerun",
            "-j", "1",
        ]
        try:
            subprocess.run(cmd, env=env, check=True, timeout=900)
        except subprocess.TimeoutExpired:
            log.error("Opus review timed out for %s", fid)
        except subprocess.CalledProcessError as e:
            log.error("Opus review failed for %s: %s", fid, e)


def _load_issues(report_dir: Path, fid: str) -> dict | None:
    matches = sorted(report_dir.glob(f"{fid}*.json"))
    if not matches:
        return None
    return json.loads(matches[0].read_text())


def write_delta_report(form_ids: list[str]) -> None:
    lines = [
        "# Layer 1 Pipeline Test — Baseline vs Layer 1",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Layer 1 changes:",
        "- field_detector now plumbs section_header into DetectedField",
        "- vlm_validator prompt requires section-prefixed naming",
        "- vlm_validator fallback rejects body-text-shaped nearby_label",
        "",
        "| Form | Baseline issues | Layer 1 issues | Δ | Naming Δ | Alignment Δ | Missing Δ |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    def _counts(d: dict | None) -> tuple[int, int, int, int]:
        if not d:
            return (0, 0, 0, 0)
        total = d.get("total_issues", 0)
        types = {"naming": 0, "alignment": 0, "missing": 0}
        for page in d.get("pages", []):
            for issue in page.get("issues", []):
                t = issue.get("type", "")
                if t in types:
                    types[t] += 1
        return (total, types["naming"], types["alignment"], types["missing"])

    totals_b = totals_l = nam_b = nam_l = ali_b = ali_l = mis_b = mis_l = 0
    for fid in form_ids:
        b = _load_issues(BASELINE_REPORTS, fid)
        l = _load_issues(LAYER1_REPORTS, fid)
        bt, bn, ba, bm = _counts(b)
        lt, ln, la, lm = _counts(l)
        delta = lt - bt
        nd = ln - bn
        ad = la - ba
        md = lm - bm
        lines.append(
            f"| {fid} | {bt} | {lt} | {delta:+d} | {nd:+d} | {ad:+d} | {md:+d} |"
        )
        totals_b += bt
        totals_l += lt
        nam_b += bn
        nam_l += ln
        ali_b += ba
        ali_l += la
        mis_b += bm
        mis_l += lm
    lines.append(
        f"| **TOTAL** | **{totals_b}** | **{totals_l}** | "
        f"**{totals_l - totals_b:+d}** | **{nam_l - nam_b:+d}** | "
        f"**{ali_l - ali_b:+d}** | **{mis_l - mis_b:+d}** |"
    )
    DELTA_REPORT.write_text("\n".join(lines) + "\n")
    log.info("Wrote %s", DELTA_REPORT)


def main() -> int:
    t0 = time.time()
    form_ids = _resolve_form_ids()
    if not form_ids:
        log.error("No form IDs resolved; aborting")
        return 1
    log.info("Layer 1 test on: %s", form_ids)

    copy_baseline_analyses(form_ids)
    run_pipeline(form_ids)
    run_opus_review(form_ids)
    write_delta_report(form_ids)

    log.info("Done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
