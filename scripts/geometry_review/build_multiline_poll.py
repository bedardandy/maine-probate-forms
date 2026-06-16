#!/usr/bin/env python3
"""Build the multiline-below voting round from the classified candidates.

Each unit shows option A = the CURRENT single-line widget (red) and option B =
the PROPOSED margin-wide paragraph box seated below the prompt (blue), both filled
with a realistic long answer through the real fill pipeline -- so A visibly
overflows/clips and B wraps. The reviewer confirms B, keeps A, or writes Other.

Scope: single-widget (n_widgets==1) narrative fields the fleet judged
open-ended; multi-widget fields already have a continuation chain and are out of
scope. Units agreed by both models sort first; one-model-only are flagged.

    GEOM_LLM=... python3 scripts/geometry_review/classify_multiline.py --fits-only --name multiline_qwen.jsonl
    GEOM_LLM=... python3 scripts/geometry_review/classify_multiline.py --fits-only --name multiline_gemma.jsonl
    python3 scripts/geometry_review/build_multiline_poll.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools.fetch import fetch_source                       # noqa: E402
from tools.fill_pdf import _ALIGN_CONST, _load_alignment    # noqa: E402
from scripts.geometry_review.build_poll import render_option  # noqa: E402

# realistic long answers so the single-line box overflows and the box wraps
SAMPLE = {
    "description": ("1998 Ford F-150 pickup (VIN 1FTZX1762WKA12345), a 2019 "
                    "Bayliner VR5 boat with trailer, John Deere 1025R tractor, "
                    "and assorted household furnishings valued at ~$42,000."),
    "list": ("Margaret L. Walsh, 82 Falmouth Foreside Way, Falmouth ME 04105; "
             "Thomas R. Walsh, 14 Ocean Ave, Portland ME 04101; Eastern Trust "
             "Co., 1 Monument Sq, Portland ME 04101."),
    "reasons": ("The petitioner has relocated out of state and can no longer "
                "fulfill the required in-person duties; a successor closer to "
                "the protected person is in the ward's best interest."),
}


def sample_for(kind: str) -> str:
    if kind in ("list",):
        return SAMPLE["list"]
    if kind in ("explanation_or_reasons", "description"):
        return SAMPLE["description" if kind == "description" else "reasons"]
    return SAMPLE["description"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    args = ap.parse_args()
    out = args.out

    cand = {(o["form"], o["field"]): o for o in
            (json.loads(l) for l in (out / "multiline_candidates.jsonl").open())}
    q = {(o["form"], o["field"]): o["shape"] for o in
         (json.loads(l) for l in (out / "multiline_qwen.jsonl").open())}
    g = {(o["form"], o["field"]): o["shape"] for o in
         (json.loads(l) for l in (out / "multiline_gemma.jsonl").open())}

    picks, split = [], []
    for k, o in cand.items():
        if o.get("n_widgets") != 1 or not o.get("fits_box"):
            continue
        qp = bool(q.get(k, {}).get("expects_paragraph"))
        gp = bool(g.get(k, {}).get("expects_paragraph"))
        if qp and gp:
            picks.append((k, o, qp, gp))           # both models -> poll
        elif qp or gp:
            split.append((k, o, qp, gp))           # fleet split -> human review, not a vote
    picks.sort(key=lambda t: t[0])

    # a fleet split means the field is ambiguous (often a mismapped table cell,
    # e.g. AF-104 reason_not_contacting). Record for the followups doc; don't poll.
    rev = out / "multiline_review_needed.jsonl"
    rev.write_text("".join(json.dumps(
        {"form": k[0], "field": k[1], "qwen_paragraph": qp, "gemma_paragraph": gp,
         "multi_col": o.get("multi_col"), "prompt": o.get("prompt"),
         "kind": (q.get(k, {}).get("kind"), g.get(k, {}).get("kind"))}) + "\n"
        for k, o, qp, gp in sorted(split, key=lambda t: t[0])))
    if split:
        print(f"fleet-split (review, not polled): {len(split)} -> {rev.name}")
        for k, o, qp, gp in split:
            print(f"  {k[0]:10} {k[1]:34} qwen={qp} gemma={gp}")

    crops = out / "poll_crops"
    crops.mkdir(parents=True, exist_ok=True)
    units = []
    for (form, field), o, qp, gp in picks:
        src = fetch_source(form)
        al = _ALIGN_CONST.get(_load_alignment(form, ROOT).get(field))
        pg = o["page"]
        cur = fitz.Rect(o["current_rect"])
        box = fitz.Rect(o["proposed_rect"])
        kind = (q.get((form, field), {}).get("kind")
                or g.get((form, field), {}).get("kind") or "description")
        val = sample_for(kind)
        win = fitz.Rect(min(cur.x0, box.x0) - 30, min(cur.y0, box.y0) - 30,
                        max(cur.x1, box.x1) + 30, max(cur.y1, box.y1) + 16)
        uid = hashlib.md5(f"mlb|{form}|{field}|0".encode()).hexdigest()[:10]
        a_png = crops / f"{uid}_A.png"
        b_png = crops / f"{uid}_B.png"
        render_option(src, pg, list(cur), val, al, win, a_png, (220, 0, 0))
        render_option(src, pg, list(box), val, None, win, b_png, (0, 90, 220))
        agree = "both models" if (qp and gp) else ("Qwen only" if qp else "gemma only")
        units.append({
            "id": uid, "form": form, "field": field, "widget_idx": "0",
            "page": pg, "value_shown": val[:48] + "…",
            "via": "multiline-below", "signal": f"narrative open-text ({agree})",
            "detail": (f"prompt: {o.get('prompt')!r} · current box "
                       f"{round(cur.width)}×{round(cur.height)}pt · "
                       f"room below {o['room_below']}pt · kind={kind}"),
            "options": [
                {"key": "A", "label": "keep current single-line box",
                 "rect": [round(v, 1) for v in cur],
                 "crop": f"poll_crops/{a_png.name}"},
                {"key": "B", "label": "make a multi-line box below the prompt",
                 "rect": [round(v, 1) for v in box],
                 "crop": f"poll_crops/{b_png.name}"},
            ]})
        print(f"{form:10} {field:34} [{agree}]")

    # preserve the prior poll, then install this round
    pd = out / "poll_data.json"
    if pd.exists():
        (out / "poll_data_prev.json").write_text(pd.read_text())
    pd.write_text(json.dumps(units, indent=1))
    print(f"\nwrote {pd} — {len(units)} multiline-below units "
          f"(prev poll saved to poll_data_prev.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
