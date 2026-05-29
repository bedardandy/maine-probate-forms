"""For each form, identify truth widgets that the heuristic matched but the
validator dropped. Output: which detected fields the gating step rejected
that were actually correct."""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.field_detector import load_detection
from modules.vlm_validator import load_validation
from scripts.benchmark_samples import SAMPLES, _ground_truth, _iou


def _find_match(rect, page, candidates, threshold=0.30):
    best = None
    best_iou = 0.0
    for f in candidates:
        if f.page != page:
            continue
        cr = (f.rect.x0, f.rect.y0, f.rect.x1, f.rect.y1)
        i = _iou(rect, cr)
        if i > best_iou and i >= threshold:
            best, best_iou = f, i
    return best


def diagnose(form_id: str, sample_path: Path) -> None:
    truth = _ground_truth(sample_path)
    detection = load_detection(form_id)
    validation = load_validation(form_id)
    if detection is None or validation is None:
        return
    h_fields = detection.fields
    v_fields = validation.fields

    drops = []  # truth widgets matched by heuristic but lost by validator
    for tp, trect, tname in truth:
        h = _find_match(trect, tp, h_fields)
        if h is None:
            continue
        v = _find_match(trect, tp, v_fields)
        if v is None:
            drops.append((tp, trect, tname, h))

    print(f"\n{form_id} — {len(drops)} true-positive(s) dropped by validator")
    print(f"  (heuristic matched {sum(1 for tp,tr,_ in truth if _find_match(tr,tp,h_fields))}/{len(truth)} truth widgets)")
    if not drops:
        return
    print(f"  {'page':<4} {'rect':<32} {'h_type':<10} {'truth_name':<25} nearby_label")
    for tp, trect, tname, h in drops[:30]:
        rect_s = f"[{trect[0]:.0f},{trect[1]:.0f},{trect[2]:.0f},{trect[3]:.0f}]"
        print(
            f"  {tp:<4} {rect_s:<32} {h.field_type.value:<10} "
            f"{tname[:24]:<25} {(h.nearby_label or '')[:40]}"
        )
    if len(drops) > 30:
        print(f"  ... +{len(drops) - 30} more")


def heuristic_misses(form_id: str, sample_path: Path) -> None:
    truth = _ground_truth(sample_path)
    detection = load_detection(form_id)
    if detection is None:
        return
    misses = []
    for tp, trect, tname in truth:
        if _find_match(trect, tp, detection.fields) is None:
            misses.append((tp, trect, tname))
    print(f"\n{form_id} — {len(misses)} truth widget(s) the heuristic missed entirely")
    if not misses:
        return

    # Look up nearby text in the source PDF for context
    src_map = {fid: src for _, src, fid in SAMPLES}
    src = Path(__file__).resolve().parents[1] / src_map.get(form_id, "")
    if not src.exists():
        return
    doc = fitz.open(src)
    print(f"  {'page':<4} {'rect':<32} {'name':<28} surrounding_text (first 80 chars)")
    for tp, trect, tname in misses[:20]:
        page = doc[tp]
        # search words within 50pt of the widget
        x0, y0, x1, y1 = trect
        search = fitz.Rect(x0 - 80, y0 - 25, x1 + 80, y1 + 25)
        words = page.get_text("words", clip=search)
        text = " ".join(w[4] for w in words)[:80]
        rect_s = f"[{x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}]"
        print(f"  {tp:<4} {rect_s:<32} {tname[:27]:<28} {text}")
    if len(misses) > 20:
        print(f"  ... +{len(misses) - 20} more")
    doc.close()


if __name__ == "__main__":
    base = Path(__file__).resolve().parents[1]
    target = sys.argv[1] if len(sys.argv) > 1 else None
    mode = sys.argv[2] if len(sys.argv) > 2 else "drops"  # drops | misses | both
    for sample_name, _, form_id in SAMPLES:
        if target and form_id != target:
            continue
        sp = base / "samples" / sample_name
        if not sp.exists():
            continue
        if mode in ("drops", "both"):
            diagnose(form_id, sp)
        if mode in ("misses", "both"):
            heuristic_misses(form_id, sp)
