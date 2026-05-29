"""Apply a validated tree YAML to a PDF, producing a deterministic AcroForm.

This is the production form-fillable writer that replaces the VLM-driven
group-promotion path (`promote_from_validation.py`). The tree YAML is the
single source of truth; running this script with the same tree against
the same baseline PDF always produces the same fillable output.

Pipeline:

  1. **Strip phase** (idempotent reset)
     * Remove any /Btn pushbuttons (legacy "clr" reset buttons).
     * Remove any /Btn parent fields with /Kids (legacy radio parents)
       — un-link kids from /Parent and restore them as plain widgets.
     * Now we have a clean canvas with one widget per fillable region.

  2. **Widget identification**
     * Re-run the digest extractor on the cleaned PDF. This assigns
       W001..Wxxx in reading order — the SAME ID space the tree references.

  3. **Apply phase**
     * For each tree node:
       - text/date/currency: rename widget /T to <node_id>[_n]
       - select_one: build a real /Btn parent, link option widgets as kids
         (skip virtual options), patch /AP/N to use option values as on-states
       - select_many: rename each option's widget to <node_id>__<value>
       - enabler: rename widget to <node_id>
       - virtual nodes: no widget output (metadata only)

  4. **Save**
     * Existing post-passes (`restyle_check_appearance`, `add_radio_clear_buttons`)
       run AFTER this script in the build chain.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import fitz
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_form_digest import extract_items, assign_widget_ids  # noqa: E402


# ─── Strip phase ────────────────────────────────────────────────────────────


def _read_acroform_fields(doc: fitz.Document) -> tuple[int | None, list[int], str]:
    """Return (acroform_xref_or_None_if_inline, fields_xrefs, full_acroform_value).

    /AcroForm can be stored either as an indirect reference on the catalog
    or inline as a dict. This helper handles both.
    """
    cat = doc.pdf_catalog()
    typ, val = doc.xref_get_key(cat, "AcroForm")
    if typ == "xref":
        m = re.match(r"(\d+) \d+ R", val)
        if not m:
            return None, [], val
        af_xref = int(m.group(1))
        typ_f, fields_val = doc.xref_get_key(af_xref, "Fields")
        if typ_f != "array":
            return af_xref, [], val
        return af_xref, [int(r) for r in re.findall(r"(\d+) \d+ R", fields_val)], val
    if typ == "dict":
        m = re.search(r"/Fields\s*\[([^\]]*)\]", val)
        if not m:
            return None, [], val
        return None, [int(r) for r in re.findall(r"(\d+) \d+ R", m.group(1))], val
    return None, [], val


def _write_acroform_fields(doc: fitz.Document, af_xref: int | None,
                           xrefs: list[int]) -> None:
    """Replace /AcroForm/Fields with the given xref list (indirect or inline)."""
    new_str = " ".join(f"{x} 0 R" for x in xrefs)
    if af_xref is not None:
        doc.xref_set_key(af_xref, "Fields", f"[{new_str}]")
        return
    cat = doc.pdf_catalog()
    _, val = doc.xref_get_key(cat, "AcroForm")
    rewritten = re.sub(
        r"(/Fields\s*\[)[^\]]*(\])",
        lambda m: m.group(1) + new_str + m.group(2),
        val, count=1,
    )
    doc.xref_set_key(cat, "AcroForm", rewritten)


def strip_legacy_groups(doc: fitz.Document, verbose: bool = False) -> None:
    """Remove pushbuttons and unwrap radio /Btn parent fields."""
    af_xref, field_xrefs, _ = _read_acroform_fields(doc)
    if not field_xrefs:
        return

    keep: list[int] = []
    drop_pushbutton: list[int] = []
    radio_parents: list[int] = []
    for xr in field_xrefs:
        ft = doc.xref_get_key(xr, "FT")
        if ft != ("name", "/Btn"):
            keep.append(xr)
            continue
        ff = doc.xref_get_key(xr, "Ff")
        try:
            ffv = int(ff[1]) if ff[0] == "int" else 0
        except ValueError:
            ffv = 0
        kids = doc.xref_get_key(xr, "Kids")
        if ffv & 65536 and kids[0] != "array":
            # Pushbutton (no kids) — drop entirely.
            drop_pushbutton.append(xr)
            continue
        if kids[0] == "array":
            # Has kids: it's a parent. Whether radio or not, unwrap so the
            # tree-driven writer can rebuild structure cleanly.
            radio_parents.append(xr)
            continue
        keep.append(xr)

    # For each radio parent, un-link kids and add them back to /Fields.
    new_kid_xrefs: list[int] = []
    for parent_xref in radio_parents:
        kids_v = doc.xref_get_key(parent_xref, "Kids")
        if kids_v[0] != "array":
            continue
        kid_xrefs = [int(r) for r in re.findall(r"(\d+) \d+ R", kids_v[1])]
        for kx in kid_xrefs:
            # Restore minimal field info so the kid stands alone.
            try:
                doc.xref_set_key(kx, "Parent", "null")
            except Exception:
                pass
            # Don't bother restoring /T — apply phase will rename anyway.
            new_kid_xrefs.append(kx)
        if verbose:
            print(f"  unwrap radio parent xref={parent_xref} kids={kid_xrefs}")

    # For pushbuttons, drop them (and their page annotation entries).
    for xr in drop_pushbutton:
        # Remove from each page's /Annots that references xr.
        for pno in range(doc.page_count):
            page = doc[pno]
            page_xref = page.xref
            annots_v = doc.xref_get_key(page_xref, "Annots")
            if annots_v[0] == "array":
                refs = re.findall(r"(\d+) \d+ R", annots_v[1])
                if str(xr) in refs:
                    refs = [r for r in refs if int(r) != xr]
                    new_arr = "[" + " ".join(f"{r} 0 R" for r in refs) + "]"
                    doc.xref_set_key(page_xref, "Annots", new_arr)
        if verbose:
            print(f"  drop pushbutton xref={xr}")

    _write_acroform_fields(doc, af_xref, keep + new_kid_xrefs)


# ─── Apply phase ────────────────────────────────────────────────────────────


def _set_widget_name(doc: fitz.Document, xref: int, name: str) -> None:
    """Rename a widget's /T (field name)."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name)[:64]
    doc.xref_set_key(xref, "T", f"({safe})")


