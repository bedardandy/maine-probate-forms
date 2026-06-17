#!/usr/bin/env python3
"""Build the *follow-up* decision poll — the geometry-review calls that could
NOT be turned into a programmatic rect rule and instead need a schema / fill-
logic / attorney-level decision (see catalog/geometry_review_followups.md).

Unlike build_poll.py (visual A/B/C rect picks, served on 8770), each unit here
carries plain-language options where every option states *what changes in the
filled PDF* if you pick it, plus a free-text box. A context crop (the field(s)
outlined in red on the real form) is rendered from the already-existing
geom-review-out/<FORM>/page-N.png renders so reviewing stays visual too, but the
decision is text-first.

    python3 scripts/geometry_review/build_followup_poll.py --out ~/geom-review-out
writes  <out>/followup_poll.json  and  <out>/followup_crops/<id>.png
Then:   python3 scripts/geometry_review/serve_followups.py --out ~/geom-review-out --port 8771
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
FORMS = ROOT / "repo" / "forms"
LETTER_W = 612.0  # all probate renders are portrait letter @ 150dpi (scale 2.083)

# ---------------------------------------------------------------------------
# The reviewed decision set. Each item:
#   form, field, widget_idx  -> the unit (widget_idx is the one in question)
#   category                 -> Structural | Semantic | Continuation | Multiline
#   title / problem          -> plain-language framing (+ the raw signal)
#   options[{key,label,result}] -> result = how the filled PDF changes
#   crop  -> [(field, widget_idx), ...] rects to outline for the context image
#   precedent (optional)     -> related prior decision worth knowing
# ---------------------------------------------------------------------------
DECISIONS = [
    # ---------------- Structural: the widget set itself is wrong --------------
    {
        "form": "AF-105", "field": "stocks_bonds_specify", "widget_idx": 0,
        "category": "Structural",
        "title": "Stocks/Bonds line is really TWO answers (description + $ value)",
        "problem": "Item reads 'Stocks/Bonds (specify) ____ having a value of $____'. "
                   "Today a single text field spans the whole line and prints over "
                   "the pre-printed '$'. Flagged: print_overlap ['$','Cash','posted','as','bail'].",
        "options": [
            {"key": "A", "label": "Split into two fields: stocks_bonds_specify (text) + new stocks_bonds_value (currency)",
             "result": "Description fills the left blank; the dollar amount fills its own box right after the printed '$' with currency formatting. No collision with the '$' glyph."},
            {"key": "B", "label": "Keep one text field for the whole line",
             "result": "Description and amount share one box; the value can overrun the printed '$' and there is no currency formatting."},
        ],
        "crop": [("stocks_bonds_specify", 0)],
    },
    {
        "form": "AF-105", "field": "expected_payments_explanation", "widget_idx": 1,
        "category": "Structural",
        "title": "Item 6 is an overarching question, not a fillable blank",
        "problem": "Item 6 ('expected payments') is a heading with its own sub-questions. "
                   "Its widget(s) overlap the 'OTHER ASSETS:' heading. Flagged: print_overlap ['OTHER','ASSETS:'].",
        "options": [
            {"key": "A", "label": "Remove expected_payments_explanation from the schema",
             "result": "No box is rendered at item 6's heading; the sub-question fields each fill their own blanks; the heading text stays clean."},
            {"key": "B", "label": "Keep the field",
             "result": "A box stays over the heading area and may capture a value that doesn't belong to any real blank."},
        ],
        "crop": [("expected_payments_explanation", 0), ("expected_payments_explanation", 1)],
    },
    {
        "form": "AF-105", "field": "dependents_list", "widget_idx": 2,
        "category": "Structural",
        "title": "Stray dependents_list widget under 'CASH ASSETS:'",
        "problem": "Widget 2 of dependents_list sits under the 'CASH ASSETS:' heading "
                   "— 'tough to tell what this goes with', likely a stray widget. "
                   "Flagged: print_overlap ['CASH','ASSETS:'], starts_under_label ['CASH'].",
        "options": [
            {"key": "A", "label": "Remove the stray widget (w2)",
             "result": "dependents_list keeps only its real line(s); nothing prints over 'CASH ASSETS:'."},
            {"key": "B", "label": "Re-locate w2 (flag for source re-derivation)",
             "result": "Keep the widget but move it to the correct blank once its true spot is confirmed against the source layout."},
            {"key": "C", "label": "Keep as-is",
             "result": "Widget stays where it is and continues to overlap the heading."},
        ],
        "crop": [("dependents_list", 0), ("dependents_list", 1), ("dependents_list", 2)],
    },
    {
        "form": "AF-105", "field": "insurance_pension_value", "widget_idx": 1,
        "category": "Structural",
        "title": "insurance_pension_value widget isn't on the answer blank",
        "problem": "'Doesn't show where the spot is' — widget 1 overlaps the 'EXPENSES:' "
                   "heading rather than the value blank. Flagged: print_overlap ['EXPENSES:'].",
        "options": [
            {"key": "A", "label": "Re-locate from the source layout (flag for re-derivation)",
             "result": "Widget is moved to the real value blank once located; value prints in the right place."},
            {"key": "B", "label": "Keep as-is",
             "result": "Value continues to print over the 'EXPENSES:' heading."},
        ],
        "crop": [("insurance_pension_value", 0), ("insurance_pension_value", 1)],
    },
    {
        "form": "PP-405", "field": "corporate_surety_address", "widget_idx": 0,
        "category": "Structural",
        "title": "corporate_surety_address overlaps 'Name of Corporate Surety:' label",
        "problem": "This belongs under '1. Name of corporate surety:'. If a blank already "
                   "exists there, this is a meta-question (the label, not an answer). "
                   "Flagged: no_line_support, print_overlap ['1.','Name','of','Corporate','Surety:'].",
        "options": [
            {"key": "A", "label": "Re-point the widget to item 1's real blank",
             "result": "The surety address fills the actual blank beneath item 1."},
            {"key": "B", "label": "Delete it (duplicate of item 1's field / meta-question)",
             "result": "No widget here; item 1's existing field carries the value."},
            {"key": "C", "label": "Keep as-is",
             "result": "Value keeps printing over the 'Name of Corporate Surety:' label."},
        ],
        "crop": [("corporate_surety_address", 0)],
    },
    {
        "form": "AF-104", "field": "reason_not_contacting", "widget_idx": 0,
        "category": "Structural",
        "title": "AF-104 'Name | Date | Reason' table has swapped columns + no rows",
        "problem": "The bottom table is 'Name | Date | Reason for not contacting'. "
                   "reason_not_contacting is mapped under the NAME column and "
                   "name_not_contacted under the REASON column — they're swapped; there "
                   "is no Date widget and no data rows. (Found by the multiline fleet split.)",
        "options": [
            {"key": "A", "label": "Swap the two mappings, add a Date-column widget, AND a multi-row continuation chain",
             "result": "Names land under Name, reasons under Reason, dates under Date, and multiple people can be listed row by row."},
            {"key": "B", "label": "Just swap the two mappings",
             "result": "Columns are correct for a single row; still no Date column and no extra rows."},
            {"key": "C", "label": "Leave as-is",
             "result": "Name and reason keep printing in each other's column."},
        ],
        "crop": [("reason_not_contacting", 0), ("name_not_contacted", 0)],
    },
    # ---------------- Semantic: fill logic, not geometry ----------------------
    {
        "form": "AF-102", "field": "notary_county", "widget_idx": 0,
        "category": "Semantic",
        "title": "notary_county should be UPPER-CASED to match 'COUNTY OF ___'",
        "problem": "Printed context is 'STATE OF … COUNTY OF …' in all caps; the county "
                   "value should be upper-cased at fill time, and long county names may "
                   "run past the box's right edge. Flagged: ocr token_offset.",
        "options": [
            {"key": "A", "label": "Uppercase on fill + allow right overflow",
             "result": "County prints in CAPS to match the line; long names extend past the box edge rather than clipping."},
            {"key": "B", "label": "Uppercase on fill, keep current box width",
             "result": "CAPS, but a long county name may clip at the box's right edge."},
            {"key": "C", "label": "Leave as typed (mixed case)",
             "result": "County prints as composed (e.g. 'Cumberland'), visually inconsistent with the all-caps line."},
        ],
        "crop": [("notary_county", 0)],
    },
    {
        "form": "DE-403", "field": "condition_decedent_residence", "widget_idx": 0,
        "category": "Semantic",
        "title": "Decedent residence: town/city + state, not a full street address",
        "problem": "The sentence reads as municipality + state (e.g. 'Falmouth, ME'), but "
                   "today the field expects a full street address. This is a value/sample "
                   "change, not a rect move. Flagged: print_overlap [',','to','this','Estate'].",
        "options": [
            {"key": "A", "label": "Change expected value to town/city + state (e.g. 'Falmouth, ME')",
             "result": "Fill composes only municipality + state; fits the line; reads correctly in the sentence."},
            {"key": "B", "label": "Keep full street address",
             "result": "Full address can overrun the blank and reads oddly mid-sentence."},
        ],
        "crop": [("condition_decedent_residence", 0)],
    },
    {
        "form": "N-115", "field": "probate_court_address", "widget_idx": 0,
        "category": "Semantic",
        "title": "Court address: town/city only — ', Maine' is pre-printed, no zip",
        "problem": "', Maine' is already printed after this blank and no zip is needed, but "
                   "today the field fills a full address incl. state/zip and the box runs to "
                   "the right past ', Maine'. Flagged: print_overlap [',','Maine.','(street','or','mailing','address)'].",
        "options": [
            {"key": "A", "label": "Town/city only + trim the box to end before ', Maine'",
             "result": "Only the municipality prints; the pre-printed ', Maine' reads correctly; no duplicate state, no zip."},
            {"key": "B", "label": "Town/city only, keep current box width",
             "result": "Municipality only, but the box still extends over the pre-printed ', Maine'."},
            {"key": "C", "label": "Keep full address incl. state/zip",
             "result": "Prints 'Town, Maine 04101' on top of the pre-printed ', Maine' — duplicated state."},
        ],
        "crop": [("probate_court_address", 0)],
    },
    {
        "form": "AD-008", "field": "notary_date", "widget_idx": 0,
        "category": "Semantic",
        "title": "notary_date is the notary's to complete — auto-fill it?",
        "problem": "notary_date is completed by the notary AT notarization. Today the box "
                   "spans the whole page width and the pipeline may auto-fill it. Same issue on "
                   "NC-001 notary_date. Flagged: ocr token_missing / codex_major.",
        "precedent": "In the maine-court-forms repo (Round 12), OFFICIAL acknowledgment-line "
                     "dates were left blank while a signer's-own date was kept. A notary_date "
                     "is the official's line.",
        "options": [
            {"key": "A", "label": "Don't auto-fill + size the box to the widest date format",
             "result": "The date blank is left for the notary; the box is only as wide as a date needs, so it no longer spans the page."},
            {"key": "B", "label": "Size-to-content but still auto-fill a date",
             "result": "A date is stamped in a correctly-sized box — but risks asserting a date the notary hasn't certified."},
            {"key": "C", "label": "Omit the field entirely",
             "result": "No notary_date widget at all; the notary writes on the blank line by hand."},
            {"key": "D", "label": "Keep as-is (full-width, auto-filled)",
             "result": "A date spans the page width, as today."},
        ],
        "crop": [("notary_date", 0)],
    },
    # ---------------- Continuation: 'part 1 of 2' line-split answers ----------
    {
        "form": "AD-008", "field": "medical_expenses_details", "widget_idx": 0,
        "category": "Continuation",
        "title": "AD-008 expense details: 2-line continuation (line 1 at the underline)",
        "problem": "Each expense-detail answer (medical / foster_care / living) should start "
                   "on the underline after 'child.' / 'birth mother.' and overflow to a 2nd "
                   "full-width line; today widget 0 is a single line that can overlap the prompt. "
                   "Flagged: vision_confirmed token_offset.",
        "options": [
            {"key": "A", "label": "Model as a 2-line continuation chain (line 1 at the prompt's trailing underline, line 2 the next row, full width)",
             "result": "Long values wrap to the second line instead of overrunning; line 1 begins exactly at the underline. Applies to all three *_expenses_details fields."},
            {"key": "B", "label": "One wide multi-line box below the prompt",
             "result": "Answer sits in a tall box under the prompt spanning the margins; simpler, but moves the answer off the prompt line."},
            {"key": "C", "label": "Keep single line",
             "result": "Answer stays on one line and can collide with the printed prompt."},
        ],
        "crop": [("medical_expenses_details", 0), ("medical_expenses_details", 1)],
    },
    {
        "form": "N-118", "field": "change_in_dwelling_new_address", "widget_idx": 0,
        "category": "Continuation",
        "title": "N-118 '(address)' answers wrap across two printed lines",
        "problem": "Several N-118 '(address)' answers wrap: line 1 ends at the prompt's "
                   "trailing blank, line 2 is the blank on the next row. Today each is a "
                   "single widget. Cluster: change_in_dwelling_new_address, "
                   "conservators_report_and_accounting_court_address, "
                   "change_in_permanent_dwelling_new_address, revised_*_plan_filed_court_address.",
        "options": [
            {"key": "A", "label": "Add a 2-widget continuation chain (line 1 + line 2) for each address field in the cluster",
             "result": "Long addresses flow onto the 2nd line; short ones stay on line 1. Consistent across the N-118 cluster."},
            {"key": "B", "label": "Single line, allow right overflow",
             "result": "Address stays on one line and may run past the right margin."},
            {"key": "C", "label": "Keep as-is",
             "result": "Single line at current width; long addresses clip."},
        ],
        "crop": [("change_in_dwelling_new_address", 0)],
    },
    {
        "form": "N-115", "field": "pr_address", "widget_idx": 0,
        "category": "Continuation",
        "title": "N-115 pr_address — possible part-1/part-2 split",
        "problem": "Possible split: a first part after 'the address' then continuation onto "
                   "the next line. Flagged: no_line_support, print_overlap "
                   "['other','persons','entitled','to','notice','by'].",
        "options": [
            {"key": "A", "label": "Add a continuation chain (line 1 after 'the address', line 2 next row)",
             "result": "Address starts at the prompt and wraps to the next line; no overlap with the following clause."},
            {"key": "B", "label": "Single line + allow overflow",
             "result": "Address stays on one line; may run into 'other persons entitled to notice'."},
            {"key": "C", "label": "Keep as-is",
             "result": "Current single-line placement, which overlaps the following text."},
        ],
        "crop": [("pr_address", 0)],
    },
    {
        "form": "AD-028", "field": "putative_parent_likely_address", "widget_idx": 0,
        "category": "Continuation",
        "title": "AD-028 putative parent address — line 1 starts at 'following address:'",
        "problem": "Line 1 of the answer is at 'following address:' and overflows to the next "
                   "line; today widget 0 is the right-hand tail and widget 1 the 2nd line. "
                   "Needs line 1 to begin at the prompt.",
        "options": [
            {"key": "A", "label": "Seat line 1 at the 'following address:' underline + keep the line-2 continuation",
             "result": "Address begins right after 'following address:' and wraps to line 2 — natural reading order."},
            {"key": "B", "label": "Single full-width line below the prompt",
             "result": "Whole address on one line under the prompt; loses the inline 'following address:' start."},
            {"key": "C", "label": "Keep as-is",
             "result": "Line 1 stays as the short right-hand tail; reads awkwardly."},
        ],
        "crop": [("putative_parent_likely_address", 0), ("putative_parent_likely_address", 1)],
    },
]


def scale_for(png_w: int) -> float:
    return png_w / LETTER_W


def render_crop(form: str, targets: list, out_crops: pathlib.Path, uid: str):
    """Outline target widget rects in red on the page render and crop a band."""
    from PIL import Image, ImageDraw
    gp = FORMS / form / "fill_geometry.json"
    if not gp.exists():
        return None
    F = json.loads(gp.read_text())["fields"]
    # resolve rects; group by page, use the first target's page for the crop
    resolved = []
    for fld, idx in targets:
        spec = F.get(fld)
        if not spec or not spec.get("widgets"):
            continue
        ws = spec["widgets"]
        i = idx if isinstance(idx, int) and idx < len(ws) else 0
        w = ws[i]
        resolved.append((w.get("page", 0), w.get("rect")))
    resolved = [r for r in resolved if r[1]]
    if not resolved:
        return None
    page = resolved[0][0]
    page_png = (OUT_PAGES / form / f"page-{page + 1}.png")
    if not page_png.exists():
        return None
    im = Image.open(page_png).convert("RGB")
    s = scale_for(im.size[0])
    dr = ImageDraw.Draw(im)
    band = [1e9, 1e9, -1e9, -1e9]
    for pg, rect in resolved:
        if pg != page:
            continue
        x0, y0, x1, y1 = [c * s for c in rect]
        dr.rectangle([x0, y0, x1, y1], outline=(220, 30, 30), width=3)
        band[0] = min(band[0], x0); band[1] = min(band[1], y0)
        band[2] = max(band[2], x1); band[3] = max(band[3], y1)
    pad_x, pad_y = 40, 95
    cx0 = max(0, int(band[0] - pad_x)); cy0 = max(0, int(band[1] - pad_y))
    cx1 = min(im.size[0], int(band[2] + pad_x)); cy1 = min(im.size[1], int(band[3] + pad_y))
    crop = im.crop((cx0, cy0, cx1, cy1))
    out_crops.mkdir(parents=True, exist_ok=True)
    fp = out_crops / f"{uid}.png"
    crop.save(fp)
    return f"followup_crops/{uid}.png"


def main() -> int:
    global OUT_PAGES
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    args = ap.parse_args()
    OUT_PAGES = args.out  # page renders live at <out>/<FORM>/page-N.png
    crops = args.out / "followup_crops"

    units = []
    for d in DECISIONS:
        uid = hashlib.sha1(f"{d['form']}/{d['field']}/{d['widget_idx']}".encode()).hexdigest()[:10]
        crop_rel = render_crop(d["form"], d.get("crop", [(d["field"], d["widget_idx"])]), crops, uid)
        units.append({
            "id": uid,
            "form": d["form"], "field": d["field"], "widget_idx": d["widget_idx"],
            "category": d["category"], "title": d["title"], "problem": d["problem"],
            "precedent": d.get("precedent", ""),
            "options": d["options"],
            "crop": crop_rel,
        })
    (args.out / "followup_poll.json").write_text(json.dumps(units, indent=1))
    n_crop = sum(1 for u in units if u["crop"])
    print(f"wrote {len(units)} follow-up units -> {args.out/'followup_poll.json'} "
          f"({n_crop} with crops)")
    by_cat: dict[str, int] = {}
    for u in units:
        by_cat[u["category"]] = by_cat.get(u["category"], 0) + 1
    for c, n in sorted(by_cat.items()):
        print(f"  {c}: {n}")
    return 0


OUT_PAGES = pathlib.Path.home() / "geom-review-out"

if __name__ == "__main__":
    import sys
    sys.exit(main())
