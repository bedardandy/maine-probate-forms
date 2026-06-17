#!/usr/bin/env python3
"""Seed the multiline-below review: narrative open-text fields stuck in a 1-line box.

The poll surfaced a reviewer rule: "a non-underlined open-ended answer generally
needs a large multi-line text box underneath the prompt, spanning the margins"
(DE-403 surety descriptions, MISC-101 service_recipients, PP-405 desc_personal
property, AD-008 *_expenses_details).

Calibration finding: the reviewer's canonical cases are NOT the sweep's
`no_line_support` set -- they are single-line widgets sitting on a short
underline. What they share is `fill_strategy.source == "llm_over_narrative"`
(the marker for free text the agent COMPOSES) while the widget is only one line
tall. So the seed is semantic, not geometric:

  seed   fill_strategy.source == llm_over_narrative  AND  every widget single-
         line (<=16pt)  AND  field_id is not an obvious short fact
         (date/age/name/number/phone/email/zip/county/docket/signature).

Narrative-sourced SHORT facts exist too (date_of_death, age) -- those are
dropped by the closed-fact regex here, and the remainder is semantically
classified (paragraph vs short value) by classify_multiline.py before the poll.

Geometry per candidate: prompt (printed ':' line on/above the rect), room_below
(whitespace to the next obstacle across the body margins -- a printed line OR a
neighboring form widget), and -- when a box fits (room_below >= MIN_BOX_H) -- a
proposed margin-wide paragraph rect seated below the prompt. fill_pdf wraps any
rect taller than 24pt, so the box is real.

A QA pass (collision of the proposed box against neighboring widgets) showed the
first cut over-fired: 17/22 boxes ran through another field. Most open-answer
fields already have a sibling box (a line+box pair, e.g. PP-405 desc_1 + the big
desc_2 box) or are table cells (GS-014 funds_received grid) or have a signature
block right below. So room_below counts other-field widgets as obstacles, and a
final overlap test drops any candidate whose box still touches a widget
(drop_reason=widget_collision) -- those are STRUCTURAL, not clean box-below.

    python3 scripts/geometry_review/detect_multiline_below.py            # triage
    python3 scripts/geometry_review/detect_multiline_below.py --emit     # + jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools.fetch import fetch_source                       # noqa: E402
from scripts.geometry_review.sweep import page_features     # noqa: E402

MIN_BOX_H = 26.0       # a paragraph box must clear fill_pdf's 24pt multiline gate
PROMPT_DROP = 3.0
EDGE_PAD = 2.0
H_SINGLE = 16.0
COMFORT_PAD = 12.0     # keep the box this comfortably above the next question
BOTTOM_MARGIN = 72.0   # never run a box past the 1" page bottom margin
COL_GAP = 14.0         # gap between symmetric side-by-side columns
TABLE_REPEAT = 2       # >= this many widgets stacked in the column == a table

CLOSED = re.compile(
    r"(^|_)(date|age|dob|birth|death|name|number|no|county|docket|year|zip|"
    r"amount|phone|email|fax|bar|ssn|signature|sign|initials|day|month|"
    r"fee|fees|rate|hourly|cost|rent|salary|wage)(_|$)",
    re.I)


def word_cover(rect, words) -> float:
    segs = []
    for wr, t in words:
        if min(rect.y1, wr.y1) - max(rect.y0, wr.y0) <= 0.4 * wr.height:
            continue
        a, b = max(rect.x0, wr.x0), min(rect.x1, wr.x1)
        if b > a:
            segs.append((a, b))
    if not segs:
        return 0.0
    segs.sort()
    cov, cur0, cur1 = 0.0, *segs[0]
    for a, b in segs[1:]:
        if a > cur1:
            cov += cur1 - cur0
            cur0, cur1 = a, b
        else:
            cur1 = max(cur1, b)
    cov += cur1 - cur0
    return round(cov / max(1.0, rect.width), 2)


def body_margins(words, page_w):
    xs0 = sorted(wr.x0 for wr, t in words)
    xs1 = sorted(wr.x1 for wr, t in words)
    if not xs0:
        return 54.0, page_w - 54.0
    return xs0[max(0, len(xs0) // 20)], xs1[min(len(xs1) - 1, int(len(xs1) * 0.95))]


def find_prompt(rect, words):
    line_band, above_band = [], []
    for wr, t in words:
        if min(rect.y1, wr.y1) - max(rect.y0, wr.y0) > 0.3 * wr.height \
                and wr.x1 <= rect.x0 + 0.6 * rect.width:
            line_band.append((wr, t))
        elif rect.y0 - 22 <= wr.y1 <= rect.y0 + 1 \
                and min(rect.x1, wr.x1) - max(rect.x0 - 40, wr.x0) > 2:
            above_band.append((wr, t))
    for band in (line_band, above_band):
        if band:
            band.sort(key=lambda p: p[0].x0)
            return (" ".join(t for _, t in band)[-60:],
                    max(wr.y1 for wr, _ in band))
    return None


def room_below(rect, words, left, right, page_h, obstacles=()):
    """Distance from rect bottom to the nearest obstacle below within [left,right].

    Obstacles are printed words AND neighboring form widgets -- a margin-wide box
    must clear both. (Same-level widgets that start at/above rect.y1 are handled
    by the final overlap test, not here.)
    """
    nxt = page_h - 36.0
    for wr, t in words:
        if wr.y0 <= rect.y1 + 1:
            continue
        if min(right, wr.x1) - max(left, wr.x0) > 4:
            nxt = min(nxt, wr.y0)
    for o in obstacles:
        if o.y0 <= rect.y1 + 1:
            continue
        if min(right, o.x1) - max(left, o.x0) > 4:
            nxt = min(nxt, o.y0)
    return round(nxt - rect.y1, 1)


def proposed_box(rect, prompt, left, right, room, page_h):
    """Seat the box below the prompt and run it DOWN to the room available.

    Per the DE-403 vote, an open-ended answer box should extend vertically to
    just-comfortably-above the next question, or to the 1" bottom margin --
    whichever comes first -- not clamp to a fixed ~4-line height. `room` is the
    gap from rect.y1 to the next obstacle, so the next obstacle sits at
    rect.y1 + room; stop COMFORT_PAD short of it."""
    top = max((prompt[1] if prompt else rect.y1) + PROMPT_DROP, rect.y0)
    bottom = min((rect.y1 + room) - COMFORT_PAD, page_h - BOTTOM_MARGIN)
    bottom = max(bottom, top + MIN_BOX_H)
    return [round(left + EDGE_PAD, 1), round(top, 1),
            round(right - EDGE_PAD, 1), round(bottom, 1)]


def all_field_widgets(form):
    """page -> [(field_id, Rect)] for every widget on the form."""
    g = json.loads((ROOT / "repo" / "forms" / form / "fill_geometry.json")
                   .read_text())["fields"]
    out = collections.defaultdict(list)
    for fid, spec in g.items():
        for wd in (spec.get("widgets") or []):
            out[wd["page"]].append((fid, fitz.Rect(wd["rect"])))
    return out


def overlaps(a, b):
    """True if rects share more than 2pt in both axes (a real collision)."""
    return (min(a.x1, b.x1) - max(a.x0, b.x0) > 2
            and min(a.y1, b.y1) - max(a.y0, b.y0) > 2)


def column_bounds(rect, others, body_left, body_right):
    """[col_left, col_right] for the candidate's column on a multi-column row.

    Per the DE-403 vote, side-by-side answer boxes (the two surety descriptions)
    should be *symmetric* -- each flush to its page margin with a small gap
    between, not sized to the (off-centre) underlying widget. So when N widgets
    share the candidate's row, divide the body width into N equal columns with a
    COL_GAP gutter and give the candidate its slot (by left-to-right rank).
    Single-column fields keep the full body margins. Returns
    (left, right, multi_col)."""
    row = [rect]
    for o in others:
        if min(rect.y1, o.y1) - max(rect.y0, o.y0) > 0.5 * min(rect.height,
                                                               o.height):
            row.append(o)
    if len(row) <= 1:
        return body_left, body_right, False
    row.sort(key=lambda r: r.x0)
    k = min(range(len(row)), key=lambda i: abs(row[i].x0 - rect.x0))
    n = len(row)
    colw = (body_right - body_left - (n - 1) * COL_GAP) / n
    left = body_left + k * (colw + COL_GAP)
    return left, left + colw, True


def is_table_column(rect, others):
    """True if >= TABLE_REPEAT other widgets share this rect's x-span on other
    rows -- an aligned grid column (GS-014 funds_received_*_N), where a margin-
    wide box-below makes no sense. Alignment (not mere x-overlap) is the signal,
    so a single-column field whose column spans the page isn't misread."""
    n = 0
    for o in others:
        if (abs(o.x0 - rect.x0) < 8 and abs(o.x1 - rect.x1) < 8
                and abs((o.y0 + o.y1) / 2 - (rect.y0 + rect.y1) / 2)
                > rect.height):
            n += 1
    return n >= TABLE_REPEAT


