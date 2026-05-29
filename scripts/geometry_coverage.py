#!/usr/bin/env python3
"""Account for every fillable widget on each form: mapped to a field, or not.

Integrator-facing transparency report. For each form it selects the fused PDF
the tree actually describes (see `gen_fill_geometry`), then splits the PDF's
widgets into:

  * mapped   — bound to a schema field (shipped in fill_geometry.json)
  * unmapped — present on the form but bound by no tree node ("orphans")

Each unmapped widget is recorded with its page, rect, detected name, widget
type, and a *heuristic* category (review-only, not authoritative):

  * mirror        — detected name matches a mapped field (a duplicate widget;
                    the same logical value likely appears in two places)
  * court_or_sig  — signature / judge / register / clerk / notary widget
                    (filled by the court or by wet ink, not the litigant)
  * likely_static — name looks like printed text the detector over-captured
                    (footnote, statutory reference, heading, sworn statement)
  * candidate_field — an apparently-fillable input with no field yet

Outputs `catalog/geometry_coverage.json` (machine-readable) and prints a
summary. Needs the pipeline build outputs (`--pipeline-root`, same as
`gen_fill_geometry`). Widget *names* are auto-generated and unreliable — treat
categories as hints for human review, not ground truth.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import yaml

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import gen_fill_geometry as gen  # noqa: E402

_SIG = re.compile(r"signature|judge|register|clerk|notary_printed|seal|witness", re.I)
_STATIC = re.compile(r"footnote|statutory_reference|reference|order_heading|"
                     r"notice_to_|warning|_the_time_|whose_claims|has_has_not|"
                     r"text_field_p", re.I)


def _norm(n: str) -> str:
    return re.sub(r"(_row\d+|_line\d+|_\d+|_2)$", "", n or "")


# Findings from a manual visual audit (renders of the fused PDFs). Keyed
# "FORM:WIDGET". Detector names are unreliable, so these override the heuristic.
# Verified during a binding pass (renders + geometry vs bound widgets). Keyed
# "FORM:WIDGET". Detector names are unreliable, so these override the heuristic.
MANUAL = {
    "GS-014:W050": ("spurious", "table-corner widget in the row-label column; "
                    "the real legal_parent_1_address is W054 (already bound)"),
    "DE-602:W005": ("spurious", "overlays declarative statement item 1 (no blank)"),
    "DE-602:W006": ("spurious", "overlays declarative statement item 2 (no blank)"),
    "DE-602:W010": ("spurious", "overlays declarative statement item 4 (no blank)"),
    # APP-2 W028/29/30 overlay the table HEADER row; the data rows hearing_1..4
    # (W031-W042) are fully bound. Not missing cells.
    "APP-2:W028": ("spurious", "overlays the HEARING-DATE column header; data rows W031-W042 bound"),
    "APP-2:W029": ("spurious", "overlays the PROCEEDING column header; data rows bound"),
    "APP-2:W030": ("spurious", "overlays the REPORTER/INDEX column header; data rows bound"),
    "DE-504:W015": ("ambiguous", "footer email box; petitioner email is already "
                    "captured in petitioner_info (item 1) — likely redundant"),
    "DE-403:W030": ("ambiguous", "large area below the Pledged-Personal-Property "
                    "labels; surety_1/2_description are bound to small boxes "
                    "(W028/W029) — verify W030 is a real shared area vs redundant"),
    "DE-403:W053": ("candidate_input", "'general pledge of personal assets' "
                    "writing area; appears unmodeled — needs form-intent review"),
    "DE-403:W072": ("candidate_input", "officer-executing-bond name area; verify "
                    "vs bound corporate_surety_officer_name_authority (W071) above"),
    # DE-601: a separate attorney-inject step set schema attorney_bar_number->W028
    # + attorney_email->W029, but W028 is visually the Email box and W029 overlays
    # the footnote — the injected widget assignments look misaligned.
    "DE-601:W028": ("ambiguous", "schema injects attorney_bar_number->W028 + "
                    "attorney_email->W029, but W028 is the Email box and W029 is "
                    "the footnote — attorney-inject misalignment; needs review"),
}


def _overlaps_bound(rect, page, bound_rects) -> bool:
    x0, y0, x1, y1 = rect
    for (bp, (bx0, by0, bx1, by1)) in bound_rects:
        if bp == page and x0 < bx1 and bx0 < x1 and y0 < by1 and by0 < y1:
            return True
    return False


def _header_band(rect, page, bound_rects) -> bool:
    """True if a bound widget sits directly below this one in an overlapping
    x-range (within 30pt) — i.e. this is a column header / label band, not an
    input cell."""
    x0, y0, x1, y1 = rect
    for (bp, (bx0, by0, bx1, by1)) in bound_rects:
        if bp == page and x0 < bx1 and bx0 < x1 and 0 <= by0 - y1 <= 30:
            return True
    return False


def _assess(form_id, w, cat, rect, page, name, bound_rects):
    """Return (assessment, reason) — a review hint for a pipeline binding pass."""
    key = f"{form_id}:{w}"
    if key in MANUAL:
        return MANUAL[key]
    if cat == "likely_static":
        return "leave_unmapped", "printed/static text the detector over-captured"
    if cat == "court_or_sig":
        return "leave_unmapped", "court/clerk/signature widget (not litigant-filled)"
    if cat == "mirror":
        return "bind_as_duplicate", "same value as the mapped field of this name"
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if _overlaps_bound(rect, page, bound_rects):
        return "ambiguous", "overlaps/abuts a bound widget — verify duplicate vs mis-bind"
    if _header_band(rect, page, bound_rects):
        return "spurious", "header/label band directly above a bound data column"
    if width < 18 and height < 18:
        return "review_marker", "checkbox-sized — confirm it is a real input"
    return "candidate_input", "apparent blank with no field — confirm via alignment/vision audit, then bind"


def _select_fused(form_id: str, pr: pathlib.Path):
    cands = gen._fused_candidates(form_id, gen._fused_list(pr))
    if not cands:
        return None, {}
    tree = yaml.safe_load((pr / "trees" / f"{form_id}.yaml").read_text())
    best = None
    for f in cands:
        w2, _, _ = gen._digest_geometry(f)
        score = gen._tree_type_mismatch(tree, w2) if len(cands) > 1 else 0
        if best is None or score < best[0]:
            best = (score, f, w2)
    return best[1], best[2]


def _bound_set(tree: dict) -> set:
    bound = set()
    for n in tree.get("nodes", []):
        for w in (n.get("widgets") or ([n["widget"]] if n.get("widget") else [])):
            bound.add(w)
        for o in n.get("options", []):
            for w in (o.get("widgets") or ([o["widget"]] if o.get("widget") else [])):
                bound.add(w)
    return bound


def coverage(form_id: str, pr: pathlib.Path, names: dict) -> dict:
    fused, wid2geom = _select_fused(form_id, pr)
    if not fused:
        return {"form_id": form_id, "_missing": "no fused pdf"}
    tree = yaml.safe_load((pr / "trees" / f"{form_id}.yaml").read_text())
    bound = _bound_set(tree)
    # exact detected-names on bound widgets — a true mirror is the SAME named
    # widget appearing twice (one bound), not a sibling repeat-row (_row2 etc.)
    bound_names = {names.get(w, "") for w in bound if names.get(w)}
    bound_rects = [(wid2geom[w][0], wid2geom[w][1]) for w in bound if w in wid2geom]
    unmapped = []
    for w, (pg, rect, wt) in wid2geom.items():
        if w in bound:
            continue
        nm = names.get(w, "")
        if _STATIC.search(nm):
            cat = "likely_static"
        elif nm and nm in bound_names:
            cat = "mirror"
        elif _SIG.search(nm):
            cat = "court_or_sig"
        else:
            cat = "candidate_field"
        assessment, reason = _assess(form_id, w, cat, rect, pg, nm, bound_rects)
        entry = {"widget_id": w, "page": pg, "rect": rect, "widget_type": wt,
                 "detected_name": nm, "category": cat,
                 "assessment": assessment, "reason": reason}
        if assessment in ("candidate_input", "ambiguous"):
            entry["suggested_field_id"] = _norm(nm) or nm
        unmapped.append(entry)
    unmapped.sort(key=lambda u: (u["page"], u["rect"][1]))
    return {"form_id": form_id, "n_mapped": len(bound),
            "n_unmapped": len(unmapped), "unmapped": unmapped}


def _names(form_id: str, pr: pathlib.Path) -> dict:
    import fitz
    import build_form_digest as dig
    fused, _ = _select_fused(form_id, pr)
    doc = fitz.open(fused)
    items = dig.extract_items(doc, include_widget_names=True)
    dig.assign_widget_ids(items)
    return {it.widget_id: getattr(it, "widget_name", "") or ""
            for it in items if it.kind == "widget"}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pipeline-root", required=True)
    ap.add_argument("--repo", default=".", type=pathlib.Path)
    a = ap.parse_args()
    pr = pathlib.Path(a.pipeline_root)
    repo = a.repo.resolve()
    forms = sorted(d.name for d in (repo / "repo" / "forms").iterdir()
                   if (d / "schema.json").exists())
    report = {}
    tot_unmapped = 0
    by_cat: dict[str, int] = {}
    by_assess: dict[str, int] = {}
    for fid in forms:
        names = _names(fid, pr)
        c = coverage(fid, pr, names)
        report[fid] = c
        for u in c.get("unmapped", []):
            by_cat[u["category"]] = by_cat.get(u["category"], 0) + 1
            by_assess[u["assessment"]] = by_assess.get(u["assessment"], 0) + 1
        tot_unmapped += c.get("n_unmapped", 0)
    out = repo / "catalog" / "geometry_coverage.json"
    out.write_text(json.dumps({
        "n_forms": len(forms),
        "n_unmapped_widgets": tot_unmapped,
        "unmapped_by_category": dict(sorted(by_cat.items())),
        "unmapped_by_assessment": dict(sorted(by_assess.items())),
        "note": ("widget detected_name is auto-generated and unreliable; "
                 "category/assessment are review hints for a pipeline binding "
                 "pass, not authoritative. Confirm each via the alignment + "
                 "vision-audit loop before binding (see docs/architecture.md)."),
        "assessment_legend": {
            "candidate_input": "apparent blank with no field — bind after confirming",
            "ambiguous": "overlaps/abuts a bound widget — verify duplicate vs mis-bind",
            "spurious": "not a real input (over static text or a table corner)",
            "review_marker": "checkbox-sized — confirm it is a real input",
            "bind_as_duplicate": "mirror of a mapped field — bind to the same field",
            "leave_unmapped": "static text or court/signature widget — leave as-is",
        },
        "binding_pass": ("A 2026-05 binding pass (renders + geometry vs bound "
                         "widgets) found 0 of these safely auto-bindable: most "
                         "are detector over-captures of headers/labels/statements; "
                         "a few are redundant (value already in a combined field), "
                         "court-filled, or tangled with the attorney-inject step. "
                         "The candidate_input/ambiguous entries are real review "
                         "items but need per-form intent + the fill-validation "
                         "loop, not an automated pass."),
        "forms": report,
    }, indent=2) + "\n")
    print(f"geometry_coverage: {len(forms)} forms, {tot_unmapped} unmapped widgets")
    print(f"by category:   {dict(sorted(by_cat.items()))}")
    print(f"by assessment: {dict(sorted(by_assess.items()))}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
