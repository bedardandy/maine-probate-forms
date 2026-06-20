#!/usr/bin/env python3
"""Snap single-line text widgets onto their printed blank (rule or underscore run).

Replicates the granular hand-alignment pattern: a fill field should sit on the
printed blank it answers -- left edge at the blank start (clear of any label),
right edge at the blank end, baseline just above the line. Blanks are detected
two ways: drawn horizontal rules, and runs of printed underscores in the text
layer (which most of these forms use). Dry-run by default.

    python3 tools/snap_to_blank.py --form AF-103            # dry-run report
    python3 tools/snap_to_blank.py --form AF-103 --apply
"""
from __future__ import annotations
import argparse, json, pathlib, re, sys
import fitz
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools")); from fetch import fetch_source  # noqa
sys.path.insert(0, str(ROOT / "scripts")); from audit_form_geometry import horizontal_rules  # noqa

US_RE = re.compile(r"^[_—….]{3,}$")  # underscores / em-dash / dots run
LEAD_GAP = 3.5      # min space between a label word and the value
BASELINE_DROP = 1.0  # box bottom this far below the printed rule (descenders clear)

# Fields to leave alone: snapping would move them across a numbered prompt onto
# the wrong blank. DE-506 probate_estate_value (item 5) sits ambiguously between
# items 5 and 6; the baseline drop tips it onto item 6's line. Items 5/6 there
# need a dedicated per-form fix (deferred QA), not an automatic snap.
SKIP_FIELDS = {("DE-506", "probate_estate_value")}


def _underscore_runs(page):
    """Char-level runs of '_' (the printed blanks most of these forms use).

    Working at the character level -- rather than whole word tokens -- catches
    blanks fused to adjacent text ("of____", "____,", "dated____") that a
    token-level regex misses, and gives the exact x-extent of just the
    underscores (so a field stops right before a trailing comma).
    """
    runs = []
    cur = None  # [x0, x1, y_bottom]
    for blk in page.get_text("rawdict").get("blocks", []):
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    bx0, _, bx1, by1 = ch["bbox"]
                    if ch["c"] == "_":
                        if cur and abs(by1 - cur[2]) < 2 and bx0 - cur[1] < 6:
                            cur[1] = bx1
                        else:
                            if cur and cur[1] - cur[0] >= 18:
                                runs.append(tuple(cur))
                            cur = [bx0, bx1, by1]
                    else:
                        if cur and cur[1] - cur[0] >= 18:
                            runs.append(tuple(cur))
                        cur = None
                if cur and cur[1] - cur[0] >= 18:
                    runs.append(tuple(cur))
                cur = None
    return runs


def blanks(page):
    """List of (x0, x1, y_line) printed blanks on the page."""
    out = [(x0, x1, y) for x0, x1, y in horizontal_rules(page)]
    out.extend(_underscore_runs(page))
    return out


def printed_words(page):
    """Non-blank printed words: (x0, x1, y_mid, text). Used to keep a small gap
    between a label and the value when the blank butts right against the label."""
    out = []
    for w in page.get_text("words"):
        t = w[4].strip()
        if not t or US_RE.match(t) or set(t) <= set("_"):
            continue
        out.append((w[0], w[2], (w[1] + w[3]) / 2, t))
    return out


