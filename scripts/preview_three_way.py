"""Render 3-way preview PNGs: v2 (red) / CF maxrecall_hires (green) / fused (blue).

For each panel form, write reports/three-way-previews/<stem>/page-NN.png.
"""
from __future__ import annotations

import pathlib

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORIG_DIR = ROOT / "forms"
V2_DIR = ROOT / "output_layer1"
CF_DIR = ROOT / "output_commonforms" / "imgsize_3200"
FUSED_DIR = ROOT / "output_fused"
OUT = ROOT / "reports" / "three-way-previews"

PANEL = [
    ("estates", "DE-101(I) Application for Informal - Intestate (Rev. 09-12-19).pdf"),
    ("estates", "DE-104 PR Acceptance (Rev. 07-01-19).pdf"),
    ("gc_adults", "PP-205 Joined Petition for Guardian and Conservator (Rev. 07-01-19).pdf"),
    ("name_change", "NC-001 Petition for Name Change of Minor.pdf"),
    ("estates", "DE-405 Inventory (Rev. 5-6-21).pdf"),
]


def widgets_by_page(pdf: pathlib.Path) -> dict:
    out: dict = {}
    if not pdf.exists():
        return out
    d = fitz.open(pdf)
    for pno, page in enumerate(d):
        out[pno] = [(w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1) for w in (page.widgets() or [])]
    d.close()
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for cat, name in PANEL:
        stem = pathlib.Path(name).stem
        src = ORIG_DIR / cat / name
        v2 = widgets_by_page(V2_DIR / cat / f"{stem}_fillable.pdf")
        cf = widgets_by_page(CF_DIR / cat / f"{stem}_commonforms.pdf")
        fused = widgets_by_page(FUSED_DIR / cat / f"{stem}_fused.pdf")
        out_dir = OUT / stem
        out_dir.mkdir(parents=True, exist_ok=True)
        d = fitz.open(src)
        for pno, page in enumerate(d):
            for r in v2.get(pno, []):
                page.draw_rect(fitz.Rect(*r), color=(1, 0.2, 0.2), width=0.6)
            for r in cf.get(pno, []):
                page.draw_rect(fitz.Rect(*r), color=(0.1, 0.7, 0.1), width=0.6)
            for r in fused.get(pno, []):
                page.draw_rect(fitz.Rect(*r), color=(0.1, 0.3, 0.9), width=0.9)
            page.get_pixmap(dpi=110).save(out_dir / f"page-{pno+1:02d}.png")
        d.close()
        print(f"{stem}: v2={sum(len(v) for v in v2.values())} "
              f"cf={sum(len(v) for v in cf.values())} "
              f"fused={sum(len(v) for v in fused.values())}")
    print(f"\nPreviews: {OUT}")


if __name__ == "__main__":
    main()
