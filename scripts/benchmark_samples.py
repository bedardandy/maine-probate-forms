"""Benchmark validator vs ground-truth AcroForm widgets in samples/.

Each sample PDF in `samples/` is a hand-completed version of a flat source
PDF in `forms/`, containing the canonical fillable widgets we want our
pipeline to recover. We compare:

  1. Heuristic-only (intermediate/detection/<form_id>.json)
  2. Validator-gated   (intermediate/validation/<form_id>.json, re-run)

against the ground-truth widget set, using IoU >= IOU_MATCH for a match.

Usage:
  python -m scripts.benchmark_samples            # use existing validation/
  python -m scripts.benchmark_samples --rerun    # delete + re-run validator
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from modules.field_detector import load_detection
from modules.vlm_validator import load_validation, validate_form

IOU_MATCH = 0.30  # loose: any meaningful overlap
IOU_STRICT = 0.50  # strict: clear positional alignment

# (sample_basename, source_form_path, form_id)
SAMPLES: list[tuple[str, str, str]] = [
    (
        "DE-101(I) Application for Informal - Intestate.pdf",
        "forms/estates/DE-101(I) Application for Informal - Intestate (Rev. 09-12-19).pdf",
        "DE-101(I)",
    ),
    (
        "DE-104 PR Acceptance.pdf",
        "forms/estates/DE-104 PR Acceptance (Rev. 07-01-19).pdf",
        "DE-104",
    ),
    (
        "DE-201(I) Application for Informal Probate of Will or Appointment (Rev. 09-12-19).pdf",
        "forms/estates/DE-201(I) Application for Informal Probate of Will or Appointment (Rev. 09-12-19).pdf",
        "DE-201(I)",
    ),
    (
        "DE-401(A) Certificate of Value Resident and Non Resident.pdf",
        "forms/estates/DE-401(A) Certificate of Value Resident and Non Resident (Rev. 7-1-19).pdf",
        "DE-401(A)",
    ),
    (
        "DE-405 Inventory (Rev. 5-6-21).pdf",
        "forms/estates/DE-405 Inventory (Rev. 5-6-21).pdf",
        "DE-405",
    ),
    (
        "DE-406 Probate Account (Rev. 7-1-19).pdf",
        "forms/estates/DE-406 Probate Account (Rev. 7-1-19).pdf",
        "DE-406",
    ),
    (
        "DE-602 Sworn Statement (Rev. 7-1-19).pdf",
        "forms/estates/DE-602 Sworn Statement (Rev. 7-1-19).pdf",
        "DE-602",
    ),
    (
        "N-115 Notice re Appointment of PR to Heirs, Devisees (Rev. 7-1-19).pdf",
        "forms/notices/N-115 Notice re Appointment of PR to Heirs_ Devisees (Rev. 7-1-19).pdf",
        "N-115",
    ),
]


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def _ground_truth(sample_path: Path) -> list[tuple[int, tuple[float, float, float, float], str]]:
    """Returns [(page, (x0,y0,x1,y1), name), ...] from a completed sample."""
    out = []
    doc = fitz.open(sample_path)
    for pno, page in enumerate(doc):
        for w in page.widgets() or []:
            r = w.rect
            out.append((pno, (r.x0, r.y0, r.x1, r.y1), w.field_name or ""))
    doc.close()
    return out


def _candidate_rects(fields) -> list[tuple[int, tuple[float, float, float, float]]]:
    return [(f.page, (f.rect.x0, f.rect.y0, f.rect.x1, f.rect.y1)) for f in fields]


def _evaluate(
    truth: list[tuple[int, tuple[float, float, float, float], str]],
    candidates: list[tuple[int, tuple[float, float, float, float]]],
    threshold: float,
) -> tuple[int, set[int], set[int]]:
    """Greedy IoU matching. Returns (num_matched, matched_truth_idx, matched_cand_idx)."""
    if not truth or not candidates:
        return 0, set(), set()
    pairs = []
    for ti, (tp, trect, _) in enumerate(truth):
        for ci, (cp, crect) in enumerate(candidates):
            if tp != cp:
                continue
            iou = _iou(trect, crect)
            if iou >= threshold:
                pairs.append((iou, ti, ci))
    pairs.sort(reverse=True)
    used_t, used_c = set(), set()
    for _, ti, ci in pairs:
        if ti in used_t or ci in used_c:
            continue
        used_t.add(ti)
        used_c.add(ci)
    return len(used_t), used_t, used_c


def run(rerun: bool) -> None:
    base = Path(__file__).resolve().parents[1]
    rows = []
    totals = {"truth": 0, "h_n": 0, "h_m": 0, "h_ms": 0, "v_n": 0, "v_m": 0, "v_ms": 0}

    for sample_name, src_rel, form_id in SAMPLES:
        sample_path = base / "samples" / sample_name
        src_path = base / src_rel
        if not sample_path.exists() or not src_path.exists():
            print(f"[skip] {form_id}: missing files")
            continue

        truth = _ground_truth(sample_path)
        detection = load_detection(form_id)
        if detection is None:
            print(f"[skip] {form_id}: no detection JSON")
            continue
        h_cands = _candidate_rects(detection.fields)

        v_path = config.VALIDATION_DIR / f"{form_id}.json"
        if rerun and v_path.exists():
            v_path.unlink()
        validation = load_validation(form_id)
        if validation is None or any(
            f.detection_source != "qwen-vl-gated" for f in validation.fields
        ):
            print(f"[run] validator on {form_id}...")
            validation = validate_form(detection, str(src_path))
            v_path.write_text(validation.model_dump_json(indent=2))
        v_cands = _candidate_rects(validation.fields)

        h_match, _, _ = _evaluate(truth, h_cands, IOU_MATCH)
        h_match_s, _, _ = _evaluate(truth, h_cands, IOU_STRICT)
        v_match, _, _ = _evaluate(truth, v_cands, IOU_MATCH)
        v_match_s, _, _ = _evaluate(truth, v_cands, IOU_STRICT)

        rows.append(
            {
                "form_id": form_id,
                "truth": len(truth),
                "h_n": len(h_cands),
                "h_match": h_match,
                "h_match_strict": h_match_s,
                "v_n": len(v_cands),
                "v_match": v_match,
                "v_match_strict": v_match_s,
            }
        )
        totals["truth"] += len(truth)
        totals["h_n"] += len(h_cands)
        totals["h_m"] += h_match
        totals["h_ms"] += h_match_s
        totals["v_n"] += len(v_cands)
        totals["v_m"] += v_match
        totals["v_ms"] += v_match_s

    # ── report ────────────────────────────────────────────────────────────
    print()
    hdr = (
        f"{'form_id':<12} {'truth':>5} | "
        f"{'h_n':>4} {'h_m':>4} {'h_ms':>5} {'h_R':>5} {'h_P':>5} | "
        f"{'v_n':>4} {'v_m':>4} {'v_ms':>5} {'v_R':>5} {'v_P':>5}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        h_r = r["h_match"] / r["truth"] if r["truth"] else 0
        h_p = r["h_match"] / r["h_n"] if r["h_n"] else 0
        v_r = r["v_match"] / r["truth"] if r["truth"] else 0
        v_p = r["v_match"] / r["v_n"] if r["v_n"] else 0
        print(
            f"{r['form_id']:<12} {r['truth']:>5} | "
            f"{r['h_n']:>4} {r['h_match']:>4} {r['h_match_strict']:>5} {h_r:>5.2f} {h_p:>5.2f} | "
            f"{r['v_n']:>4} {r['v_match']:>4} {r['v_match_strict']:>5} {v_r:>5.2f} {v_p:>5.2f}"
        )
    print("-" * len(hdr))
    h_R = totals["h_m"] / totals["truth"] if totals["truth"] else 0
    h_P = totals["h_m"] / totals["h_n"] if totals["h_n"] else 0
    v_R = totals["v_m"] / totals["truth"] if totals["truth"] else 0
    v_P = totals["v_m"] / totals["v_n"] if totals["v_n"] else 0
    print(
        f"{'TOTAL':<12} {totals['truth']:>5} | "
        f"{totals['h_n']:>4} {totals['h_m']:>4} {totals['h_ms']:>5} {h_R:>5.2f} {h_P:>5.2f} | "
        f"{totals['v_n']:>4} {totals['v_m']:>4} {totals['v_ms']:>5} {v_R:>5.2f} {v_P:>5.2f}"
    )
    print()
    print(f"  IoU threshold: loose={IOU_MATCH}  strict={IOU_STRICT}")
    print(f"  h_* = heuristic-only ({IOU_MATCH} match)   v_* = validator-gated")
    print(f"  R = recall (matched / truth)   P = precision (matched / candidates)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", action="store_true", help="Delete stale validation/*.json and re-run")
    args = ap.parse_args()
    run(args.rerun)
