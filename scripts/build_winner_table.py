"""Stage 1.D: per-form winner table + feature extraction.

For every form audited in both v2 and fused, produce a row with:
  - v2 issue count (alignment / naming / missing / total)
  - fused issue count
  - delta (fused - v2)
  - bucket (fused / v2 / wash)
  - feature columns to support a heuristic classifier in Stage 4

Features extracted from source-PDF + v2/fused outputs:
  - n_pages
  - n_widgets_v2
  - n_widgets_fused
  - n_cells (from fuse pipeline cell detector)
  - n_columns_text_lines (modal text-line x0 peak count)
  - v2_naming_quality (fraction of names that look "clean")
  - has_wingdings (binary)
  - section_banner_count (heuristic for sectioned forms)
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import Counter

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

V2_AUDIT = ROOT / "reports" / "opus-alignment-layer1-full"
FUSED_AUDIT = ROOT / "reports" / "opus-alignment-fused-full"
ORIG_DIR = ROOT / "forms"
V2_DIR = ROOT / "output_layer1"
FUSED_DIR = ROOT / "output_fused"
ANALYSIS_DIR = ROOT / "intermediate_layer1" / "analysis"
OUT_REPORT = ROOT / "reports" / "stage1-winner-table.md"

BUCKET_THRESHOLD = 5  # |Δ| < 5 = wash; Δ < -5 = fused wins; Δ > +5 = v2 wins


def issue_counts(report_path: pathlib.Path) -> tuple[int, int, int, int] | None:
    if not report_path.exists():
        return None
    d = json.loads(report_path.read_text())
    a = n = m = 0
    for pg in d.get("pages", []):
        for issue in pg.get("issues", []):
            t = issue.get("type", "")
            if t == "alignment": a += 1
            elif t == "naming":  n += 1
            elif t == "missing": m += 1
    return (a + n + m, a, n, m)


def widget_count(pdf: pathlib.Path) -> int:
    if not pdf.exists():
        return 0
    d = fitz.open(pdf)
    n = sum(len(list(p.widgets())) for p in d)
    d.close()
    return n


def naming_quality_score(pdf: pathlib.Path) -> float:
    """Fraction of widget names that look clean snake_case."""
    if not pdf.exists():
        return 0.0
    d = fitz.open(pdf)
    names = []
    for p in d:
        for w in p.widgets() or []:
            if w.field_name:
                names.append(w.field_name)
    d.close()
    if not names:
        return 0.0
    clean = 0
    for n in names:
        # Clean = pure snake_case lowercase, no spaces, no capital letters
        if re.fullmatch(r"[a-z][a-z0-9_]*", n):
            clean += 1
    return clean / len(names)


def feature_extract(form_id: str, cat: str, name: str) -> dict:
    stem = pathlib.Path(name).stem
    src = ORIG_DIR / cat / name
    v2 = V2_DIR / cat / f"{stem}_fillable.pdf"
    fu = FUSED_DIR / cat / f"{stem}_fused.pdf"
    feats = {
        "n_pages": 0,
        "n_widgets_v2": widget_count(v2),
        "n_widgets_fused": widget_count(fu),
        "n_cells": 0,
        "n_text_line_peaks": 0,
        "v2_naming_quality": naming_quality_score(v2),
        "has_wingdings": 0,
        "section_banner_count": 0,
    }
    # Open source PDF for page count + font detection
    try:
        d = fitz.open(src)
        feats["n_pages"] = d.page_count
        d.close()
    except Exception:
        pass
    # Use the analysis JSON for cell/column/font signals
    apath = ANALYSIS_DIR / f"{form_id}.json"
    if apath.exists():
        try:
            from scripts.fuse_layer1_cf import _detect_table_cells, _text_line_peaks
            a = json.loads(apath.read_text())
            cells = 0
            peak_set = set()
            wingdings = False
            banners = 0
            for pg in a.get("pages", []):
                cells += len(_detect_table_cells(pg))
                for x in _text_line_peaks(pg):
                    peak_set.add(round(x))
                for tb in pg.get("text_blocks", []):
                    for ln in tb.get("lines", []):
                        for sp in ln.get("spans", []):
                            if any(k in sp.get("font", "").lower() for k in ("wing", "zapf", "symbol")):
                                wingdings = True
                            text = sp.get("text", "").strip()
                            # "A.", "B.", "1.", "I." in caps section banner pattern
                            if re.match(r"^[A-Z]\.\s+[A-Z]", text):
                                banners += 1
            feats["n_cells"] = cells
            feats["n_text_line_peaks"] = len(peak_set)
            feats["has_wingdings"] = int(wingdings)
            feats["section_banner_count"] = banners
        except Exception:
            pass
    return feats


def main() -> int:
    rows: list[dict] = []
    for cat_dir in sorted(V2_DIR.iterdir()):
        if not cat_dir.is_dir():
            continue
        for v2_pdf in sorted(cat_dir.glob("*_fillable.pdf")):
            stem = v2_pdf.stem.replace("_fillable", "")
            name = stem + ".pdf"
            v2_rep = issue_counts(V2_AUDIT / f"{stem}.json")
            fu_rep = issue_counts(FUSED_AUDIT / f"{stem}_fused.json")
            if not v2_rep or not fu_rep:
                continue
            # Form id = leading code in filename
            m = re.match(r"^([A-Z]+-?\d+(?:\([A-Z]\))?)", name)
            form_id = m.group(1) if m else stem
            feats = feature_extract(form_id, cat_dir.name, name)
            delta = fu_rep[0] - v2_rep[0]
            if delta <= -BUCKET_THRESHOLD:
                bucket = "fused"
            elif delta >= BUCKET_THRESHOLD:
                bucket = "v2"
            else:
                bucket = "wash"
            rows.append({
                "form_id": form_id,
                "category": cat_dir.name,
                "stem": stem,
                "v2": v2_rep[0],
                "v2_a": v2_rep[1],
                "v2_n": v2_rep[2],
                "v2_m": v2_rep[3],
                "fused": fu_rep[0],
                "fu_a": fu_rep[1],
                "fu_n": fu_rep[2],
                "fu_m": fu_rep[3],
                "delta": delta,
                "bucket": bucket,
                **feats,
            })

    # Aggregate
    by_bucket = Counter(r["bucket"] for r in rows)
    total_v2 = sum(r["v2"] for r in rows)
    total_fu = sum(r["fused"] for r in rows)

    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    md = ["# Stage 1 — full-sweep winner table\n"]
    md.append(f"Forms audited: **{len(rows)}**  |  v2 total: **{total_v2}**  |  fused total: **{total_fu}**  |  Δ: **{total_fu-total_v2:+d}**\n")
    md.append("Bucket distribution: " + ", ".join(f"**{b}**={n}" for b, n in by_bucket.most_common()) + "\n")
    md.append("Bucket rule: fused wins if Δ ≤ −5; v2 wins if Δ ≥ +5; otherwise wash.\n")

    md.append("## Per-form table\n")
    md.append("| form | cat | v2 (A/N/M) | fused (A/N/M) | Δ | bucket | pages | cells | peaks | wing | sect | v2_qual |")
    md.append("|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|")
    for r in sorted(rows, key=lambda r: r["delta"]):
        md.append(
            f"| {r['form_id']} | {r['category']} | "
            f"{r['v2']} ({r['v2_a']}/{r['v2_n']}/{r['v2_m']}) | "
            f"{r['fused']} ({r['fu_a']}/{r['fu_n']}/{r['fu_m']}) | "
            f"{r['delta']:+d} | {r['bucket']} | "
            f"{r['n_pages']} | {r['n_cells']} | {r['n_text_line_peaks']} | "
            f"{r['has_wingdings']} | {r['section_banner_count']} | "
            f"{r['v2_naming_quality']:.2f} |"
        )

    # Per-bucket feature summary
    md.append("\n## Mean feature values per bucket\n")
    md.append("| feature | fused | wash | v2 |")
    md.append("|---|---:|---:|---:|")
    feat_keys = ["n_pages", "n_cells", "n_text_line_peaks", "n_widgets_v2",
                 "v2_naming_quality", "has_wingdings", "section_banner_count"]
    for key in feat_keys:
        row = f"| {key} |"
        for b in ("fused", "wash", "v2"):
            sub = [r[key] for r in rows if r["bucket"] == b]
            if sub:
                row += f" {sum(sub)/len(sub):.2f} |"
            else:
                row += " — |"
        md.append(row)

    OUT_REPORT.write_text("\n".join(md))
    print(f"Wrote {OUT_REPORT}")
    print(f"Total: v2={total_v2}, fused={total_fu}, Δ={total_fu - total_v2:+d}")
    print(f"Buckets: {dict(by_bucket)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
