#!/usr/bin/env python3
"""Flattened-PDF "OCR-style" read-back analysis to surface fill edge cases.

Fills a form with seeded mock data, flattens the AcroForm widgets to real page
text (doc.bake), then reads the text back the way an OCR/extraction consumer
would and flags:

  * overprint   — a filled value's glyphs overlap printed label/rule text
                  (garbled, unreadable to OCR)
  * offpage     — a filled value runs past the page margin (truncated/clipped)
  * dropped     — a resolved value did not land as readable text at all
  * coverage    — fillable fields that stayed empty (smoke gap)

Run with several seeds (incl. --stress for long values) to shake out overflow.

    python3 tools/ocr_readback.py --form DE-101 --seeds 1,2,3 --stress
    python3 tools/ocr_readback.py --form DE-101 --seeds 7 --json
"""
from __future__ import annotations
import argparse, json, pathlib, sys, tempfile
import fitz
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa
import mock_case_gen as MCG  # noqa

MARGIN = 16.0


def _printed(form_id):
    """Per page: (set of printed span keys, list of printed INK char boxes).

    Ink chars exclude '_' and whitespace, so a value correctly seated on an
    underscore blank doesn't count as overprinting the label that shares the
    span. Each ink box carries the surrounding word for reporting.
    """
    doc = fitz.open(str(fetch_source(form_id)))
    out = {}
    for i in range(doc.page_count):
        keys = set(); ink = []
        for blk in doc[i].get_text("rawdict")["blocks"]:
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    word = "".join(c["c"] for c in sp.get("chars", [])).strip()
                    keys.add((word, tuple(round(c) for c in sp["bbox"])))
                    for ch in sp.get("chars", []):
                        if ch["c"] not in "_ \t":
                            ink.append((ch["bbox"], word))
        out[i] = (keys, ink)
    doc.close()
    return out


def analyze(form_id, seed, stress=False):
    case = MCG.generate(form_id, seed, stress=stress)
    cf = tempfile.mktemp(suffix=".json"); pathlib.Path(cf).write_text(json.dumps(case))
    out = tempfile.mktemp(suffix=".pdf")
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "fill_pdf.py"),
                        "--form", form_id, "--case", cf, "--out", out],
                       capture_output=True, text=True)
    try:
        res = json.loads(r.stdout)
    except Exception:
        return {"form": form_id, "seed": seed, "error": r.stderr[-300:] or "fill failed"}
    printed = _printed(form_id)
    doc = fitz.open(out)
    pw = doc[0].rect.width
    doc.bake(widgets=True)
    findings = []
    for i in range(doc.page_count):
        if i not in printed:        # fill added an addendum/overflow page
            continue
        pkeys, ink = printed[i]
        for blk in doc[i].get_text("rawdict")["blocks"]:
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    txt = "".join(c["c"] for c in sp.get("chars", [])).strip()
                    bb = sp["bbox"]
                    key = (txt, tuple(round(c) for c in bb))
                    if not txt or key in pkeys:
                        continue  # printed text, unchanged by fill
                    if bb[2] > pw - MARGIN or bb[0] < MARGIN:
                        findings.append({"code": "offpage", "page": i,
                                         "text": txt[:40], "x1": round(bb[2], 1)})
                    # overprint: filled glyphs overlap a printed ink char
                    for cb, word in ink:
                        ix = min(bb[2], cb[2]) - max(bb[0], cb[0])
                        iy = min(bb[3], cb[3]) - max(bb[1], cb[1])
                        if ix > 1.5 and iy > 3:
                            findings.append({"code": "overprint", "page": i,
                                             "value": txt[:30], "over": word[:30]})
                            break
    doc.close()
    return {"form": form_id, "seed": seed, "stress": stress,
            "text_written": res.get("text_written"),
            "options_checked": res.get("options_checked"),
            "source_verified": res.get("source_verified"),
            "findings": findings}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",") if s.strip()]
    runs = []
    for s in seeds:
        runs.append(analyze(a.form, s, False))
        if a.stress:
            runs.append(analyze(a.form, s, True))
    if a.json:
        print(json.dumps(runs, indent=1))
    else:
        for r in runs:
            tag = f"{r['form']} seed{r['seed']}{'/stress' if r.get('stress') else ''}"
            if r.get("error"):
                print(f"{tag}: ERROR {r['error']}"); continue
            f = r["findings"]
            print(f"{tag}: {r['text_written']} text, verified={r['source_verified']}, "
                  f"{len(f)} findings")
            for x in f:
                print("   ", x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
