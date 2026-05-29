"""Compare CF baseline + sweep variants against our v2 layer1, on 5 panel forms.

Reads:
  - forms/<cat>/<name>.pdf (originals)
  - output_layer1/<cat>/<stem>_fillable.pdf (ours v2)
  - output_commonforms/<cat>/<stem>_commonforms.pdf (CF baseline)
  - output_commonforms/sweep/<variant>/<cat>/<stem>_commonforms.pdf

Writes reports/commonforms-sweep.md with one row per (form, variant).
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORIG_DIR = ROOT / "forms"
OURS_DIR = ROOT / "output_layer1"
CF_BASE_DIR = ROOT / "output_commonforms"
SWEEP_DIR = CF_BASE_DIR / "sweep"
REPORT = ROOT / "reports" / "commonforms-sweep.md"

FORMS = [
    ("estates", "DE-101(I) Application for Informal - Intestate (Rev. 09-12-19).pdf"),
    ("estates", "DE-104 PR Acceptance (Rev. 07-01-19).pdf"),
    ("gc_adults", "PP-205 Joined Petition for Guardian and Conservator (Rev. 07-01-19).pdf"),
    ("name_change", "NC-001 Petition for Name Change of Minor.pdf"),
    ("estates", "DE-405 Inventory (Rev. 5-6-21).pdf"),
]

VARIANTS = ["baseline", "highres", "precision", "recall", "sig_multi", "all_in", "maxrecall_hires"]
IOU_MATCH_THRESHOLD = 0.30


@dataclass
class Widget:
    page: int
    rect: tuple


def load_widgets(pdf: pathlib.Path) -> list[Widget]:
    out = []
    if not pdf.exists():
        return out
    d = fitz.open(pdf)
    for pno, page in enumerate(d):
        for w in page.widgets() or []:
            r = w.rect
            out.append(Widget(pno, (r.x0, r.y0, r.x1, r.y1)))
    d.close()
    return out


def iou(a: tuple, b: tuple) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def center_dist(a: tuple, b: tuple) -> float:
    acx, acy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bcx, bcy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5


def match(ours: list[Widget], cf: list[Widget]) -> dict:
    by_p_o: dict[int, list[Widget]] = {}
    by_p_c: dict[int, list[Widget]] = {}
    for w in ours:
        by_p_o.setdefault(w.page, []).append(w)
    for w in cf:
        by_p_c.setdefault(w.page, []).append(w)
    pairs = []
    for p in set(by_p_o) | set(by_p_c):
        a = by_p_o.get(p, [])
        b = by_p_c.get(p, [])
        for i, wa in enumerate(a):
            for j, wb in enumerate(b):
                pairs.append((iou(wa.rect, wb.rect), p, i, j, wa, wb))
    pairs.sort(key=lambda x: -x[0])
    used = set()
    matched = []
    for s, p, i, j, wa, wb in pairs:
        if s < IOU_MATCH_THRESHOLD:
            break
        if (p, "o", i) in used or (p, "c", j) in used:
            continue
        used.add((p, "o", i))
        used.add((p, "c", j))
        matched.append((wa, wb, s))
    ours_matched = {(p, i) for p, side, i in used if side == "o"}
    cf_matched = {(p, j) for p, side, j in used if side == "c"}
    ours_extra = sum(1 for w in ours if (w.page, ours.index(w)) not in ours_matched)
    # use list comprehension by page index instead
    ours_extra = 0
    for p, ws in by_p_o.items():
        for i in range(len(ws)):
            if (p, i) not in ours_matched:
                ours_extra += 1
    cf_extra = 0
    for p, ws in by_p_c.items():
        for j in range(len(ws)):
            if (p, j) not in cf_matched:
                cf_extra += 1
    if matched:
        ious = [m[2] for m in matched]
        cds = [center_dist(m[0].rect, m[1].rect) for m in matched]
        mean_iou = sum(ious) / len(ious)
        mean_cd = sum(cds) / len(cds)
    else:
        mean_iou = 0.0
        mean_cd = 0.0
    return dict(
        matched=len(matched),
        ours_extra=ours_extra,
        cf_extra=cf_extra,
        mean_iou=mean_iou,
        mean_cd=mean_cd,
    )


def cf_path(variant: str, cat: str, stem: str) -> pathlib.Path:
    if variant == "baseline":
        return CF_BASE_DIR / cat / f"{stem}_commonforms.pdf"
    return SWEEP_DIR / variant / cat / f"{stem}_commonforms.pdf"


def main() -> None:
    rows = []
    for cat, name in FORMS:
        stem = pathlib.Path(name).stem
        ours_pdf = OURS_DIR / cat / f"{stem}_fillable.pdf"
        ours = load_widgets(ours_pdf)
        for v in VARIANTS:
            cf = load_widgets(cf_path(v, cat, stem))
            if not cf:
                rows.append((name, v, len(ours), 0, 0, 0, 0, 0.0, 0.0, "MISSING"))
                continue
            m = match(ours, cf)
            rows.append(
                (
                    name,
                    v,
                    len(ours),
                    len(cf),
                    m["matched"],
                    m["ours_extra"],
                    m["cf_extra"],
                    m["mean_iou"],
                    m["mean_cd"],
                    "",
                )
            )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    md = ["# CommonForms parameter sweep vs Layer1 v2\n"]
    md.append("Same 5 panel forms, 5 CF variants + baseline. v2 layer1 is the comparison anchor.\n")
    md.append("Greedy per-page IoU matching at 0.30 threshold. Center distance in PDF points.\n")
    md.append("**Variants:**")
    md.append("- `baseline`: image-size 1600, confidence 0.3 (default)")
    md.append("- `highres`: image-size 2400, confidence 0.3")
    md.append("- `precision`: image-size 1600, confidence 0.45")
    md.append("- `recall`: image-size 1600, confidence 0.20")
    md.append("- `sig_multi`: 1600/0.3 + --use-signature-fields --multiline")
    md.append("- `all_in`: 2400/0.3 + --use-signature-fields --multiline")
    md.append("- `maxrecall_hires`: 2400/0.20 + --use-signature-fields --multiline (combined recall+highres)\n")
    md.append("| form | variant | ours | cf | matched | ours-only | cf-only | mean IoU | mean dist (pt) |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        name, v, o, c, m, oe, ce, miou, mcd, note = r
        if note:
            md.append(f"| {name} | {v} | {o} | — | — | — | — | — | — _{note}_ |")
        else:
            md.append(f"| {name} | {v} | {o} | {c} | {m} | {oe} | {ce} | {miou:.3f} | {mcd:.1f} |")

    # Aggregate per variant (mean across forms)
    md.append("\n## Per-variant aggregates (mean across 5 forms)\n")
    md.append("| variant | avg cf widgets | avg matched | avg cf-only | avg mean IoU | avg mean dist (pt) |")
    md.append("|---|---:|---:|---:|---:|---:|")
    by_var: dict[str, list] = {}
    for r in rows:
        name, v, o, c, m, oe, ce, miou, mcd, note = r
        if note:
            continue
        by_var.setdefault(v, []).append((c, m, ce, miou, mcd))
    for v in VARIANTS:
        items = by_var.get(v, [])
        if not items:
            continue
        n = len(items)
        ac = sum(x[0] for x in items) / n
        am = sum(x[1] for x in items) / n
        ace = sum(x[2] for x in items) / n
        ai = sum(x[3] for x in items) / n
        ad = sum(x[4] for x in items) / n
        md.append(f"| {v} | {ac:.1f} | {am:.1f} | {ace:.1f} | {ai:.3f} | {ad:.1f} |")

    REPORT.write_text("\n".join(md))
    print(REPORT)


if __name__ == "__main__":
    main()