def narrative_singleline_fields(form):
    """field_id -> (label, [widgets]) for narrative single-line non-shortfact."""
    s = json.loads((ROOT / "repo" / "forms" / form / "schema.json").read_text())
    g = json.loads((ROOT / "repo" / "forms" / form / "fill_geometry.json")
                   .read_text())["fields"]
    out = {}
    for f in s["fields"]:
        if (f.get("fill_strategy") or {}).get("source") != "llm_over_narrative":
            continue
        fid = f["field_id"]
        spec = g.get(fid)
        if not spec or not spec.get("widgets"):
            continue
        if any(fitz.Rect(w["rect"]).height > H_SINGLE for w in spec["widgets"]):
            continue
        if CLOSED.search(fid):
            continue
        out[fid] = (f.get("label", ""), spec["widgets"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    args = ap.parse_args()

    forms = sorted(d.name for d in (ROOT / "repo" / "forms").iterdir()
                   if (d / "schema.json").exists())
    rows = []
    for form in forms:
        fields = narrative_singleline_fields(form)
        if not fields:
            continue
        try:
            doc = fitz.open(str(fetch_source(form)))
        except Exception:
            continue
        feats = {p: page_features(doc[p]) for p in range(doc.page_count)}
        dims = {p: (doc[p].rect.width, doc[p].rect.height)
                for p in range(doc.page_count)}
        doc.close()
        allw = all_field_widgets(form)
        for fid, (label, widgets) in fields.items():
            w = widgets[0]
            r = fitz.Rect(w["rect"]); pg = w["page"]
            words = feats[pg]["words"]; pw, ph = dims[pg]
            others = [wr for ofid, wr in allw.get(pg, []) if ofid != fid]
            bl, br = body_margins(words, pw)
            left, right, multi = column_bounds(r, others, bl, br)
            prompt = find_prompt(r, words)
            rb = room_below(r, words, left, right, ph, others)
            rec = {"form": form, "field": fid, "label": label,
                   "widget_idx": 0, "page": pg, "n_widgets": len(widgets),
                   "current_rect": [round(v, 1) for v in r],
                   "prompt": prompt[0] if prompt else None,
                   "room_below": rb, "word_cover": word_cover(r, words),
                   "multi_col": multi, "fits_box": rb >= MIN_BOX_H}
            if rec["fits_box"] and is_table_column(r, others):
                rec["fits_box"] = False
                rec["drop_reason"] = "table_cell"
            elif rec["fits_box"]:
                box = proposed_box(r, prompt, left, right, rb, ph)
                # final guard: a clean box-below touches no other widget. Same-
                # level overlaps (sibling box) land here -> structural.
                if any(overlaps(fitz.Rect(box), wr) for wr in others):
                    rec["fits_box"] = False
                    rec["drop_reason"] = "widget_collision"
                else:
                    rec["proposed_rect"] = box
            rows.append(rec)

    # Symmetry (DE-403 vote): side-by-side answer boxes sharing a row should be
    # the same shape, so snap each multi-col group to a common top and bottom
    # (the shallowest, so neither box runs into anything below it).
    groups = collections.defaultdict(list)
    for r in rows:
        if r.get("proposed_rect") and r.get("multi_col"):
            groups[(r["form"], r["page"], round(r["current_rect"][1]))].append(r)
    for g in groups.values():
        if len(g) > 1:
            top = max(x["proposed_rect"][1] for x in g)
            bot = min(x["proposed_rect"][3] for x in g)
            for x in g:
                x["proposed_rect"][1] = top
                x["proposed_rect"][3] = bot

    fits = [r for r in rows if r["fits_box"]]
    collide = [r for r in rows if r.get("drop_reason") == "widget_collision"]
    table = [r for r in rows if r.get("drop_reason") == "table_cell"]
    mc = sum(1 for r in fits if r.get("multi_col"))
    print(f"narrative single-line non-shortfact fields: {len(rows)}")
    print(f"  clean box below (>= {MIN_BOX_H:.0f}pt, no collision): {len(fits)} "
          f"({mc} column-width on multi-col rows, {len(fits)-mc} margin-wide)")
    print(f"  dropped -- table/grid cell: {len(table)}")
    print(f"  dropped -- box would hit a sibling widget (STRUCTURAL): {len(collide)}")
    print(f"  no room below (overflow / 'see Exhibit A' class): "
          f"{len(rows)-len(fits)-len(collide)-len(table)}")
    byform = collections.Counter(r["form"] for r in fits)
    print("  top forms (fits_box):", dict(byform.most_common(8)))
    if args.emit:
        p = args.out / "multiline_candidates.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"\nwrote {p} ({len(rows)} rows; {len(fits)} fit a box)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
