#!/usr/bin/env python3
"""Tier 0.5 of the geometry review: layout-aware OCR over the sentinel renders.

Independent of the analytic pass: PaddleOCR (PP-OCRv6, oneDNN off — the PIR
oneDNN path crashes on this host) reads each rendered page and we verify each
sentinel token's *actual* rendered position against its expected rect:

  token_missing   expected on the page but not recognized anywhere — strong
                  signal of glyph collision/garble (or a failed appearance)
  token_offset    found, but its center is > OFFSET_PT from the rect center
  merged_line     recognized inside the same OCR line as printed text — the
                  token abuts or touches printed glyphs

Every (form, field, widget) gets an OCR verdict; misplacements found on
fields the analytic pass did NOT flag become new candidates. Output merges
into <out>/ocr_results.jsonl; cross-checked rows in merged_candidates.jsonl.

    FLAGS_use_mkldnn=0 python3 scripts/geometry_review/ocr_check.py --out ~/geom-review-out
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.fill_pdf import _load_alignment  # noqa: E402

DPI = 150
SCALE = DPI / 72.0
OFFSET_PT = 8.0

_NORM = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8"})


def norm_token(s: str) -> str:
    return s.upper().replace(" ", "")


def find_token(tok: str, lines: list[tuple[str, list]]) -> tuple[list, str] | None:
    """Locate `tok` in OCR lines; return (quad-box, full line text)."""
    pats = [tok, "2Q" + tok[2:], "Z0" + tok[2:]]
    for text, poly in lines:
        up = norm_token(text)
        upn = up.translate(_NORM)
        for p in pats:
            pn = p.translate(_NORM)
            i = upn.find(pn)
            if i < 0:
                continue
            xs = [pt[0] for pt in poly]; ys = [pt[1] for pt in poly]
            x0, x1 = min(xs), max(xs)
            # proportional sub-box for the token inside a longer line
            cw = (x1 - x0) / max(1, len(up))
            return ([x0 + i * cw, min(ys), x0 + (i + len(p)) * cw, max(ys)], text)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--forms", help="comma-separated subset")
    args = ap.parse_args()

    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False,
                    use_textline_orientation=False, lang="en",
                    enable_mkldnn=False)

    token_dir = args.out / "tokens"
    forms = sorted(p.stem for p in token_dir.glob("*.json"))
    if args.forms:
        want = {f.strip() for f in args.forms.split(",")}
        forms = [f for f in forms if f in want]

    out_f = (args.out / "ocr_results.jsonl").open("a")
    done_marker = args.out / "ocr_done.txt"
    already = set(done_marker.read_text().splitlines()) if done_marker.exists() else set()

    for form in forms:
        if form in already:
            continue
        tokens = json.loads((token_dir / f"{form}.json").read_text())
        align_map = _load_alignment(form, ROOT)
        fdir = args.out / form
        pages = sorted(fdir.glob("page-*.png"))
        page_lines: dict[int, list] = {}
        for i, png in enumerate(pages):
            cache = fdir / f"ocr-{png.stem}.json"
            if cache.exists():
                page_lines[i] = json.loads(cache.read_text())
            else:
                r = ocr.predict(str(png))[0]
                lines = [(t, p.tolist()) for t, p in
                         zip(r["rec_texts"], r["rec_polys"])]
                cache.write_text(json.dumps(lines))
                page_lines[i] = lines

        n_bad = 0
        for tok, meta in tokens.items():
            pg = meta["page"]
            if pg not in page_lines:
                continue
            hit = find_token(tok, page_lines[pg])
            rect = meta["rect"]
            align = align_map.get(meta["field"], "left")
            h = rect[3] - rect[1]
            row = {"form": form, "field": meta["field"],
                   "widget_idx": meta["widget_idx"], "page": pg,
                   "token": tok, "rect": rect}
            if hit is None:
                # tiny boxes shrink the font below OCR readability — only
                # treat a miss as signal when the box could render legibly
                w_pt = rect[2] - rect[0]
                h_pt = rect[3] - rect[1]
                row["ocr"] = ("token_missing" if w_pt >= 18 and h_pt >= 7
                              else "unreadable_small")
            else:
                box, line_text = hit
                # horizontal expectation depends on declared justification;
                # vertical on single-line (centered) vs paragraph (top) boxes
                if align == "center":
                    dx = ((box[0] + box[2]) / 2
                          - (rect[0] + rect[2]) / 2 * SCALE) / SCALE
                elif align == "right":
                    dx = (box[2] - (rect[2] - 2) * SCALE) / SCALE
                else:
                    dx = (box[0] - (rect[0] + 2) * SCALE) / SCALE
                if h > 24:
                    dy = (box[1] - (rect[1] + 2) * SCALE) / SCALE
                else:
                    dy = ((box[1] + box[3]) / 2
                          - (rect[1] + rect[3]) / 2 * SCALE) / SCALE
                row["dx_pt"] = round(dx, 1)
                row["dy_pt"] = round(dy, 1)
                others = norm_token(line_text).replace(norm_token(tok), "").strip()
                if abs(dx) > OFFSET_PT or abs(dy) > OFFSET_PT:
                    row["ocr"] = "token_offset"
                elif len(others) > 2:
                    row["ocr"] = "merged_line"
                    row["line_text"] = line_text[:80]
                else:
                    row["ocr"] = "ok"
            if row.get("ocr") not in ("ok", "unreadable_small"):
                n_bad += 1
            out_f.write(json.dumps(row) + "\n")
        with done_marker.open("a") as fh:
            fh.write(form + "\n")
        print(f"{form}: {len(tokens)} tokens, {n_bad} OCR flags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
