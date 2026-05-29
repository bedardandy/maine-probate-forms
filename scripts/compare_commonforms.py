"""Compare commonforms output vs our layer1 output on the same originals.

Per form: count widgets per page, match widgets across the two outputs by greedy
nearest-IoU, report mean IoU + center-distance of matched pairs, count
unmatched (extra/missing) on each side, render side-by-side preview PNGs.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
OURS_DIR = ROOT / "output_layer1"
CF_DIR = ROOT / "output_commonforms"
ORIG_DIR = ROOT / "forms"
PREVIEW_DIR = ROOT / "reports" / "commonforms-comparison-previews"
REPORT = ROOT / "reports" / "commonforms-comparison.md"

FORMS = [
    ("estates", "DE-101(I) Application for Informal - Intestate (Rev. 09-12-19).pdf"),
    ("estates", "DE-104 PR Acceptance (Rev. 07-01-19).pdf"),
    ("gc_adults", "PP-205 Joined Petition for Guardian and Conservator (Rev. 07-01-19).pdf"),
    ("name_change", "NC-001 Petition for Name Change of Minor.pdf"),
    ("estates", "DE-405 Inventory (Rev. 5-6-21).pdf"),
]

IOU_MATCH_THRESHOLD = 0.30


@dataclass
class Widget:
    page: int
    rect: tuple
    name: str
    type: int


def load_widgets(pdf_path: pathlib.Path) -> list[Widget]:
    out = []
    d = fitz.open(pdf_path)
    for pno, page in enumerate(d):
        for w in page.widgets() or []:
            r = w.rect
            out.append(Widget(pno, (r.x0, r.y0, r.x1, r.y1), w.field_name or "", w.field_type))
    d.close()
    return out


def iou(a: tuple, b: tuple) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    aw, ah = a[2] - a[0], a[3] - a[1]
    bw, bh = b[2] - b[0], b[3] - b[1]
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def center_dist(a: tuple, b: tuple) -> float:
    acx, acy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bcx, bcy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5


def match_widgets(ours: list[Widget], cf: list[Widget]) -> tuple[list, list, list]:
    """Greedy IoU matching per page. Returns (matches, ours_unmatched, cf_unmatched)."""
    by_page_ours: dict[int, list[Widget]] = {}
    by_page_cf: dict[int, list[Widget]] = {}
    for w in ours:
        by_page_ours.setdefault(w.page, []).append(w)
    for w in cf:
        by_page_cf.setdefault(w.page, []).append(w)

    matches = []
    ours_unmatched = []
    cf_unmatched = []
    pages = set(by_page_ours) | set(by_page_cf)
    for p in sorted(pages):
        a = list(by_page_ours.get(p, []))
        b = list(by_page_cf.get(p, []))
        pairs = []
        for i, wa in enumerate(a):
            for j, wb in enumerate(b):
                pairs.append((iou(wa.rect, wb.rect), i, j))
        pairs.sort(reverse=True)
        used_a, used_b = set(), set()
        for s, i, j in pairs:
            if s < IOU_MATCH_THRESHOLD:
                break
            if i in used_a or j in used_b:
                continue
            used_a.add(i)
            used_b.add(j)
            matches.append((a[i], b[j], s))
        ours_unmatched.extend(a[i] for i in range(len(a)) if i not in used_a)
        cf_unmatched.extend(b[j] for j in range(len(b)) if j not in used_b)
    return matches, ours_unmatched, cf_unmatched


def render_preview(orig_pdf: pathlib.Path, ours: list[Widget], cf: list[Widget], out_dir: pathlib.Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = fitz.open(orig_pdf)
    by_page_ours: dict[int, list[Widget]] = {}
    by_page_cf: dict[int, list[Widget]] = {}
    for w in ours:
        by_page_ours.setdefault(w.page, []).append(w)
    for w in cf:
        by_page_cf.setdefault(w.page, []).append(w)
    for pno, page in enumerate(d):
        for w in by_page_ours.get(pno, []):
            page.draw_rect(fitz.Rect(*w.rect), color=(1, 0, 0), width=0.7)
        for w in by_page_cf.get(pno, []):
            page.draw_rect(fitz.Rect(*w.rect), color=(0, 0.6, 0), width=0.7)
        pix = page.get_pixmap(dpi=110)
        pix.save(out_dir / f"page-{pno+1:02d}.png")
    n = d.page_count
    d.close()
    return n


def stats_for(matches: list, ours_um: list, cf_um: list) -> dict:
    if matches:
        ious = [m[2] for m in matches]
        cdists = [center_dist(m[0].rect, m[1].rect) for m in matches]
        mean_iou = sum(ious) / len(ious)
        mean_cd = sum(cdists) / len(cdists)
    else:
        mean_iou = 0.0
        mean_cd = 0.0
    return {
        "matched": len(matches),
        "ours_extra": len(ours_um),
        "cf_extra": len(cf_um),
        "mean_iou": mean_iou,
        "mean_center_dist_pt": mean_cd,
    }


def main() -> None:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for cat, name in FORMS:
        orig = ORIG_DIR / cat / name
        ours = OURS_DIR / cat / (orig.stem + "_fillable.pdf")
        cf = CF_DIR / cat / (orig.stem + "_commonforms.pdf")
        if not (orig.exists() and ours.exists() and cf.exists()):
            print(f"[skip] missing files for {name}")
            continue
        ws_ours = load_widgets(ours)
        ws_cf = load_widgets(cf)
        matches, ours_um, cf_um = match_widgets(ws_ours, ws_cf)
        s = stats_for(matches, ours_um, cf_um)
        s["form"] = name
        s["category"] = cat
        s["ours_total"] = len(ws_ours)
        s["cf_total"] = len(ws_cf)
        preview_dir = PREVIEW_DIR / orig.stem
        s["pages"] = render_preview(orig, ws_ours, ws_cf, preview_dir)
        rows.append(s)
        print(f"{name}: ours={s['ours_total']} cf={s['cf_total']} matched={s['matched']} "
              f"mean_iou={s['mean_iou']:.3f} mean_dist={s['mean_center_dist_pt']:.1f}pt")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    md = ["# CommonForms vs Layer1 comparison\n"]
    md.append("FFDNet-L (default, image_size=1600, conf=0.3) vs our v2 layer1 outputs.\n")
    md.append("Greedy per-page IoU matching with 0.30 threshold. Center distance in PDF points (72pt = 1in).\n")
    md.append("| form | pages | ours | cf | matched | ours-only | cf-only | mean IoU | mean center-dist (pt) |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        md.append(f"| {r['form']} | {r['pages']} | {r['ours_total']} | {r['cf_total']} | "
                  f"{r['matched']} | {r['ours_extra']} | {r['cf_extra']} | "
                  f"{r['mean_iou']:.3f} | {r['mean_center_dist_pt']:.1f} |")
    md.append("\n## Reading the table\n")
    md.append("- **matched**: widgets present in both outputs at IoU >= 0.30 on the same page.")
    md.append("- **ours-only / cf-only**: widgets only one side detected.")
    md.append("- **mean IoU**: 1.0 = perfect overlap; 0.5 typical for half-overlapping rects of similar size.")
    md.append("- **mean center-dist**: visual offset between matched widget centers in points.\n")
    md.append("## Previews\n")
    md.append("Red = ours (layer1), Green = commonforms. PNGs in `commonforms-comparison-previews/<form>/`.\n")
    REPORT.write_text("\n".join(md))
    print(f"\nReport: {REPORT}")
    print(f"Previews: {PREVIEW_DIR}")


if __name__ == "__main__":
    main()