def apply_rect_overrides(doc: fitz.Document, tree: dict,
                          wid_to_xref: dict[str, int],
                          verbose: bool = False) -> int:
    """Apply per-widget rect overrides from `tree["rect_overrides"]`.

    Format: {Wxxx: [x0, y0, x1, y1]} in top-down PDF user coords (the
    same system PyMuPDF and the digest use). Used to fix bad upstream
    widget detection (e.g. a widget that spans two adjacent underscores)
    without re-running the source pipeline. Applied after strip so
    geometry changes are visible to subsequent snap passes."""
    overrides = tree.get("rect_overrides") or {}
    if not isinstance(overrides, dict):
        return 0
    # Map xref → page_idx so we can look up page height for the top-down
    # to bottom-up conversion. Pre-iterating page.widgets() to keep Widget
    # objects around hits a "not bound to a page" issue when calling
    # .update() later, so we use raw xref_set_key with manual coord math.
    xref_to_page_idx: dict[int, int] = {}
    for page in doc:
        for w in page.widgets():
            xref_to_page_idx[w.xref] = page.number
    applied = 0
    for wid, rect in overrides.items():
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            print(f"  rect_override {wid}: expected [x0,y0,x1,y1], got {rect}",
                  file=sys.stderr)
            continue
        xref = wid_to_xref.get(wid)
        page_idx = xref_to_page_idx.get(xref) if xref is not None else None
        if page_idx is None:
            print(f"  rect_override {wid}: widget not found in PDF",
                  file=sys.stderr)
            continue
        page_height = doc[page_idx].rect.height
        x0, y0_t, x1, y1_t = rect
        y_ll = page_height - y1_t
        y_ur = page_height - y0_t
        doc.xref_set_key(xref, "Rect",
                         f"[{x0} {y_ll} {x1} {y_ur}]")
        applied += 1
        if verbose:
            print(f"  rect_override {wid}: → ({x0},{y0_t},{x1},{y1_t})")
    return applied


