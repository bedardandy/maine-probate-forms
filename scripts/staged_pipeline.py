"""4-stage form-fillable pipeline with per-form recipes + checkpoint outputs.

Stages:
  1. Checkboxes — CF detection + raster overlay snap (universal win)
  2. Cell tables — text widgets fitted to detected table cells
  3. Underlines — text widgets snapped to drawn underlines
  4. Free forms — column/cluster snaps, margin alignment for the rest
  5. Audit (run separately via opus_alignment_review.py or local_alignment_review.py)

Each stage writes a checkpoint PDF so you can inspect after any stage.
Per-form recipes (in recipes.json) toggle which stages apply.

Usage:
  scripts/staged_pipeline.py                    # all 79 forms with default recipe
  scripts/staged_pipeline.py --form PP-205      # single form
  scripts/staged_pipeline.py --stages 1,2,3     # skip stage 4 globally
  scripts/staged_pipeline.py --no-checkpoints   # only write final PDF
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.fuse_layer1_cf import (  # noqa: E402
    FusedWidget,
    load_pdf_widgets,
    load_analysis,
    extract_underlines_and_boxes,
    canonical_sizes,
    snap_text_to_underline,
    canonicalize_checkboxes,
    raster_snap_checkboxes,
    table_cell_snap,
    snap_widget_y_to_cell_rows,
    filter_ours_in_table_areas,
    filter_widgets_in_header_cells,
    vertical_separator_snap,
    wide_widget_left_margin_snap,
    wide_widget_right_margin_snap,
    column_snap,
    widget_cluster_snap,
    widget_x1_cluster_snap,
    snap_widget_top_below_text,
    page_margin_clamp,
    filter_footer_line_widgets,
    filter_orphan_widgets,
    revert_bad_widgets_to_v2,
    nms_overlap,
    name_widgets,
    normalize_row_indices,
    add_ours_only,
    write_fused,
    ORIG_DIR,
    OURS_DIR,
    CF_DIR,
)

CHECKPOINT_ROOT = ROOT / "output_staged"
RECIPES_FILE = ROOT / "scripts" / "recipes.json"

DEFAULT_RECIPE = {"base": "cf", "stages": [1, 2, 3, 4]}


def stage1_checkboxes(state: dict) -> None:
    """Stage 1: canonicalize + raster-snap all checkbox widgets."""
    state["widgets"] = canonicalize_checkboxes(state["widgets"])
    state["raster_stats"] = raster_snap_checkboxes(state["widgets"], state["src_pdf"])


def stage2_cells(state: dict) -> None:
    """Stage 2: cell-table widgets — snap to cells, drop header overlays, renumber rows."""
    state["widgets"] = filter_ours_in_table_areas(state["widgets"], state["analysis"])
    state["widgets"] = filter_widgets_in_header_cells(state["widgets"], state["analysis"])
    state["widgets"] = table_cell_snap(state["widgets"], state["analysis"])
    state["widgets"] = snap_widget_y_to_cell_rows(state["widgets"], state["analysis"])


def stage3_underlines(state: dict) -> None:
    """Stage 3: snap text widgets to source-PDF underlines (already done at CF load — no-op here).

    The underline snap happens in load-time iteration over CF widgets. This
    stage exists so a recipe can opt OUT of underline-anchoring (e.g. to keep
    free-form widgets at CF's raw detection position).
    """
    # Already applied at load time via snap_text_to_underline. Stage is a placeholder
    # but lets us toggle off the behavior in a future revision.
    pass


def stage4_freeforms(state: dict) -> None:
    """Stage 4: free-form widget alignment — verticals, margins, column/cluster snap."""
    state["widgets"] = vertical_separator_snap(state["widgets"], state["analysis"])
    state["widgets"] = wide_widget_left_margin_snap(state["widgets"], state["analysis"])
    state["widgets"] = wide_widget_right_margin_snap(state["widgets"], state["analysis"])
    state["widgets"] = column_snap(state["widgets"], state["analysis"])
    state["widgets"] = widget_cluster_snap(state["widgets"])
    state["widgets"] = widget_x1_cluster_snap(state["widgets"])
    state["widgets"] = snap_widget_top_below_text(state["widgets"], state["analysis"])


def post_pipeline(state: dict) -> None:
    """Cleanup passes always applied: margin clamp, orphan filter, NMS, naming."""
    state["widgets"] = page_margin_clamp(state["widgets"])
    state["widgets"] = filter_footer_line_widgets(state["widgets"], state["analysis"])
    state["widgets"] = filter_orphan_widgets(state["widgets"], state["analysis"])
    state["widgets"] = revert_bad_widgets_to_v2(
        state["widgets"], state["analysis"], state["v2_widgets"]
    )
    state["widgets"] = nms_overlap(state["widgets"])
    state["widgets"] = name_widgets(state["widgets"], state["analysis"], state["form_name"])
    state["widgets"] = normalize_row_indices(state["widgets"])


STAGES = {
    1: ("checkboxes", stage1_checkboxes),
    2: ("cells",      stage2_cells),
    3: ("underlines", stage3_underlines),
    4: ("freeforms",  stage4_freeforms),
}


def run_form(cat: str, name: str, recipe: dict, write_checkpoints: bool = True) -> dict:
    """Run the staged pipeline for a single form."""
    stem = pathlib.Path(name).stem
    src = ORIG_DIR / cat / name
    cf_pdf = CF_DIR / cat / f"{stem}_commonforms.pdf"
    v2_pdf = OURS_DIR / cat / f"{stem}_fillable.pdf"

    cf_widgets = load_pdf_widgets(cf_pdf)
    v2_widgets = load_pdf_widgets(v2_pdf)
    analysis = load_analysis(name)

    underlines, checkboxes = extract_underlines_and_boxes(analysis["pages"])
    text_h, cw, ch = canonical_sizes(underlines, checkboxes)

    base = recipe.get("base", "cf")
    stages = recipe.get("stages", [])

    if base == "v2":
        # Start from v2's widget set. Stage 1 (if present) replaces v2 checkboxes
        # with CF's raster-snapped equivalents. Stages 2-4 act on text widgets if
        # enabled. For v2-wins forms we typically use base=v2 with stages=[1] only,
        # giving "v2 layout + better checkboxes".
        fused = list(v2_widgets)
        if 1 in stages:
            cf_checks = [w for w in cf_widgets if w.type == "check"]
            cf_checks = canonicalize_checkboxes(cf_checks)
            raster_snap_checkboxes(cf_checks, src)
            # Drop v2's checkbox widgets and substitute CF's
            fused = [w for w in fused if w.type != "check"] + cf_checks
    else:
        # base = "cf" (default): start from CF, fold in v2-only widgets where CF
        # missed something. Stage 3 controls whether to underline-snap CF text.
        snapped = []
        if 3 in stages:
            for w in cf_widgets:
                if w.type == "text":
                    snapped.append(
                        snap_text_to_underline(w, underlines.get(w.page, []), text_h)
                    )
                else:
                    snapped.append(w)
        else:
            snapped = list(cf_widgets)
        fused = add_ours_only(snapped, v2_widgets)

    state = {
        "src_pdf": src,
        "cat": cat,
        "form_name": name,
        "stem": stem,
        "analysis": analysis,
        "cf_widgets": cf_widgets,
        "v2_widgets": v2_widgets,
        "widgets": fused,
        "raster_stats": {},
    }

    stages_run = []
    for stage_id in sorted(recipe.get("stages", [])):
        stage_label, stage_fn = STAGES[stage_id]
        if stage_id == 3:
            # Already applied at load time; mark it as "run" so checkpoint writes.
            stages_run.append((stage_id, stage_label))
            if write_checkpoints:
                cpath = CHECKPOINT_ROOT / f"after_stage{stage_id}_{stage_label}" / cat
                _write_checkpoint(state, cpath, stem)
            continue
        stage_fn(state)
        stages_run.append((stage_id, stage_label))
        if write_checkpoints:
            cpath = CHECKPOINT_ROOT / f"after_stage{stage_id}_{stage_label}" / cat
            _write_checkpoint(state, cpath, stem)

    post_pipeline(state)

    final_dir = CHECKPOINT_ROOT / "final" / cat
    final_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = final_dir / f"{stem}_staged.pdf"
    write_fused(src, state["widgets"], out_pdf)

    cf_n = sum(1 for w in state["widgets"] if w.source == "cf")
    ours_n = sum(1 for w in state["widgets"] if w.source == "ours")
    return {
        "form": name,
        "total": len(state["widgets"]),
        "from_cf": cf_n,
        "from_ours": ours_n,
        "raster": state["raster_stats"],
        "stages_run": [s[1] for s in stages_run],
        "out_pdf": str(out_pdf.relative_to(ROOT)),
    }


def _write_checkpoint(state: dict, cpath: pathlib.Path, stem: str) -> None:
    cpath.mkdir(parents=True, exist_ok=True)
    write_fused(state["src_pdf"], state["widgets"], cpath / f"{stem}_staged.pdf")


def load_recipes() -> dict:
    if RECIPES_FILE.exists():
        return json.loads(RECIPES_FILE.read_text())
    return {"default": DEFAULT_RECIPE}


def get_recipe(form_id: str, recipes: dict) -> dict:
    return recipes.get(form_id, recipes.get("default", DEFAULT_RECIPE))


def list_forms(form_filter: str | None) -> list[tuple[str, str]]:
    targets = []
    for src in sorted(ORIG_DIR.rglob("*.pdf")):
        try:
            d = fitz.open(src)
            w = sum(len(list(p.widgets())) for p in d)
            d.close()
        except Exception:
            continue
        if w > 0:
            continue
        cat = src.parent.name
        v2 = OURS_DIR / cat / (src.stem + "_fillable.pdf")
        cf = CF_DIR / cat / (src.stem + "_commonforms.pdf")
        if not v2.exists() or not cf.exists():
            continue
        if form_filter and form_filter not in src.name:
            continue
        targets.append((cat, src.name))
    return targets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", help="filter to forms whose name matches this substring")
    ap.add_argument("--stages", help="comma-separated stage IDs (overrides recipe)")
    ap.add_argument("--no-checkpoints", action="store_true",
                    help="skip per-stage checkpoint PDFs")
    args = ap.parse_args()

    recipes = load_recipes()
    targets = list_forms(args.form)
    if not targets:
        print("No matching forms found.")
        return 1
    print(f"Running staged pipeline on {len(targets)} forms.")

    override_stages = None
    if args.stages:
        override_stages = [int(s) for s in args.stages.split(",")]

    for i, (cat, name) in enumerate(targets, 1):
        m = re.match(r"^([A-Z]+-?\d+(?:\([A-Z]\))?)", name)
        form_id = m.group(1) if m else pathlib.Path(name).stem
        recipe = get_recipe(form_id, recipes).copy()
        if override_stages is not None:
            recipe["stages"] = override_stages
        t0 = time.time()
        try:
            r = run_form(cat, name, recipe, write_checkpoints=not args.no_checkpoints)
            elapsed = time.time() - t0
            stages_str = ",".join(str(s) for s in recipe["stages"])
            print(f"  [{i:3d}/{len(targets)}] OK {elapsed:5.1f}s "
                  f"stages=[{stages_str}] total={r['total']:3d} "
                  f"({r['from_cf']:3d}cf+{r['from_ours']:3d}ours) {form_id}")
        except Exception as e:
            print(f"  [{i:3d}/{len(targets)}] FAIL  {form_id}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
