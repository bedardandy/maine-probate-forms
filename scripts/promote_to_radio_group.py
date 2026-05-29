"""Convert a set of independent checkbox widgets into a real PDF radio group.

The AcroForm pipeline writes checkboxes for the appointment-type triple
(Limited-Purpose / Standard / Expanded) — they end up with the right
visual appearance and the right positions, but each is an independent
checkbox: multiple can be checked, no parent links them, /T is per-kid.

This pass converts them into a proper PDF radio group:
  * One parent /Btn field with /T=<group_name>, /Ff=Radio, /V=/Off,
    /Kids=[kid_xrefs...]. Lives in /AcroForm/Fields.
  * Each kid:
      - Strip /T, /V, /Ff (these now live on the parent).
      - Add /Parent <parent_xref> 0 R.
      - Set /AS=/Off.
      - Rename AP/N entry from /Yes to /<group_option>.
  * Update /AcroForm/Fields: remove kid xrefs, add parent xref.

Visual stays as a square checkbox (kids' AP streams are unchanged), but
behavior becomes a true radio group: clicking one selects it and the
parent /V is set to the matching on-state name; clicking another clears
the first because the reader walks the /Kids list and resets siblings.

We pick groups via a "names sharing a prefix" heuristic — for example,
appointment_type_limited_purpose / _standard / _expanded all map to
parent T="appointment_type" with options limited_purpose/standard/expanded.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import fitz


# Hard-coded for now. When the validator emits group_id reliably we'll read
# this from the validation/naming JSON instead of pattern-matching names.
# Two forms supported:
#   ("name", [kid_field_names...])                  — match by field name
#   ("name", "by_position", page, y, [(x0, opt)..]) — match by widget rect
RADIO_GROUPS: list = [
    # PB-007 appointment-type triple. Detector + writer give it inconsistent
    # names across pipeline runs; identify by position (page 0, y≈181, three
    # x slots) and assign the option labels in visual order.
    ("appointment_type", "by_position", 0, 181.7, [
        (314.7, "limited_purpose"),
        (406.8, "standard"),
        (464.2, "expanded"),
    ]),
]


def _option_from_name(parent: str, kid_name: str) -> str:
    """Strip the parent prefix and underscore to recover the option label."""
    if kid_name.startswith(parent + "_"):
        return kid_name[len(parent) + 1:]
    return kid_name


def _resolve_kids_by_position(doc: fitz.Document, page_no: int,
                              y: float, x_options: list[tuple[float, str]],
                              y_tol: float = 2.0,
                              x_tol: float = 3.0) -> list[tuple[int, str]]:
    """Return [(xref, option_label), ...] for widgets matching the given x positions
    on the given page within tolerance. None for any unmatched slot."""
    page = doc[page_no]
    out: list[tuple[int, str]] = []
    for x_target, opt in x_options:
        match = None
        for w in (page.widgets() or []):
            if w.field_type_string not in ("CheckBox", "RadioButton"):
                continue
            r = w.rect
            if abs(r.y0 - y) > y_tol:
                continue
            if abs(r.x0 - x_target) > x_tol:
                continue
            match = w.xref
            break
        out.append((match, opt))
    return out


def promote_groups(doc: fitz.Document, groups: list) -> int:
    """Apply each group; return number of groups successfully converted."""
    # Build a name → xref map for the by-name path.
    name_to_xref: dict[str, int] = {}
    for pno in range(doc.page_count):
        for w in (doc[pno].widgets() or []):
            if w.field_name:
                name_to_xref.setdefault(w.field_name, w.xref)

    converted = 0
    for spec in groups:
        if len(spec) >= 2 and spec[1] == "by_position":
            parent_name, _, page_no, y, x_opts = spec
            resolved = _resolve_kids_by_position(doc, page_no, y, x_opts)
            kid_xrefs = [r[0] for r in resolved]
            options = [r[1] for r in resolved]
            if any(x is None for x in kid_xrefs):
                missing = [opt for kx, opt in resolved if kx is None]
                print(f"  [skip] {parent_name!r}: missing kids at "
                      f"page {page_no} y={y} options={missing}")
                continue
        else:
            parent_name, kid_names = spec[0], spec[1]
            kid_xrefs = []
            options = []
            ok = True
            for kn in kid_names:
                x = name_to_xref.get(kn)
                if x is None:
                    print(f"  [skip] {parent_name!r}: kid {kn!r} not found")
                    ok = False
                    break
                kid_xrefs.append(x)
                options.append(_option_from_name(parent_name, kn))
            if not ok:
                continue

        # Allocate a new xref for the parent field object.
        parent_xref = doc.get_new_xref()
        kids_arr = " ".join(f"{x} 0 R" for x in kid_xrefs)
        # FT=/Btn, Ff bit 16 = Radio (32768). Don't set NoToggleToOff so a
        # second click on the active option can clear it (acceptable for
        # appointment-type — none-selected is a valid state).
        parent_obj = (
            "<<\n"
            "/FT /Btn\n"
            f"/T ({parent_name})\n"
            "/Ff 32768\n"
            "/V /Off\n"
            f"/Kids [{kids_arr}]\n"
            ">>"
        )
        doc.update_object(parent_xref, parent_obj)

        # Rewrite each kid: rename AP/N on-state, strip /T /V /Ff, add /Parent,
        # reset /AS to /Off so the group starts unselected.
        for kid_xref, opt in zip(kid_xrefs, options):
            typ, val = doc.xref_get_key(kid_xref, "AP/N")
            if typ == "dict":
                # Replace any /Yes entry (and previous /<opt> if rerun) with /<opt>.
                # The on-state key in our writer's checkboxes is /Yes; if this
                # script is run twice, the second pass is a no-op.
                new_val = re.sub(r"/Yes(\b)", f"/{opt}\\1", val)
                if new_val != val:
                    doc.xref_set_key(kid_xref, "AP/N", new_val)
            doc.xref_set_key(kid_xref, "AS", "/Off")
            doc.xref_set_key(kid_xref, "Parent", f"{parent_xref} 0 R")
            # Erase per-kid field properties — these live on the parent now.
            for k in ("T", "V", "Ff"):
                try:
                    doc.xref_set_key(kid_xref, k, "null")
                except Exception:
                    pass

        # Update /AcroForm/Fields: remove kids, add parent.
        if not _update_acroform_fields(doc, kid_xrefs, parent_xref):
            print(f"  [warn] {parent_name!r}: failed to update /AcroForm/Fields; "
                  "Edge/Chrome may not see the radio group")

        print(f"  [ok]   {parent_name!r}: {len(kid_xrefs)} kids -> "
              f"options={options}, parent_xref={parent_xref}")
        converted += 1
    return converted


def _update_acroform_fields(doc: fitz.Document,
                            kid_xrefs: list[int], parent_xref: int) -> bool:
    """Remove kid_xrefs from /AcroForm/Fields and add parent_xref.

    Handles both forms of AcroForm:
      * Indirect ref:  catalog/AcroForm = "<n> 0 R" — set Fields on that xref.
      * Inline dict:   catalog/AcroForm = "<<...>>" — rewrite the whole dict
                       on the catalog because xref_set_key can't address
                       nested dict keys.

    Returns True if the array was successfully updated.
    """
    catalog = doc.pdf_catalog()
    typ, val = doc.xref_get_key(catalog, "AcroForm")

    if typ == "xref":
        m = re.match(r"(\d+)\s+\d+\s+R", val)
        if not m:
            return False
        af_xref = int(m.group(1))
        ftyp, fval = doc.xref_get_key(af_xref, "Fields")
        if ftyp != "array":
            return False
        new_arr = _rewrite_fields_array(fval, kid_xrefs, parent_xref)
        doc.xref_set_key(af_xref, "Fields", new_arr)
        return True

    if typ == "dict":
        # Inline AcroForm dict on the catalog. Extract /Fields[...], rewrite
        # the array, splice it back into the dict, then xref_set_key the
        # whole AcroForm value.
        m = re.search(r"/Fields\s*(\[[^\]]*\])", val, re.DOTALL)
        if not m:
            return False
        new_arr = _rewrite_fields_array(m.group(1), kid_xrefs, parent_xref)
        new_dict = val[:m.start(1)] + new_arr + val[m.end(1):]
        doc.xref_set_key(catalog, "AcroForm", new_dict)
        return True

    return False


def _rewrite_fields_array(arr_str: str, kid_xrefs: list[int],
                          parent_xref: int) -> str:
    """Take a "[a 0 R b 0 R ...]" Fields array, drop kid_xrefs, append parent."""
    inside = arr_str.strip()
    if inside.startswith("["):
        inside = inside[1:]
    if inside.endswith("]"):
        inside = inside[:-1]
    refs = re.findall(r"(\d+)\s+(\d+)\s+R", inside)
    keep = [(int(a), int(b)) for a, b in refs if int(a) not in kid_xrefs]
    keep.append((parent_xref, 0))
    return "[" + " ".join(f"{a} {b} R" for a, b in keep) + "]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()

    in_path = args.input
    if not in_path.exists():
        print(f"missing: {in_path}", file=sys.stderr)
        return 2
    out_path = args.out or in_path.with_name(in_path.stem + "_radios.pdf")

    doc = fitz.open(in_path)
    n = promote_groups(doc, RADIO_GROUPS)
    if out_path.resolve() == in_path.resolve():
        doc.saveIncr() if hasattr(doc, "saveIncr") else doc.save(
            out_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    else:
        doc.save(out_path, deflate=True)
    doc.close()
    print(f"\nConverted {n} radio group(s).")
    print(f"output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