def consolidate_same_named_widgets(doc: fitz.Document, verbose: bool = False) -> int:
    """Group top-level widgets sharing /T under a single parent /Btn field.

    Edge/PDFium does not auto-merge top-level widgets that happen to share
    a /T value — clicks on the second/third widget become no-ops. The PDF
    spec models this as one field with multiple /Kids widget annotations,
    so we promote every duplicate-named group into that shape:

      parent: { /T name, /FT /Btn, /Ff <copied>, /Kids [w1 w2 ...] }
      kid_i:  /Parent <parent>, /T removed (it's inherited)

    The widget xrefs themselves are unchanged — restyle and validate-button
    passes that run after this still see the same xrefs in the page /Annots.
    """
    af_xref, field_xrefs, _ = _read_acroform_fields(doc)
    if not field_xrefs:
        return 0

    by_name: dict[str, list[int]] = {}
    others: list[int] = []
    for xr in field_xrefs:
        subtype = doc.xref_get_key(xr, "Subtype")
        t_v = doc.xref_get_key(xr, "T")
        is_widget = subtype[0] == "name" and subtype[1] == "/Widget"
        if not is_widget or t_v[0] != "string":
            others.append(xr)
            continue
        name = t_v[1].strip("()")
        by_name.setdefault(name, []).append(xr)

    new_top_level: list[int] = list(others)
    consolidated = 0
    for name, xrefs in by_name.items():
        if len(xrefs) == 1:
            new_top_level.append(xrefs[0])
            continue
        ft_v = doc.xref_get_key(xrefs[0], "FT")
        ft_str = f"/{ft_v[1].lstrip('/')}" if ft_v[0] == "name" else "/Btn"
        ff_v = doc.xref_get_key(xrefs[0], "Ff")
        try:
            ff_int = int(ff_v[1]) if ff_v[0] == "int" else 0
        except ValueError:
            ff_int = 0

        parent_xref = doc.get_new_xref()
        kids_str = " ".join(f"{x} 0 R" for x in xrefs)
        doc.update_object(parent_xref, (
            "<<\n"
            f"/FT {ft_str}\n"
            f"/T ({name})\n"
            f"/Ff {ff_int}\n"
            f"/Kids [{kids_str}]\n"
            ">>"
        ))

        for kx in xrefs:
            # Remove /T from kid (inherited from parent now), set /Parent.
            doc.xref_set_key(kx, "T", "null")
            doc.xref_set_key(kx, "Parent", f"{parent_xref} 0 R")

        new_top_level.append(parent_xref)
        consolidated += 1
        if verbose:
            print(f"  consolidate '{name}': {len(xrefs)} widgets → parent xref={parent_xref}")

    _write_acroform_fields(doc, af_xref, new_top_level)
    return consolidated


def _option_widget_ids(opt: dict) -> list[str]:
    out: list[str] = []
    if opt.get("widget"):
        out.append(opt["widget"])
    out.extend(opt.get("widgets") or [])
    return out


def _node_widget_ids(node: dict) -> list[str]:
    out: list[str] = []
    if node.get("widget"):
        out.append(node["widget"])
    out.extend(node.get("widgets") or [])
    return out


