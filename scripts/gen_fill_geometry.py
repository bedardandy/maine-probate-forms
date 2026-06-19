#!/usr/bin/env python3
"""Generate per-form fill_geometry.json (field_id -> widget rects) for probate.

Closes the one gap for true filled-PDF output: this repo ships
`field_id -> W-id` (schema.widget_id / fields.csv) but not `W-id -> geometry`.
This derives a single self-contained artifact per form from a separate detection
pipeline's build outputs:

  * <pipeline-root>/trees/<form_id>.yaml          field_id (+ option) -> W-id
  * <pipeline-root>/output_fused/.../*_fused.pdf   digested in reading order
                                                   -> W-id -> rect/page

Output (one <form_id>.json per form):

  { "form_id", "page_size", "n_pages",
    "fields": {
       "<field_id>": {"type":"text",       "widgets":[{"page","rect"}...]},
       "<field_id>": {"type":"select_one", "options":[{"value","label","page","rect"}...]}
    } }

Rects are the pipeline-aligned positions; they inject cleanly onto the fetched
flat source (same page geometry). The build outputs (trees/, output_fused/) are
gitignored and live in the separate detection pipeline — pass its path with
`--pipeline-root`. For the full regenerate-and-publish flow see
`scripts/regen_fill_geometry.py` and `docs/maintenance.md`.

    python3 scripts/gen_fill_geometry.py --pipeline-root /path/to/pipeline --out /tmp/fillgeom
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import sys

import fitz
import yaml

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import build_form_digest as dig  # noqa: E402
from geometry_optimizer import optimize_geometry  # noqa: E402

DEFAULT_PIPELINE = SCRIPTS.parent  # build outputs alongside, if present
PUBLISHED_ROOT = SCRIPTS.parent


def _fused_list(pipeline_root: pathlib.Path) -> list[str]:
    return glob.glob(str(pipeline_root / "output_fused" / "**" / "*fused.pdf"),
                     recursive=True)


def _fused_candidates(form_id: str, fused: list[str]) -> list[str]:
    """All fused PDFs whose filename matches a published form_id, tolerating
    (I)/(T)/space suffixes. A variant id (e.g. `AF-101.vA`) has no fused PDF of
    its own; it shares the base form's layout, so fall back to the base id by
    stripping the `.vXX` suffix. Ordered least-decorated (shortest) first."""
    base = re.sub(r"\.v[A-Za-z0-9]+$", "", form_id)
    for fid in dict.fromkeys((form_id, base)):     # exact, then base; deduped
        cand = [f for f in fused
                if re.match(re.escape(fid) + r"[\s(._]", os.path.basename(f))
                or os.path.basename(f).startswith(fid + " ")]
        if cand:
            return sorted(cand, key=lambda p: len(os.path.basename(p)))
    return []


def _tree_type_mismatch(tree: dict, wid2geom: dict) -> int:
    """Count tree bindings whose widget type contradicts the node type: a
    text/date/currency node bound to a non-Text widget, or a select option
    bound to a Text widget. Used to pick the right fused PDF when a form-id has
    sibling candidates (e.g. a `Formal Petition` vs an `(I) Informal` PDF)."""
    texty = {"text", "date", "currency"}
    mm = 0
    for n in tree.get("nodes", []):
        nt = n.get("type", "text")
        for w in (n.get("widgets") or ([n["widget"]] if n.get("widget") else [])):
            g = wid2geom.get(w)
            if g and nt in texty and g[2] != "Text":
                mm += 1
        for o in n.get("options", []):
            for w in (o.get("widgets") or ([o["widget"]] if o.get("widget") else [])):
                g = wid2geom.get(w)
                if g and g[2] == "Text":
                    mm += 1
    return mm


def _find_fused(form_id: str, fused: list[str]) -> str | None:
    """Back-compat single-best match (shortest filename). For tree-aware
    selection across sibling PDFs use `_fused_candidates` + `_tree_type_mismatch`
    (see `build_geometry`)."""
    cands = _fused_candidates(form_id, fused)
    return cands[0] if cands else None


def _digest_geometry(fused_pdf: str):
    """W-id -> (page, rect, widget_type) from the fused PDF, reading order."""
    doc = fitz.open(fused_pdf)
    items = dig.extract_items(doc, include_widget_names=True)
    dig.assign_widget_ids(items)
    xref2geom = {w.xref: (p.number, [round(c, 1) for c in w.rect],
                          w.field_type_string)
                 for p in doc for w in (p.widgets() or [])}
    out = {it.widget_id: xref2geom[it.widget_xref]
           for it in items if it.kind == "widget" and it.widget_xref in xref2geom}
    return out, (round(doc[0].rect.width), round(doc[0].rect.height)), doc.page_count


def build_geometry(form_id: str,
                   pipeline_root: str | pathlib.Path = DEFAULT_PIPELINE) -> dict:
    pr = pathlib.Path(pipeline_root)
    tree_path = pr / "trees" / f"{form_id}.yaml"
    cands = _fused_candidates(form_id, _fused_list(pr))
    if not tree_path.exists() or not cands:
        return {"form_id": form_id, "_missing": (
            "no tree" if not tree_path.exists() else "no fused pdf")}
    tree = yaml.safe_load(tree_path.read_text())
    # When a form-id matches several fused PDFs (e.g. a Formal Petition and an
    # (I) Informal sibling that share the prefix), pick the one whose widget
    # types best fit the tree; ties keep the shortest filename (cands[0]).
    best = None
    for f in cands:
        w2, ps, npg = _digest_geometry(f)
        score = _tree_type_mismatch(tree, w2) if len(cands) > 1 else 0
        if best is None or score < best[0]:
            best = (score, f, w2, ps, npg)
    _, fused, wid2geom, page_size, n_pages = best

    fields: dict[str, dict] = {}
    rect_overrides = tree.get("rect_overrides") or {}
    for node in tree.get("nodes", []):
        nid = node.get("id")
        ntype = node.get("type", "text")
        if node.get("rect"):                          # injected: explicit rect,
            # no fused widget (a label/underline field the source PDF leaves
            # box-less; rect placed by the AcroForm inject technique).
            r = [round(float(c), 1) for c in node["rect"]]
            pg = int(node.get("page", 0))
            if pg < n_pages and r[2] > r[0] and r[3] > r[1]:
                fields[nid] = {"type": ntype, "injected": True,
                               "geometry_source": "manual",
                               "locked": True,
                               "widgets": [{"page": pg, "rect": r}]}
            continue
        node_wids = node.get("widgets") or (
            [node["widget"]] if node.get("widget") else [])
        if node_wids:                                 # text / date / currency
            rects = [{"page": wid2geom[w][0], "rect": wid2geom[w][1],
                      **({"geometry_source": "rect_override", "locked": True}
                         if w in rect_overrides else
                         {"geometry_source": "detected"})}
                     for w in node_wids if w in wid2geom]
            if rects:
                fields[nid] = {"type": ntype, "widgets": rects}
        elif node.get("options"):                     # select_one / select_many
            opts = []
            for o in node["options"]:
                wids = o.get("widgets") or ([o["widget"]] if o.get("widget")
                                            else [])
                for w in wids:
                    if w in wid2geom:
                        opts.append({"value": o.get("value"),
                                     "label": o.get("label"),
                                     "page": wid2geom[w][0],
                                     "rect": wid2geom[w][1],
                                     **({"geometry_source": "rect_override",
                                         "locked": True}
                                        if w in rect_overrides else
                                        {"geometry_source": "detected"})})
            if opts:
                fields[nid] = {"type": ntype, "options": opts}
    geometry = {"form_id": form_id,
            "coordinate_system": "pymupdf_top_left_points",
            "page_size": list(page_size),
            "n_pages": n_pages, "source_fused": os.path.basename(fused),
            "fields": fields}
    schema_path = PUBLISHED_ROOT / "repo" / "forms" / form_id / "schema.json"
    if schema_path.exists():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        with fitz.open(fused) as source_doc:
            geometry, changes = optimize_geometry(geometry, schema, source_doc)
        if changes:
            geometry["optimizer_changes"] = changes
    return geometry


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form")
    ap.add_argument("--out", required=True, help="output dir (one <form_id>.json each)")
    ap.add_argument("--pipeline-root", default=str(DEFAULT_PIPELINE),
                    help="dir holding trees/ + output_fused/ (the detection pipeline)")
    ap.add_argument("--ids-from", default="HEAD",
                    help="git ref to enumerate form ids from (default: current checkout)")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)

    if a.form:
        ids = [a.form]
    else:
        import subprocess
        files = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", a.ids_from, "repo/forms/"],
            text=True).splitlines()
        ids = sorted({f.split("/")[2] for f in files if f.endswith("schema.json")})

    ok = miss = 0; missing = []
    for fid in ids:
        g = build_geometry(fid, a.pipeline_root)
        if g.get("_missing") or not g.get("fields"):
            miss += 1; missing.append((fid, g.get("_missing") or "0 fields")); continue
        (out / f"{fid}.json").write_text(json.dumps(g, indent=2))
        ok += 1
    print(f"geometry written: {ok} | skipped: {miss}")
    for fid, why in missing:
        print(f"  skip {fid}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
