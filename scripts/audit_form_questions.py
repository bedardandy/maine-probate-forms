#!/usr/bin/env python3
"""Question / field-quality auditor for Maine probate form packages.

Goes past "is the rect in the right place" (scripts/audit_form_geometry.py) to
ask whether the *field model* of each form is sound. Per form it flags:

  uncovered_question        a numbered printed prompt whose answer area has a
                            blank/rule but no fill widget (question with no field)
  text_on_signature_line    a text widget sitting on a printed "Signature" rule
                            (should be wet-ink / left blank)
  symbol_splits_underline   one widget spanning an interior printed $ , . or a
                            "20" year stub — should be separate fields (e.g.
                            day / month / year, or dollars / cents)
  duplicate_widget_name     two widgets that would receive the SAME AcroForm
                            name and thus overwrite each other
  alignment_suggestion      declared justification differs from the layout-based
                            recommendation (currency->right, short mid-sentence
                            blank->center, else left)
  underline_buffer          a widget edge bleeds into an adjacent printed word
                            with no gap (e.g. "____County") — wants a small buffer

Output: JSON ({summary, findings}) to --out, plus a one-line summary to stdout.
Pure read-only analysis; never edits geometry.
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

sys.path.insert(0, str(ROOT / "scripts"))
from audit_form_geometry import horizontal_rules  # noqa: E402

CURRENCY_RE = re.compile(
    r"(?:^|_)(?:value|val|amount|amt|fee|fees|penal_sum|penal|balance|income|"
    r"expense|expenses|salary|wage|wages|disbursement|sum_numeric|gross_value|"
    r"net_value|tax)(?:$|_)", re.I)
NUMBERED_RE = re.compile(r"^\(?(\d{1,2})[.)]$")
SIGNATURE_RE = re.compile(r"signature", re.I)
SPLIT_SYMBOLS = {"$", ","}
YEAR_STUB_RE = re.compile(r"^20_*$|^_+20")


def _rows(words):
    return words  # (x0,y0,x1,y1,text,block,line,word_no)


def _same_row(a_cy, w, tol=4.0):
    return abs((w[1] + w[3]) / 2 - a_cy) <= tol


def audit_form(form_id: str, geom: dict, doc: fitz.Document) -> list[dict]:
    out = []
    schema_fields = {}
    spkg = ROOT / "repo" / "forms" / form_id / "schema.json"
    if spkg.exists():
        schema_fields = {f["field_id"]: f
                         for f in json.loads(spkg.read_text()).get("fields", [])}
    align_path = ROOT / "catalog" / "field_alignment.json"
    declared_align = {}
    if align_path.exists():
        declared_align = json.loads(align_path.read_text()).get(
            "forms", {}).get(form_id, {})

    def f(code, sev, **d):
        out.append({"form_id": form_id, "code": code, "severity": sev, **d})

    words_by_pg = {i: doc[i].get_text("words") for i in range(doc.page_count)}
    rules_by_pg = {i: horizontal_rules(doc[i]) for i in range(doc.page_count)}

    # --- collect widgets + the names fill_pdf would assign ---
    placed = []  # (name, page, rect, field_id, kind, index, dtype)
    for fid, spec in geom.get("fields", {}).items():
        dtype = schema_fields.get(fid, {}).get("data_type")
        ws = spec.get("widgets") or []
        for i, w in enumerate(ws):
            name = fid if i == 0 else f"{fid}__{i}"
            placed.append((name, w["page"], fitz.Rect(w["rect"]),
                           fid, spec.get("type"), i, dtype))

    # duplicate_widget_name
    seen = {}
    for name, pg, rect, fid, kind, idx, dtype in placed:
        key = (name, pg)
        if key in seen:
            f("duplicate_widget_name", "high", field_id=fid, name=name, page=pg)
        seen[key] = True

    for name, pg, rect, fid, kind, idx, dtype in placed:
        if kind in ("enabler",) or dtype in ("checkbox",):
            continue
        words = words_by_pg[pg]
        cy = (rect.y0 + rect.y1) / 2
        row = [w for w in words if _same_row(cy, w, 4.0)]

        # text_on_signature_line: a text widget within 22pt above a "Signature" label
        if dtype != "signature":
            for w in words:
                if SIGNATURE_RE.search(w[4]) and 0 <= w[1] - rect.y1 < 22 \
                        and rect.x0 < w[2] and rect.x1 > w[0] - 40:
                    f("text_on_signature_line", "high", field_id=fid, page=pg,
                      note=f"text widget above printed '{w[4]}'")
                    break

        # symbol_splits_underline: interior printed split symbol / year stub
        for w in row:
            wcx = (w[0] + w[2]) / 2
            if rect.x0 + 5 < wcx < rect.x1 - 5:
                tok = w[4].strip()
                if tok in SPLIT_SYMBOLS or YEAR_STUB_RE.match(tok):
                    f("symbol_splits_underline", "medium", field_id=fid, page=pg,
                      symbol=tok, note="widget spans an interior printed symbol; "
                      "consider separate fields")
                    break

        # underline_buffer: widget edge bleeds into an adjacent printed glyph
        left = [w for w in row if w[2] <= rect.x0 + 1 and rect.x0 - w[2] < 30]
        right = [w for w in row if w[0] >= rect.x1 - 1 and w[0] - rect.x1 < 30]
        if right:
            nearest = min(right, key=lambda w: w[0] - rect.x1)
            gap = nearest[0] - rect.x1
            if gap < 1.0 and nearest[4].strip() not in (".", ","):
                f("underline_buffer", "low", field_id=fid, page=pg, side="right",
                  gap=round(gap, 1), neighbor=nearest[4])
        if left:
            nearest = max(left, key=lambda w: w[2])
            gap = rect.x0 - nearest[2]
            if gap < 1.0:
                f("underline_buffer", "low", field_id=fid, page=pg, side="left",
                  gap=round(gap, 1), neighbor=nearest[4])

        # alignment_suggestion (single-line text only)
        if kind in ("text", "date", "currency") and rect.height <= 24:
            # data_type is authoritative for currency; the field-id regex
            # over-matches descriptive fields (penal_sum_words, *_expenses_details).
            is_currency = dtype == "currency"
            has_left = any(w[2] <= rect.x0 - 2 and rect.x0 - w[2] < 45 for w in row)
            has_right = any(w[0] >= rect.x1 + 2 and w[0] - rect.x1 < 45 for w in row)
            short = rect.width < 130
            if is_currency:
                want = "right"
            elif has_left and has_right and short:
                want = "center"
            else:
                want = "left"
            have = declared_align.get(fid, "left")
            # Never recommend demoting an explicit center/right back to left:
            # captions and value columns are intentionally non-left.
            if want != have and not (want == "left" and have in ("center", "right")):
                f("alignment_suggestion", "low", field_id=fid, page=pg,
                  have=have, want=want)

    # uncovered_question: numbered prompt whose answer band has a rule but no widget
    placed_rects = [(pg, rect) for _, pg, rect, *_ in placed]
    for pg in range(doc.page_count):
        words = words_by_pg[pg]
        items = [(w, NUMBERED_RE.match(w[4].strip()))
                 for w in words]
        nums = sorted([(w[1], w) for w, m in items if m], key=lambda t: t[0])
        for i, (y, w) in enumerate(nums):
            y_next = nums[i + 1][0] if i + 1 < len(nums) else doc[pg].rect.height
            band = (y - 2, y_next - 2)
            rules = [r for r in rules_by_pg[pg] if band[0] <= r[2] <= band[1]]
            if not rules:
                continue
            covered = any(p == pg and rect.y1 >= band[0] and rect.y0 <= band[1]
                          for p, rect in placed_rects)
            if not covered:
                f("uncovered_question", "medium", page=pg,
                  number=w[4].strip(), y=round(y, 1),
                  note="numbered prompt with a blank rule but no fill widget")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms")
    ap.add_argument("--out")
    args = ap.parse_args()
    forms = ([s.strip() for s in args.forms.split(",")] if args.forms else
             sorted(p.parent.name for p in
                    (ROOT / "repo" / "forms").glob("*/fill_geometry.json")))
    findings = []
    for form_id in forms:
        geom = json.loads((ROOT / "repo" / "forms" / form_id /
                           "fill_geometry.json").read_text())
        with fitz.open(str(fetch_source(form_id))) as doc:
            findings.extend(audit_form(form_id, geom, doc))
    by_code = {}
    for x in findings:
        by_code[x["code"]] = by_code.get(x["code"], 0) + 1
    report = {"forms_audited": len(forms), "finding_count": len(findings),
              "by_code": dict(sorted(by_code.items())), "findings": findings}
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"forms_audited": len(forms),
                      "finding_count": len(findings),
                      "by_code": report["by_code"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