def apply_tree(doc: fitz.Document, tree: dict,
               wid_to_xref: dict[str, int], verbose: bool = False) -> dict:
    """Walk tree and emit AcroForm structure. Returns a stats dict."""
    stats = {"text": 0, "select_one": 0, "select_many": 0,
             "enabler": 0, "virtual": 0, "missing": 0}

    for node in tree.get("nodes", []):
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        ntype = node.get("type")
        if not nid or not ntype:
            continue

        if node.get("virtual"):
            stats["virtual"] += 1
            continue

        if ntype in ("text", "date", "currency"):
            # All widgets under one node share the same /T. Consolidation
            # pass parents them so the value entered once auto-syncs to
            # every position (e.g. `case_title` repeated in section headings).
            # split them into separate nodes.
            wids = _node_widget_ids(node)
            for wid in wids:
                xref = wid_to_xref.get(wid)
                if xref is None:
                    stats["missing"] += 1
                    continue
                _set_widget_name(doc, xref, nid)
            stats["text"] += 1

        elif ntype == "enabler":
            wids = _node_widget_ids(node)
            for wid in wids:
                xref = wid_to_xref.get(wid)
                if xref is None:
                    stats["missing"] += 1
                    continue
                _set_widget_name(doc, xref, nid)
            stats["enabler"] += 1

        elif ntype == "select_many":
            for opt in node.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                val = opt.get("value", "opt")
                for wid in _option_widget_ids(opt):
                    xref = wid_to_xref.get(wid)
                    if xref is None:
                        stats["missing"] += 1
                        continue
                    _set_widget_name(doc, xref, f"{nid}__{val}")
            stats["select_many"] += 1

        elif ntype == "select_one":
            # Each option's widgets share a /T ("{nid}__{value}") and the
            # consolidate pass parents them. Cross-option mutex is enforced
            # at save time by the doc-level validator, not structurally.
            for opt in node.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                if opt.get("virtual"):
                    continue
                val = opt.get("value", "opt")
                for wid in _option_widget_ids(opt):
                    xref = wid_to_xref.get(wid)
                    if xref is None:
                        stats["missing"] += 1
                        continue
                    _set_widget_name(doc, xref, f"{nid}__{val}")
            stats["select_one"] += 1
            if verbose:
                opts = [o.get("value") for o in node.get("options") or []
                        if isinstance(o, dict) and not o.get("virtual")]
                print(f"  + select_one {nid}: {len(opts)} option(s) → independent CHKs")

    return stats


# ─── Entrypoint ─────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path,
                    help="Input PDF (may have legacy radio parents/pushbuttons; "
                         "they will be stripped first).")
    ap.add_argument("tree_yaml", type=pathlib.Path,
                    help="Validated tree YAML produced by build_form_tree.py.")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr); return 2
    if not args.tree_yaml.exists():
        print(f"missing: {args.tree_yaml}", file=sys.stderr); return 2

    tree = yaml.safe_load(args.tree_yaml.read_text())
    if not isinstance(tree, dict) or "nodes" not in tree:
        print("tree YAML missing top-level `nodes` list", file=sys.stderr); return 3

    doc = fitz.open(args.pdf)
    print(f"input: {args.pdf}  pages={doc.page_count}")

    if args.verbose:
        print("strip phase:")
    strip_legacy_groups(doc, verbose=args.verbose)

    # Re-extract widgets after strip — IDs match what the digest extractor saw.
    items = extract_items(doc)
    assign_widget_ids(items)
    wid_to_xref = {it.widget_id: it.widget_xref for it in items if it.kind == "widget"}
    print(f"resolved {len(wid_to_xref)} widgets after strip")

    n_overrides = apply_rect_overrides(doc, tree, wid_to_xref,
                                       verbose=args.verbose)
    if n_overrides:
        print(f"applied {n_overrides} rect override(s)")

    if args.verbose:
        print("apply phase:")
    stats = apply_tree(doc, tree, wid_to_xref, verbose=args.verbose)

    # Report any widgets in the form that no tree node references.
    bound: set[str] = set()
    for n in tree.get("nodes", []):
        if not isinstance(n, dict):
            continue
        if n.get("widget"):
            bound.add(n["widget"])
        bound.update(n.get("widgets") or [])
        for o in n.get("options") or []:
            if not isinstance(o, dict):
                continue
            if o.get("widget"):
                bound.add(o["widget"])
            bound.update(o.get("widgets") or [])
    unbound = [it for it in items
               if it.kind == "widget" and it.widget_id not in bound]
    stats["unbound"] = len(unbound)
    if unbound and args.verbose:
        print(f"unbound widgets ({len(unbound)} — kept as-is, not renamed):")
        for it in unbound:
            print(f"  {it.widget_id} {it.widget_type:11s} "
                  f"page={it.page + 1} rect=("
                  f"{it.x0:.0f},{it.y0:.0f},{it.x1:.0f},{it.y1:.0f})")

    if args.verbose:
        print("consolidate phase:")
    n_consolidated = consolidate_same_named_widgets(doc, verbose=args.verbose)
    stats["consolidated"] = n_consolidated

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.resolve() == args.pdf.resolve():
        doc.save(args.out, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    else:
        doc.save(args.out, deflate=True)
    doc.close()

    print("\nstats:", " ".join(f"{k}={v}" for k, v in stats.items()))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
