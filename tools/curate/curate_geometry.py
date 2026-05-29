#!/usr/bin/env python3
"""Hand-curation feedback loop for a form's field geometry.

When a form's auto-detected field placement is slightly off, the fastest fix is
often to *nudge it by hand* in any PDF editor — then teach the repo what you did.
This tool closes that loop:

    render  ->  (you hand-edit the PDF)  ->  diff  ->  apply

  render : emit a fillable PDF whose widgets are named by field_id at the current
           fill_geometry rects (over the real blank from metadata.source_url).
           Open it in Acrobat / LibreOffice Draw / Preview, drag a field to where
           it belongs, rename / add / delete fields, and save.

  diff   : read the edited PDF back, compare every widget to fill_geometry.json,
           and emit (a) a human-readable Markdown report of what moved / was
           renamed / added / removed, and (b) a machine-applicable override patch
           (--emit-override) in the same shape as fill_geometry.

  apply  : merge an override patch into the form's fill_geometry.json (writes a
           .bak first). Report-only by default — nothing is mutated unless you run
           this verb. The patch is small and reviewable, so it makes a clean PR.

Coordinates stay in PyMuPDF top-left point space throughout (the same convention
fill_pdf.py writes and CLAUDE.md documents), so a read-back is apples-to-apples.
Widget names follow fill_pdf.py: `field_id` (primary text widget),
`field_id__<n>` (continuation text widgets), `field_id__<value>` (option boxes).

    python3 -m tools.curate.curate_geometry render --form DE-101 --out edit.pdf
    #   ...hand-edit edit.pdf, save in place...
    python3 -m tools.curate.curate_geometry diff   --form DE-101 --edited edit.pdf \
        --emit-override DE-101.override.json
    python3 -m tools.curate.curate_geometry apply  --form DE-101 \
        --override DE-101.override.json

Not legal advice — geometry curation only changes where fields sit, not what
belongs on the form.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (maine-probate-forms-oss geometry curation)"}
DEFAULT_TOL = 0.5          # points; sub-tolerance nudges are treated as no-ops
RENAME_IOU = 0.5           # primary-rect overlap above which add+remove == rename


# --------------------------------------------------------------------------- io
def _root(arg_root: str | None) -> pathlib.Path:
    if arg_root:
        return pathlib.Path(arg_root)
    return pathlib.Path(__file__).resolve().parent.parent.parent


def _form_dir(root: pathlib.Path, form_id: str) -> pathlib.Path:
    d = root / "repo" / "forms" / form_id
    if not d.is_dir():
        sys.exit(f"no such form dir: {d}")
    return d


def _load(form_dir: pathlib.Path):
    geom = json.loads((form_dir / "fill_geometry.json").read_text())
    meta = json.loads((form_dir / "metadata.json").read_text())
    return geom, meta


def _fetch_blank(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


# ---------------------------------------------------------------- widget naming
def _parse_widget_name(name: str):
    """fill_pdf.py naming -> (base_field_id, suffix). suffix is None for the
    primary text widget, an int for a text continuation, or a str for an option."""
    if name is None:
        return None, None
    if "__" not in name:
        return name, None
    base, suf = name.rsplit("__", 1)
    if suf.isdigit():
        return base, int(suf)
    return base, suf


# ------------------------------------------------------------------ geom <-> pdf
def _spec_widgets(spec: dict):
    """Yield ('widget'|'option', key, page, rect) for a fill_geometry field spec.
    key: int index for text widgets, option value for choice fields."""
    for i, w in enumerate(spec.get("widgets", []) or []):
        yield "widget", i, w["page"], list(w["rect"])
    for j, o in enumerate(spec.get("options", []) or []):
        yield "option", str(o.get("value") or j), o["page"], list(o["rect"])


def _read_edited(edited_pdf: pathlib.Path) -> dict:
    """Read an edited AcroForm PDF -> {field_id: reconstructed fill_geometry spec}."""
    import fitz
    doc = fitz.open(str(edited_pdf))
    fields: dict = {}
    for pno, page in enumerate(doc):
        for wdg in page.widgets() or []:
            base, suf = _parse_widget_name(wdg.field_name)
            if not base:
                continue
            r = wdg.rect
            rect = [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)]
            is_checkbox = wdg.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX
            f = fields.setdefault(base, {"widgets": {}, "options": {}})
            if is_checkbox or isinstance(suf, str):
                f["options"][suf if suf is not None else "0"] = {"page": pno, "rect": rect}
            else:
                f["widgets"][suf or 0] = {"page": pno, "rect": rect}
    doc.close()

    # normalize the dict-of-positions into ordered fill_geometry specs
    out = {}
    for fid, f in fields.items():
        if f["options"]:
            opts = [{"value": v, "page": d["page"], "rect": d["rect"]}
                    for v, d in sorted(f["options"].items())]
            out[fid] = {"type": "select_one", "options": opts}
        else:
            wl = [f["widgets"][i] for i in sorted(f["widgets"])]
            out[fid] = {"type": "text", "widgets": wl}
    return out


# -------------------------------------------------------------------------- math
def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def _primary_rect(spec: dict):
    for w in spec.get("widgets", []) or []:
        return w["page"], w["rect"]
    for o in spec.get("options", []) or []:
        return o["page"], o["rect"]
    return None, None


def _delta(old, new):
    return [round(new[i] - old[i], 1) for i in range(4)]


def _moved(old_rect, new_rect, tol):
    return any(abs(new_rect[i] - old_rect[i]) > tol for i in range(4))


# -------------------------------------------------------------------------- diff
def diff(form_id, root, edited_pdf, tol):
    geom, _meta = _load(_form_dir(root, form_id))
    base = geom["fields"]
    edited = _read_edited(edited_pdf)

    moved, added, removed = {}, {}, []
    widget_changes = []   # (fid, kind, key, old_rect, new_rect, delta)

    base_ids, edit_ids = set(base), set(edited)
    common = base_ids & edit_ids

    for fid in sorted(common):
        b_pos = {(k, key): (pg, rc) for k, key, pg, rc in _spec_widgets(base[fid])}
        e_pos = {(k, key): (pg, rc) for k, key, pg, rc in _spec_widgets(edited[fid])}
        changed = False
        for slot, (bpg, brc) in b_pos.items():
            if slot not in e_pos:
                widget_changes.append((fid, slot[0], slot[1], brc, None, None))
                changed = True
                continue
            epg, erc = e_pos[slot]
            if epg != bpg or _moved(brc, erc, tol):
                widget_changes.append((fid, slot[0], slot[1], brc, erc, _delta(brc, erc)))
                changed = True
        for slot, (epg, erc) in e_pos.items():
            if slot not in b_pos:
                widget_changes.append((fid, slot[0], slot[1], None, erc, None))
                changed = True
        if changed:
            moved[fid] = edited[fid]

    for fid in sorted(edit_ids - base_ids):
        added[fid] = edited[fid]
    for fid in sorted(base_ids - edit_ids):
        removed.append(fid)

    # rename detection: removed base field whose primary rect overlaps an added one
    renamed = {}
    for old in list(removed):
        opg, orc = _primary_rect(base[old])
        if orc is None:
            continue
        best, best_iou = None, RENAME_IOU
        for new in list(added):
            npg, nrc = _primary_rect(added[new])
            if nrc is None or npg != opg:
                continue
            i = _iou(orc, nrc)
            if i > best_iou:
                best, best_iou = new, i
        if best:
            renamed[old] = best
            removed.remove(old)
            # carry the renamed field's geometry under its new name, so a
            # rename that also nudged the field keeps the new position.
            spec = added.pop(best, None)
            if spec is not None:
                moved[best] = spec

    return {
        "form_id": form_id,
        "moved": moved, "added": added, "removed": removed, "renamed": renamed,
        "widget_changes": widget_changes, "tol": tol,
    }


def render_report(d) -> str:
    L = [f"# Geometry curation diff — {d['form_id']}",
         f"\nTolerance: {d['tol']} pt. Coordinates are top-left points "
         f"`[x0, y0, x1, y1]`.\n"]
    n = (len(d["moved"]) + len(d["added"]) + len(d["removed"]) + len(d["renamed"]))
    if n == 0:
        L.append("**No changes** — the edited PDF matches fill_geometry.json.")
        return "\n".join(L)

    if d["renamed"]:
        L.append("## Renamed (same position, new field_id)\n")
        L.append("| old field_id | -> | new field_id |")
        L.append("| --- | --- | --- |")
        for o, nw in d["renamed"].items():
            L.append(f"| `{o}` | -> | `{nw}` |")
        L.append("")

    if d["widget_changes"]:
        L.append("## Moved / changed widgets\n")
        L.append("| field_id | slot | old rect | new rect | delta [dx0,dy0,dx1,dy1] |")
        L.append("| --- | --- | --- | --- | --- |")
        for fid, kind, key, old, new, dl in d["widget_changes"]:
            slot = f"{kind}:{key}"
            old_s = "—" if old is None else str(old)
            new_s = "—" if new is None else str(new)
            dl_s = "added" if old is None else ("removed" if new is None else str(dl))
            L.append(f"| `{fid}` | {slot} | {old_s} | {new_s} | {dl_s} |")
        L.append("")

    if d["added"]:
        L.append("## Added fields (not in current geometry)\n")
        for fid in sorted(d["added"]):
            pg, rc = _primary_rect(d["added"][fid])
            L.append(f"- `{fid}` — page {pg}, rect {rc}")
        L.append("")

    if d["removed"]:
        L.append("## Removed fields (in geometry, gone from edited PDF)\n")
        for fid in d["removed"]:
            L.append(f"- `{fid}`")
        L.append("")

    L.append("> Review, then turn this into a patch with `--emit-override`, and "
             "merge with the `apply` verb.")
    return "\n".join(L)


def build_override(d) -> dict:
    fields = {}
    fields.update(d["moved"])
    fields.update(d["added"])
    return {
        "form_id": d["form_id"],
        "_note": "Geometry override produced by curate_geometry diff. Merge with: "
                 "python3 -m tools.curate.curate_geometry apply --form "
                 f"{d['form_id']} --override <this file>",
        "fields": fields,
        "_removed": d["removed"],
        "_renamed": d["renamed"],
    }


# ------------------------------------------------------------------------ render
def render(form_id, root, out, source):
    import fitz
    fd = _form_dir(root, form_id)
    geom, meta = _load(fd)
    if source:
        pdf_bytes = pathlib.Path(source).read_bytes()
    else:
        url = meta.get("source_url")
        if not url:
            sys.exit("no source_url in metadata.json; pass --source <blank.pdf>")
        pdf_bytes = _fetch_blank(url)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    placed = 0
    for fid, spec in geom["fields"].items():
        for kind, key, pg, rc in _spec_widgets(spec):
            if pg >= doc.page_count:
                continue
            w = fitz.Widget()
            if kind == "option":
                w.field_name = f"{fid}__{key}"
                w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
            else:
                w.field_name = fid if key == 0 else f"{fid}__{key}"
                w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            w.rect = fitz.Rect(rc)
            w.field_value = "" if kind != "option" else False
            doc[pg].add_widget(w)
            placed += 1
    outp = pathlib.Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(outp))
    doc.close()
    print(f"{form_id}: wrote {placed} editable widgets -> {outp}")
    print("Hand-edit it in any PDF editor (move/rename/add/delete fields), save, "
          "then run the `diff` verb against it.")


# ------------------------------------------------------------------------- apply
def apply(form_id, root, override_path):
    fd = _form_dir(root, form_id)
    geom_path = fd / "fill_geometry.json"
    geom = json.loads(geom_path.read_text())
    ov = json.loads(pathlib.Path(override_path).read_text())
    if ov.get("form_id") and ov["form_id"] != form_id:
        sys.exit(f"override is for {ov['form_id']}, not {form_id}")

    fields = geom["fields"]
    renamed = ov.get("_renamed", {}) or {}
    removed = ov.get("_removed", []) or []
    patch = ov.get("fields", {}) or {}

    n_ren = n_rm = n_set = 0
    for old, new in renamed.items():
        if old in fields:
            fields[new] = fields.pop(old)
            n_ren += 1
    for fid in removed:
        if fields.pop(fid, None) is not None:
            n_rm += 1
    for fid, spec in patch.items():
        # preserve the original 'type' if the override defaulted it
        if fid in fields and spec.get("type") in (None, "text", "select_one"):
            spec = {**spec, "type": fields[fid].get("type", spec.get("type"))}
        fields[fid] = spec
        n_set += 1

    backup = geom_path.with_suffix(".json.bak")
    backup.write_text(geom_path.read_text())
    geom_path.write_text(json.dumps(geom, indent=2) + "\n")
    print(f"{form_id}: applied {n_set} field update(s), {n_ren} rename(s), "
          f"{n_rm} removal(s).")
    print(f"backup: {backup}")
    print("Re-run a fill to verify, then commit fill_geometry.json (delete the .bak).")


# --------------------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("render", help="emit an editable fielded PDF from current geometry")
    pr.add_argument("--form", required=True)
    pr.add_argument("--root")
    pr.add_argument("--out", required=True)
    pr.add_argument("--source", help="local blank PDF (else fetched from source_url)")

    pd = sub.add_parser("diff", help="diff an edited PDF against fill_geometry.json")
    pd.add_argument("--form", required=True)
    pd.add_argument("--root")
    pd.add_argument("--edited", required=True, help="the hand-edited PDF")
    pd.add_argument("--tol", type=float, default=DEFAULT_TOL,
                    help=f"ignore moves under this many points (default {DEFAULT_TOL})")
    pd.add_argument("--emit-override", help="write a mergeable override JSON here")
    pd.add_argument("--report", help="write the Markdown report here (else stdout)")

    pa = sub.add_parser("apply", help="merge an override patch into fill_geometry.json")
    pa.add_argument("--form", required=True)
    pa.add_argument("--root")
    pa.add_argument("--override", required=True)

    a = ap.parse_args()
    root = _root(a.root)

    if a.cmd == "render":
        render(a.form, root, a.out, a.source)
        return 0
    if a.cmd == "diff":
        d = diff(a.form, root, pathlib.Path(a.edited), a.tol)
        report = render_report(d)
        if a.report:
            pathlib.Path(a.report).write_text(report + "\n")
            print(f"report -> {a.report}")
        else:
            print(report)
        if a.emit_override:
            n = len(d["moved"]) + len(d["added"]) + len(d["removed"]) + len(d["renamed"])
            pathlib.Path(a.emit_override).write_text(
                json.dumps(build_override(d), indent=2) + "\n")
            print(f"\noverride ({n} change set(s)) -> {a.emit_override}")
        return 0
    if a.cmd == "apply":
        apply(a.form, root, a.override)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
