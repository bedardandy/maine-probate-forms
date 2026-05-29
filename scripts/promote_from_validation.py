"""Promote radio groups in a fused PDF based on a validation JSON's group_* fields.

Replaces the hand-coded RADIO_GROUPS table in promote_to_radio_group.py.
For every set of fields sharing a group_id with group_role=radio in the
validation JSON, this script:

  * Locates the matching widgets in the target PDF (by rect proximity since
    validation field names may differ from widget names — the writer's
    snake_case path can drift from the validator's annotations).
  * Builds a parent /Btn dict with /Kids pointing to the widgets.
  * Strips per-kid /T /V /Ff and adds /Parent.
  * Patches each kid's AP/N to use a unique on-state name (group_option).
  * Updates /AcroForm/Fields to swap kids for the parent.

This is the production path once the VLM consistently emits group_id /
group_role / group_option for radio groups.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.promote_to_radio_group import (  # noqa: E402
    _update_acroform_fields,
)


def _find_widget_by_rect(doc: fitz.Document, page_no: int,
                         rect: dict, tol: float = 2.0) -> int | None:
    """Find a widget xref whose rect approximately matches the given rect."""
    if page_no >= doc.page_count:
        return None
    page = doc[page_no]
    target = (rect["x0"], rect["y0"], rect["x1"], rect["y1"])
    for w in (page.widgets() or []):
        r = w.rect
        if (abs(r.x0 - target[0]) <= tol and abs(r.y0 - target[1]) <= tol
                and abs(r.x1 - target[2]) <= tol and abs(r.y1 - target[3]) <= tol):
            return w.xref
    return None


# Maximum global-y distance between two kids for them to be considered part
# of the same radio cluster. Tuned for letter-size forms: a typical numbered
# section is 80-120pt tall, and contiguous lettered subsections (A/B/C with
# their inline duties) span ~150-300pt. Anything farther (top-of-page summary
# vs Section 4 letters; petitioner-section vs respondent-section) is treated
# as logically separate.
CLUSTER_DISTANCE_PT = 300.0


def _cluster_by_proximity(members: list[dict],
                          page_offsets: list[float]) -> list[list[int]]:
    """Cluster members into spatially-close groups using transitive proximity.

    Returns indices grouped by cluster. Two members link if their bounding-box
    centers (computed in global y-coordinates spanning all pages) are within
    CLUSTER_DISTANCE_PT; transitive closure merges chains.
    """
    n = len(members)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    centers: list[tuple[float, float]] = []
    for m in members:
        gy = page_offsets[m["page"]] + 0.5 * (m["rect"]["y0"] + m["rect"]["y1"])
        cx = 0.5 * (m["rect"]["x0"] + m["rect"]["x1"])
        centers.append((cx, gy))

    for i in range(n):
        for j in range(i + 1, n):
            dx = centers[i][0] - centers[j][0]
            dy = centers[i][1] - centers[j][1]
            if (dx * dx + dy * dy) ** 0.5 < CLUSTER_DISTANCE_PT:
                union(i, j)

    by_root: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        by_root[find(i)].append(i)
    return list(by_root.values())


def promote_from_validation(target_pdf: pathlib.Path,
                            validation_json: pathlib.Path,
                            out_pdf: pathlib.Path,
                            verbose: bool = False) -> int:
    val = json.loads(validation_json.read_text())
    fields = val.get("fields", [])

    # Bucket fields with group_role=radio by group_id.
    radio_buckets: dict[str, list[dict]] = defaultdict(list)
    for f in fields:
        if f.get("group_role") == "radio" and f.get("group_id"):
            radio_buckets[f["group_id"]].append(f)

    if not radio_buckets:
        print("no radio groups found in validation JSON")
        return 0

    doc = fitz.open(target_pdf)

    # Pre-compute cumulative y offsets per page so we can talk about
    # cross-page proximity in one global axis.
    page_heights = [doc[i].rect.height for i in range(doc.page_count)]
    page_offsets = [0.0]
    for h in page_heights:
        page_offsets.append(page_offsets[-1] + h)

    # Robust dedupe — two passes:
    #
    # 1. Cross-cluster split: kids in spatially-distant clusters belong to
    #    different logical questions (top-of-page summary vs Section 4
    #    letters; petitioner-section vs respondent-section). Split into
    #    one radio group per cluster, suffixed group_id_<n>.
    # 2. Within-cluster option dedupe: even within a single cluster the
    #    VLM sometimes annotates two non-equivalent widgets (e.g. a
    #    checkbox AND an adjacent text field) with the same group_option.
    #    Keep only one widget per unique option, preferring the smallest
    #    (checkbox-sized) bbox — text fields lose to checkboxes.
    expanded_buckets: dict[str, list[dict]] = {}
    for gid, members in radio_buckets.items():
        clusters = _cluster_by_proximity(members, page_offsets)
        clusters.sort(key=lambda c: min(
            page_offsets[members[i]["page"]] + members[i]["rect"]["y0"]
            for i in c
        ))
        for n, idxs in enumerate(clusters, start=1):
            sub = [members[i] for i in idxs]
            # Within-cluster option dedupe.
            best_per_option: dict[str, dict] = {}
            for m in sub:
                opt = m.get("group_option", "")
                if not opt:
                    continue
                w = m["rect"]["x1"] - m["rect"]["x0"]
                h = m["rect"]["y1"] - m["rect"]["y0"]
                area = w * h
                cur = best_per_option.get(opt)
                # Prefer the smaller bbox (checkboxes are 10x10pt; text fields
                # are ~50x12). Among equally-small kids, prefer the first.
                if cur is None:
                    best_per_option[opt] = m
                else:
                    cw = cur["rect"]["x1"] - cur["rect"]["x0"]
                    ch = cur["rect"]["y1"] - cur["rect"]["y0"]
                    if area < cw * ch:
                        best_per_option[opt] = m
            kept = list(best_per_option.values())
            new_gid = gid if len(clusters) == 1 else f"{gid}_{n}"
            expanded_buckets[new_gid] = kept
        if verbose and len(clusters) > 1:
            print(f"  [split] {gid!r}: {len(members)} kids → "
                  f"{len(clusters)} clusters → "
                  f"{[f'{gid}_{i+1}' for i in range(len(clusters))]}")
        elif verbose and len(members) != len(expanded_buckets[gid]):
            print(f"  [dedupe] {gid!r}: {len(members)} kids → "
                  f"{len(expanded_buckets[gid])} after option dedupe")

    converted = 0
    for gid, members in expanded_buckets.items():
        if len(members) < 2:
            print(f"  [skip] {gid!r}: only {len(members)} kid(s) (need >=2)")
            continue
        # Resolve each member to a widget xref by rect match.
        kid_xrefs: list[int] = []
        options: list[str] = []
        missed = 0
        for m in members:
            x = _find_widget_by_rect(doc, m["page"], m["rect"])
            if x is None:
                missed += 1
                if verbose:
                    print(f"  [miss] {gid!r}: no widget at "
                          f"page={m['page']} rect={m['rect']} "
                          f"option={m.get('group_option')!r}")
                continue
            kid_xrefs.append(x)
            options.append(m.get("group_option") or m.get("nearby_label", "opt"))
        if len(kid_xrefs) < 2:
            print(f"  [skip] {gid!r}: only {len(kid_xrefs)} of "
                  f"{len(members)} kids resolved")
            continue

        parent_xref = doc.get_new_xref()
        kids_arr = " ".join(f"{x} 0 R" for x in kid_xrefs)
        doc.update_object(parent_xref, (
            "<<\n"
            "/FT /Btn\n"
            f"/T ({gid})\n"
            "/Ff 32768\n"
            "/V /Off\n"
            f"/Kids [{kids_arr}]\n"
            ">>"
        ))

        for kid_xref, opt in zip(kid_xrefs, options):
            typ, val_apn = doc.xref_get_key(kid_xref, "AP/N")
            if typ == "dict":
                new_val = re.sub(r"/Yes(\b)", f"/{opt}\\1", val_apn)
                if new_val != val_apn:
                    doc.xref_set_key(kid_xref, "AP/N", new_val)
            doc.xref_set_key(kid_xref, "AS", "/Off")
            doc.xref_set_key(kid_xref, "Parent", f"{parent_xref} 0 R")
            for k in ("T", "V", "Ff"):
                try:
                    doc.xref_set_key(kid_xref, k, "null")
                except Exception:
                    pass

        if not _update_acroform_fields(doc, kid_xrefs, parent_xref):
            print(f"  [warn] {gid!r}: /AcroForm/Fields update failed")
        print(f"  [ok]   {gid!r}: {len(kid_xrefs)} kids "
              f"({missed} missed) options={options}")
        converted += 1

    if out_pdf.resolve() == target_pdf.resolve():
        doc.save(out_pdf, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    else:
        doc.save(out_pdf, deflate=True)
    doc.close()
    return converted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("validation_json", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr); return 2
    if not args.validation_json.exists():
        print(f"missing: {args.validation_json}", file=sys.stderr); return 2
    out = args.out or args.pdf.with_name(args.pdf.stem + "_radios.pdf")
    n = promote_from_validation(args.pdf, args.validation_json, out, verbose=args.verbose)
    print(f"\nconverted {n} radio group(s)")
    print(f"output: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
