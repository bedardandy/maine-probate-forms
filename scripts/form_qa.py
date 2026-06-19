#!/usr/bin/env python3
"""Per-form QA harness: questions -> fields -> placeholder fill -> read-back.

The closed loop for verifying a form is modelled correctly and reads well:

  1. PROMPTS  - extract every printed text line from the official PDF's digital
     text layer (exact, not OCR), with bboxes. Lines ending ":"/"?", numbered/
     lettered items, and checkbox-option labels are marked as "asks".
  2. MAP      - associate each prompt with the schema field(s)/widget(s) that
     answer it by geometry (a widget on the prompt's line to the right, or on the
     line(s) below within the same block).
  3. CHECK    - flag GAP (an "asks" prompt with no answer widget), ORPHAN (a
     widget with no prompt to its left/above), and SEMANTIC mismatch (the field's
     value_guide label/expectation shares no keywords with the prompt).
  4. FILL     - render a QA copy where EVERY fillable field carries a short
     plausible value (from the value guide) and never blank ("N/A" fallback);
     one option is checked per choice. Rasterised to PNG for a layout-aware
     read-back (agent vision) -- a printed question with no value beside it, or a
     value floating with no question, jumps out.

    python3 scripts/form_qa.py --form "DE-101(I)" --out-dir /tmp/qa
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402
from fill_pdf import (_ALIGN_CONST, _add_checkbox, _add_text, _load_alignment,  # noqa: E402
                      _strip_widgets, _value_for_printed_context)

ASKS_RE = re.compile(r"[:?]\s*$|^\(?[0-9]{1,2}[.)]|^\(?[a-z][.)]|check (all|one)", re.I)
STOP = set("the a an of for to and or with in on at by is are be this that as "
           "if any all".split())


def _plausible(fid: str, guide: dict) -> str:
    dt = guide.get("data_type")
    ex = guide.get("examples")
    if ex:
        return str(ex[0])
    if dt == "date":
        return "01/15/2025"
    if dt == "currency":
        return "1234.56"
    if dt == "address":
        return "12 Main St, Portland, ME 04101"
    if dt in ("person_name", "entity_name"):
        return "Jane Q. Public"
    if dt == "phone":
        return "207-555-0142"
    if dt == "email":
        return "jane@example.com"
    if dt == "docket_number":
        return "2025-1234-AB"
    if "county" in fid.lower():
        return "CUMBERLAND"
    return "N/A"


def prompts(doc: fitz.Document):
    out = []
    for pno, page in enumerate(doc):
        for blk in page.get_text("dict")["blocks"]:
            for line in blk.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = line["bbox"]
                # "asks" = a prompt that should have a fillable field: ends with
                # ':'/'?', OR a short numbered/lettered label. A numbered/lettered
                # line that is a long sentence is a narrative clause (e.g. a
                # 'under penalty of perjury (a) ...' verification statement), not a
                # blank, so it does not need a field.
                marker = re.match(r"^\(?[0-9]{1,2}[.)]|^\(?[a-z][.)]", text, re.I)
                ends = text.rstrip().endswith((":", "?"))
                short = len(text.split()) <= 8
                asks = bool(ends or re.search(r"check (all|one)", text, re.I)
                            or (marker and short))
                out.append({"page": pno, "text": text, "bbox": [x0, y0, x1, y1],
                            "asks": asks})
    return out


def _toks(s: str):
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in STOP and len(w) > 2}


def qa(form_id: str, out_dir: pathlib.Path) -> dict:
    pkg = ROOT / "repo" / "forms" / form_id
    geom = json.loads((pkg / "fill_geometry.json").read_text())["fields"]
    schema = {f["field_id"]: f for f in json.loads((pkg / "schema.json").read_text())["fields"]}
    guide = json.loads((pkg / "value_guide.json").read_text())["fields"]
    align = _load_alignment(form_id, ROOT)
    doc = fitz.open(str(fetch_source(form_id)))
    P = prompts(doc)

    # widgets list
    widgets = []
    for fid, spec in geom.items():
        for i, w in enumerate(spec.get("widgets") or []):
            widgets.append({"fid": fid, "page": w["page"], "rect": w["rect"],
                            "kind": spec.get("type")})
        for o in spec.get("options") or []:
            widgets.append({"fid": fid, "page": o["page"], "rect": o["rect"],
                            "kind": "choice", "opt": o.get("value")})

    def near(prompt):
        px0, py0, px1, py1 = prompt["bbox"]
        cy = (py0 + py1) / 2
        hits = []
        for w in widgets:
            if w["page"] != prompt["page"]:
                continue
            wx0, wy0, wx1, wy1 = w["rect"]
            wcy = (wy0 + wy1) / 2
            same_line = abs(wcy - cy) < 6 and wx0 >= px0 - 4
            below = 0 < wy0 - py1 < 30 and wx1 > px0 and wx0 < px1 + 80
            if same_line or below:
                hits.append(w["fid"])
        return sorted(set(hits))

    gaps, mapped_fields = [], set()
    for pr in P:
        ans = near(pr)
        for fid in ans:
            mapped_fields.add(fid)
        if pr["asks"] and not ans:
            gaps.append({"page": pr["page"], "prompt": pr["text"][:80]})
        pr["fields"] = ans

    # orphans: widget-bearing fields never mapped to any prompt. Exclude
    # repeating-table rows (slot members / `_<n>_` ids), which answer one shared
    # column-header prompt, not a per-row prompt.
    slot_re = re.compile(r"_\d+(_|$)")
    has_widget = {fid for fid, s in geom.items() if s.get("widgets") or s.get("options")}
    orphans = sorted(
        fid for fid in has_widget - mapped_fields
        if not slot_re.search(fid)
        and schema.get(fid, {}).get("subcategory") != "repeating_slot")

    # semantic: a field mapped to a real question whose guide label shares no
    # keywords with the question text (possible wrong field for the prompt).
    # Restricted to "asks" prompts so headers/titles don't create noise.
    semantic = []
    for pr in P:
        if not pr["asks"]:
            continue
        ptok = _toks(pr["text"])
        if len(ptok) < 2:
            continue
        for fid in pr.get("fields", []):
            lab = guide.get(fid, {}).get("label", fid)
            if _toks(lab) and not (_toks(lab) & ptok):
                semantic.append({"field": fid, "label": lab, "prompt": pr["text"][:60]})

    # QA fill render (never blank)
    _strip_widgets(doc)
    for fid, spec in geom.items():
        if spec.get("geometry_source", "").startswith(("suppressed", "court_completed")):
            continue
        g = guide.get(fid, {})
        for i, w in enumerate(spec.get("widgets") or []):
            name = fid if i == 0 else f"{fid}__{i}"
            if spec.get("type") == "enabler":
                _add_checkbox(doc[w["page"]], w["rect"], name)
                continue
            val = _value_for_printed_context(doc[w["page"]], w["rect"], fid,
                                             _plausible(fid, g))
            _add_text(doc[w["page"]], w["rect"], name, val,
                      _ALIGN_CONST.get(align.get(fid)),
                      force_multiline=bool(w.get("multiline")))
        opts = spec.get("options") or []
        if opts:  # check the first option only
            o = opts[0]
            _add_checkbox(doc[o["page"]], o["rect"], f"{fid}__{o.get('value') or 0}")

    out_dir.mkdir(parents=True, exist_ok=True)
    pngs = []
    for i, page in enumerate(doc):
        p = out_dir / f"{form_id.replace('/', '_')}_qa_p{i + 1}.png"
        page.get_pixmap(dpi=160).save(str(p))
        pngs.append(str(p))
    doc.close()

    report = {"form_id": form_id, "n_prompts": len(P),
              "n_asks": sum(1 for p in P if p["asks"]),
              "gaps": gaps, "orphans": orphans, "semantic_mismatches": semantic,
              "pngs": pngs}
    (out_dir / f"{form_id.replace('/', '_')}_qa.json").write_text(
        json.dumps(report, indent=2))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--out-dir", default="/tmp/qa")
    args = ap.parse_args()
    r = qa(args.form, pathlib.Path(args.out_dir))
    print(json.dumps({k: v for k, v in r.items() if k != "pngs"}, indent=2))
    print("\n".join(r["pngs"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
