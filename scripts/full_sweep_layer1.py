"""Full 104-form Layer 1 sweep.

Re-runs detect -> validate -> name -> write on all forms (overwriting
baseline output/), then runs Opus alignment review into a separate
report dir so we can diff against the original baseline reports.

Designed to survive disconnect:
  setsid nohup python scripts/full_sweep_layer1.py \\
    > /tmp/full_sweep_layer1.log 2>&1 < /dev/null &
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Override config paths BEFORE importing pipeline modules so the sweep
# writes to segregated layer1 dirs (preserving the baseline state for
# diffing) and forces fresh validation rather than reusing 3-month-old
# stale validation JSONs from intermediate/validation/.
import config

LAYER1_INTERMEDIATE = ROOT / "intermediate_layer1"
LAYER1_OUTPUT = ROOT / "output_layer1"
BASELINE_INTERMEDIATE = ROOT / "intermediate"

config.INTERMEDIATE_DIR = LAYER1_INTERMEDIATE
config.ANALYSIS_DIR = LAYER1_INTERMEDIATE / "analysis"
config.DETECTION_DIR = LAYER1_INTERMEDIATE / "detection"
config.VALIDATION_DIR = LAYER1_INTERMEDIATE / "validation"
config.NAMING_DIR = LAYER1_INTERMEDIATE / "naming"
config.OUTPUT_DIR = LAYER1_OUTPUT

BASELINE_REPORTS = ROOT / "reports" / "opus-alignment"
LAYER1_REPORTS = ROOT / "reports" / "opus-alignment-layer1-full"
DELTA_REPORT = ROOT / "reports" / "layer1-full-deltas.md"
for d in (
    config.ANALYSIS_DIR,
    config.DETECTION_DIR,
    config.VALIDATION_DIR,
    config.NAMING_DIR,
    config.OUTPUT_DIR,
    LAYER1_REPORTS,
):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("layer1-full")

from modules.field_detector import detect_all_forms  # noqa: E402
from modules.vlm_validator import validate_all_forms  # noqa: E402
from modules.taxonomy import name_all_forms  # noqa: E402
from modules.acroform_writer import write_all_forms  # noqa: E402


def copy_baseline_analyses() -> None:
    """Copy analysis JSONs from baseline so detect can run without re-rendering."""
    src = BASELINE_INTERMEDIATE / "analysis"
    dst = config.ANALYSIS_DIR
    copied = 0
    for s in src.glob("*.json"):
        d = dst / s.name
        if d.exists():
            continue
        import shutil
        shutil.copy2(s, d)
        copied += 1
    log.info("Copied %d analysis JSONs (skipped %d already present)",
             copied, len(list(src.glob('*.json'))) - copied)


def run_pipeline() -> None:
    log.info("=" * 60)
    log.info("STAGE 3: detect (force=True)")
    log.info("=" * 60)
    detect_all_forms(force=True)

    log.info("=" * 60)
    log.info("STAGE 4: VLM validate")
    log.info("=" * 60)
    validate_all_forms()

    log.info("=" * 60)
    log.info("STAGE 5: name (force=True)")
    log.info("=" * 60)
    name_all_forms(force=True)

    log.info("=" * 60)
    log.info("STAGE 6: write AcroForm (force=True)")
    log.info("=" * 60)
    write_all_forms(force=True)


def run_opus_review() -> None:
    log.info("=" * 60)
    log.info("Opus alignment review (j=4) -> %s", LAYER1_REPORTS)
    log.info("=" * 60)
    env = os.environ.copy()
    env["OPUS_REPORT_DIR"] = str(LAYER1_REPORTS)
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "opus_alignment_review.py"),
        "--root", str(config.OUTPUT_DIR),
        "--rerun",
        "-j", "4",
    ]
    subprocess.run(cmd, env=env, check=False)


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


def write_delta_report() -> None:
    baseline_files = {p.stem: p for p in BASELINE_REPORTS.glob("*.json")}
    layer1_files = {p.stem: p for p in LAYER1_REPORTS.glob("*.json")}
    common = sorted(set(baseline_files) & set(layer1_files))

    lines = [
        "# Layer 1 Full Sweep — Baseline vs Layer 1 (104 forms)",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| Form | Base | L1 | Δ | Nam Δ | Ali Δ | Mis Δ |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    sums = [0] * 8  # bt, lt, bn, ln, ba, la, bm, lm
    rows = []
    for fid in common:
        b = json.loads(baseline_files[fid].read_text())
        l = json.loads(layer1_files[fid].read_text())
        bt, bn, ba, bm = _counts(b)
        lt, ln, la, lm = _counts(l)
        rows.append((fid, bt, lt, bn, ln, ba, la, bm, lm))
        sums[0] += bt; sums[1] += lt; sums[2] += bn; sums[3] += ln
        sums[4] += ba; sums[5] += la; sums[6] += bm; sums[7] += lm
    rows.sort(key=lambda r: r[2] - r[1])  # biggest improvement first
    for fid, bt, lt, bn, ln, ba, la, bm, lm in rows:
        lines.append(
            f"| {fid[:60]} | {bt} | {lt} | {lt - bt:+d} | "
            f"{ln - bn:+d} | {la - ba:+d} | {lm - bm:+d} |"
        )
    bt, lt, bn, ln, ba, la, bm, lm = sums
    lines.append(
        f"| **TOTAL** | **{bt}** | **{lt}** | **{lt - bt:+d}** | "
        f"**{ln - bn:+d}** | **{la - ba:+d}** | **{lm - bm:+d}** |"
    )
    if bt:
        pct_total = 100 * (lt - bt) / bt
        lines.append(f"\nTotal issue change: {pct_total:+.1f}%")
    if bn:
        pct_naming = 100 * (ln - bn) / bn
        lines.append(f"Naming issue change: {pct_naming:+.1f}%")
    DELTA_REPORT.write_text("\n".join(lines) + "\n")
    log.info("Wrote %s (%d forms)", DELTA_REPORT, len(rows))


def main() -> int:
    t0 = time.time()
    copy_baseline_analyses()
    run_pipeline()
    log.info("Pipeline elapsed: %.1f min", (time.time() - t0) / 60)
    run_opus_review()
    write_delta_report()
    log.info("Total elapsed: %.1f min", (time.time() - t0) / 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
