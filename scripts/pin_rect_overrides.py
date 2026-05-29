#!/usr/bin/env python3
"""Re-apply tree-yaml rect_overrides to a PDF as the FINAL widget-positioning
step, so manual rects win over snap_checkboxes and snap_text_fields output.

Used at the end of the per-form pipeline:
    apply_tree → restyle → gen_validation_js → add_validate_button
    → snap_checkboxes → snap_text_fields → pin_rect_overrides
"""
from __future__ import annotations
import argparse
import pathlib
import sys
import fitz
import yaml

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from apply_tree import (  # noqa: E402
    strip_legacy_groups, extract_items, assign_widget_ids,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("tree", type=pathlib.Path)
    args = ap.parse_args()
    if not args.pdf.exists() or not args.tree.exists():
        print("missing input", file=sys.stderr); return 2
    tree = yaml.safe_load(args.tree.read_text())
    overrides = tree.get("rect_overrides") or {}
    kill_list = tree.get("kill_widgets") or []
    if not overrides and not kill_list:
        print("no rect_overrides or kill_widgets")
        return 0
    # Build wid → (node_id, index_within_node) mapping. Multi-widget
    # nodes (e.g. petitioner_name_address_email: [W004, W005]) bind one
    # field_name to multiple PDF widgets via apply_tree's consolidate
    # pass. To pin a specific W-ID's rect when there are multiple
    # widgets sharing a name, we need both the name AND the index.
    wid_to_node: dict[str, tuple[str, int]] = {}
    for n in tree.get("nodes", []):
        if not isinstance(n, dict): continue
        wids: list[str] = []
        if n.get("widget"):
            wids = [n["widget"]]
        elif n.get("widgets"):
            wids = list(n["widgets"])
        for i, w in enumerate(wids):
            wid_to_node[w] = (n["id"], i)

    doc = fitz.open(args.pdf)
    # Apply each override by finding the widget whose name matches the
    # node bound to that W###.
    applied = 0
    for wid, rect in overrides.items():
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            continue
        mapping = wid_to_node.get(wid)
        if mapping is None:
            print(f"  pin {wid}: no tree node references this widget",
                  file=sys.stderr)
            continue
        node_id, idx = mapping
        x0, y0_t, x1, y1_t = rect
        # Find every widget bound to this node_id, sort by reading order
        # (page, y, x), and pin the one at `idx`. Reading order matches
        # how apply_tree's assign_widget_ids enumerates widgets when it
        # assigns W### labels, so W004 is the first widget for the node,
        # W005 the second, etc.
        candidates: list[tuple[int, float, float, fitz.Widget, fitz.Page]] = []
        for page in doc:
            for w in page.widgets():
                if w.field_name == node_id:
                    candidates.append(
                        (page.number, w.rect.y0, w.rect.x0, w, page))
        candidates.sort(key=lambda t: (t[0], t[1], t[2]))
        if idx >= len(candidates):
            print(f"  pin {wid}: index {idx} out of range for node "
                  f"{node_id!r} ({len(candidates)} widgets)",
                  file=sys.stderr)
            continue
        _, _, _, w, page = candidates[idx]
        ph = page.rect.height
        y_ll = ph - y1_t
        y_ur = ph - y0_t
        doc.xref_set_key(w.xref, "Rect", f"[{x0} {y_ll} {x1} {y_ur}]")
        applied += 1
    # Delete phantom widgets by source field name (kill_widgets: list of
    # field names that appear in the PDF — usually unbound widgets whose
    # composite source name survives apply_tree's rename pass).
    killed = 0
    if kill_list:
        kill_set = set(kill_list)
        for page in doc:
            kills = [w for w in page.widgets()
                     if w.field_name in kill_set]
            for w in kills:
                page.delete_widget(w)
                killed += 1
    doc.save(args.pdf, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    print(f"pinned {applied}/{len(overrides)} rect_overrides; "
          f"killed {killed}/{len(kill_list)} widgets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
