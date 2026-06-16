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
(whitespace to the next printed line across the body margins), and -- when a box
fits (room_below >= MIN_BOX_H) -- a proposed margin-wide paragraph rect seated
below the prompt. fill_pdf wraps any rect taller than 24pt, so the box is real.

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
DESIRED_H = 48.0       # ~4 lines at 10pt; clamped to the room actually available
PROMPT_DROP = 3.0
EDGE_PAD = 2.0
H_SINGLE = 16.0

CLOSED = re.compile(
    r"(^|_)(date|age|dob|birth|death|name|number|no|county|docket|year|zip|"
    r"amount|phone|email|fax|bar|ssn|signature|sign|initials|day|month)(_|$)",
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


def room_below(rect, words, left, right, page_h):
    nxt = page_h - 36.0
    for wr, t in words:
        if wr.y0 <= rect.y1 + 1:
            continue
        if min(right, wr.x1) - max(left, wr.x0) > 4:
            nxt = min(nxt, wr.y0)
    return round(nxt - rect.y1, 1)


def proposed_box(rect, prompt, left, right, room):
    top = max((prompt[1] if prompt else rect.y1) + PROMPT_DROP, rect.y0)
    bottom = top + max(MIN_BOX_H, min(DESIRED_H, room - 4))
    return [round(left + EDGE_PAD, 1), round(top, 1),
            round(right - EDGE_PAD, 1), round(bottom, 1)]


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
        for fid, (label, widgets) in fields.items():
            w = widgets[0]
            r = fitz.Rect(w["rect"]); pg = w["page"]
            words = feats[pg]["words"]; pw, ph = dims[pg]
            left, right = body_margins(words, pw)
            prompt = find_prompt(r, words)
            rb = room_below(r, words, left, right, ph)
            rec = {"form": form, "field": fid, "label": label,
                   "widget_idx": 0, "page": pg, "n_widgets": len(widgets),
                   "current_rect": [round(v, 1) for v in r],
                   "prompt": prompt[0] if prompt else None,
                   "room_below": rb, "word_cover": word_cover(r, words),
                   "fits_box": rb >= MIN_BOX_H}
            if rec["fits_box"]:
                rec["proposed_rect"] = proposed_box(r, prompt, left, right, rb)
            rows.append(rec)

    fits = [r for r in rows if r["fits_box"]]
    print(f"narrative single-line non-shortfact fields: {len(rows)}")
    print(f"  with room for a box below (>= {MIN_BOX_H:.0f}pt): {len(fits)}")
    print(f"  no room below (overflow / 'see Exhibit A' class): {len(rows)-len(fits)}")
    byform = collections.Counter(r["form"] for r in fits)
    print("  top forms (fits_box):", dict(byform.most_common(8)))
    if args.emit:
        p = args.out / "multiline_candidates.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"\nwrote {p} ({len(rows)} rows; {len(fits)} fit a box)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