def snap(form_id, apply=False):
    pkg = ROOT / "repo" / "forms" / form_id
    geom = json.loads((pkg / "fill_geometry.json").read_text())
    schema = {f["field_id"]: f for f in json.loads((pkg / "schema.json").read_text())["fields"]}
    doc = fitz.open(str(fetch_source(form_id)))
    bl = {i: blanks(doc[i]) for i in range(doc.page_count)}
    pw = {i: printed_words(doc[i]) for i in range(doc.page_count)}
    # every other field's widget rects, per page -- to avoid snapping a field on
    # top of a neighbour (collisions the geometry audit gates on).
    others = {}
    for ofid, ospec in geom["fields"].items():
        for ow in ospec.get("widgets", []) or []:
            others.setdefault(ow["page"], []).append((ofid, ow["rect"]))

    def _collides(nr, page, self_fid):
        for ofid, orr in others.get(page, []):
            if ofid == self_fid:
                continue
            ix = min(nr[2], orr[2]) - max(nr[0], orr[0])
            iy = min(nr[3], orr[3]) - max(nr[1], orr[1])
            if ix > 2 and iy > 2:
                return True
        return False

    def _target(fid, spec, w):
        """Snap target rect for one widget, or None. No collision check here."""
        if (form_id, fid) in SKIP_FIELDS:
            return None
        if (spec.get("options") or spec.get("type") == "enabler"
                or spec.get("geometry_source", "").startswith(("suppressed", "court"))
                or schema.get(fid, {}).get("data_type") == "signature"
                or len(spec.get("widgets", []) or []) > 1):
            return None
        r = w["rect"]; h = r[3] - r[1]
        if h > 20:
            return None
        cands = []
        row_overlaps = 0  # distinct blanks underlying this widget on the row
        for x0, x1, y in bl[w["page"]]:
            if x1 - x0 < 20:
                continue
            ov = min(r[2], x1) - max(r[0], x0)
            if abs(y - r[3]) < 5 and ov > 25:  # blanks on the field's own line
                row_overlaps += 1
            if abs(y - r[3]) < 9 and ov > 0.4 * min(r[2] - r[0], x1 - x0):
                cands.append((abs(y - r[3]), x0, x1, y))
        if not cands or row_overlaps > 1:
            # multi-slot sentence (e.g. "on ___, ___, at ___"): one field spans
            # several inline blanks -- snapping would truncate it. Leave as-is.
            return None
        _, bx0, bx1, by = min(cands)
        # don't aggressively shrink onto a much shorter blank (multi-slot / answer
        # area) -- leave those for manual review.
        if (bx1 - bx0) < 0.75 * (r[2] - r[0]):
            return None
        # Leading inset: when the blank butts right against the label word
        # ("of____", "dated____"), keep a small gap so the value doesn't collide
        # with the printed word.
        lx = bx0
        for _ox0, ox1, oy, _t in pw[w["page"]]:
            if abs(oy - by) < 6 and ox1 <= bx0 + 2 and bx0 - ox1 < LEAD_GAP:
                lx = max(lx, ox1 + LEAD_GAP)
        # Baseline: drop the box so the value sits right on the rule (looks
        # underlined) while descenders (g/y/p) still clear it -- the value is
        # vertically centred in the box, so descender bottom ~= box_bottom -
        # (h - fontsize)/2; place box_bottom a hair below the rule.
        y1 = by + BASELINE_DROP
        new = [round(lx, 1), round(y1 - h, 1), round(bx1, 1), round(y1, 1)]
        if abs(new[0] - r[0]) < 1 and abs(new[2] - r[2]) < 1 and abs(new[3] - r[3]) < 1:
            return None  # no-op (already on the blank)
        return new

    # Pass 1: compute every widget's snap target.
    targets = {}  # id(w) -> (page, new_rect)
    for fid, spec in geom["fields"].items():
        for w in spec.get("widgets", []) or []:
            t = _target(fid, spec, w)
            if t is not None:
                targets[id(w)] = (w["page"], t)
    # Effective rect of every widget once snaps land (target if it has one).
    eff = {}
    for fid, spec in geom["fields"].items():
        for w in spec.get("widgets", []) or []:
            eff.setdefault(w["page"], []).append(
                (fid, targets.get(id(w), (None, w["rect"]))[1]))

    def _eff_collides(nr, page, self_fid):
        for ofid, orr in eff.get(page, []):
            if ofid == self_fid:
                continue
            ix = min(nr[2], orr[2]) - max(nr[0], orr[0])
            iy = min(nr[3], orr[3]) - max(nr[1], orr[1])
            if ix > 2 and iy > 2:
                return True
        return False

    # Pass 2: apply targets that don't introduce a new collision once everyone
    # has moved (a neighbour the field overlaps may itself be snapping away).
    changes = []
    for fid, spec in geom["fields"].items():
        for w in spec.get("widgets", []) or []:
            if id(w) not in targets:
                continue
            new = targets[id(w)][1]
            if _eff_collides(new, w["page"], fid) and not _collides(w["rect"], w["page"], fid):
                continue
            changes.append((fid, w["page"], [round(c, 1) for c in w["rect"]], new))
            if apply:
                w["rect"] = new
    doc.close()
    if apply:
        (pkg / "fill_geometry.json").write_text(json.dumps(geom, indent=1, ensure_ascii=False))
    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    for c in snap(a.form, a.apply):
        print(f"  {c[0]} p{c[1]} {c[2]} -> {c[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
