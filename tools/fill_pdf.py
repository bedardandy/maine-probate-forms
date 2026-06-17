#!/usr/bin/env python3
"""Apply a fill plan to a flat probate PDF -> a real filled PDF.

Combines `tools/fill_plan.py` (field_id -> value) with the per-form
`fill_geometry.json` (field_id -> widget rects) to inject AcroForm widgets named
by field_id onto the fetched flat source and write the resolved values. This is
the probate analog of the court repo's `fill_form` PDF output.

    python3 tools/fill_pdf.py --form DE-101 --case case.json --out /tmp/DE-101.filled.pdf
    python3 tools/fill_pdf.py --form DE-101 --case case.json \
        --source "DE-101 (flat from source_url).pdf" --out /tmp/DE-101.filled.pdf

Flat PDFs are not shipped; with no --source the official PDF is fetched from
metadata.json.source_url (cached, manifest-verified — see tools/fetch.py). Text
fields and checkbox/radio options the plan resolved are written; narrative
fields the agent composed (placed under narrative_facts[field_id]) fold into the
resolved text. Not legal advice — verify against the official form.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

import fitz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from canonical_adapter import to_case_object       # noqa: E402
from fill_plan import build_plan, _render_value      # noqa: E402
import verify                                         # noqa: E402
import addendum                                       # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _strip_widgets(doc: fitz.Document) -> None:
    for page in doc:
        for w in list(page.widgets() or []):
            page.delete_widget(w)


# --- Consistent text rendering (A: font size, B: justification) ---------------
# A target size used for ALL text fields, decoupled from rect height so filled
# text is visually uniform across a form. We only shrink to fit (never grow),
# and only cap for unusually short boxes.
TARGET_FONTSIZE = 10.0
MIN_FONTSIZE = 6.0
_PAD = 2.0                 # horizontal padding assumed inside the widget, per side
_MULTILINE_MIN_H = 24.0    # a box taller than this is treated as a paragraph area

# Field-name tokens that denote money -> right-justify (digits read better flush
# right and line up in value columns). Word-boundary anchored to avoid matching
# substrings like "valid" or "evaluate".
_CURRENCY_RE = re.compile(
    r"(?:^|_)(?:value|val|amount|amt|fee|fees|penal_sum|penal|balance|income|"
    r"expense|expenses|salary|wage|wages|disbursement|disbursements|sum_numeric|"
    r"gross_value|net_value|estimated_maine_estate_tax)(?:$|_)", re.I)


_ALIGN_CONST = {"left": fitz.TEXT_ALIGN_LEFT, "center": fitz.TEXT_ALIGN_CENTER,
                "right": fitz.TEXT_ALIGN_RIGHT}


def _text_align(name: str) -> int:
    """Fallback name heuristic, used only when the declared map has no entry."""
    n = name.lower()
    if "caption" in n:
        return fitz.TEXT_ALIGN_CENTER
    if _CURRENCY_RE.search(n):
        return fitz.TEXT_ALIGN_RIGHT
    return fitz.TEXT_ALIGN_LEFT


def _load_alignment(form_id: str, root: pathlib.Path) -> dict[str, str]:
    """Declared per-field justification from catalog/field_alignment.json.

    Authoritative (derived from the schema data_type by author_field_align.py).
    Returns {field_id: 'center'|'right'}; absent fields default to left.
    """
    p = root / "catalog" / "field_alignment.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("forms", {}).get(form_id, {})
    except Exception:
        return {}


def _fontsize_for(value: str, r: fitz.Rect, multiline: bool) -> float:
    # Start at the target, capped only so a very short box can't clip vertically.
    fs = min(TARGET_FONTSIZE, max(MIN_FONTSIZE, r.height - 2))
    if multiline:
        return round(fs, 1)                      # let long text wrap; keep size
    avail = max(1.0, r.width - 2 * _PAD)
    try:
        text_w = fitz.get_text_length(value, fontname="helv", fontsize=fs)
    except Exception:
        text_w = len(value) * fs * 0.5
    if text_w > avail:                           # single line overflow -> shrink to fit
        fs = max(MIN_FONTSIZE, fs * avail / text_w)
    return round(fs, 1)


def _add_text(page: fitz.Page, rect, name: str, value: str,
              align: int | None = None) -> None:
    w = fitz.Widget()
    w.field_name = name
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    r = fitz.Rect(rect)
    w.rect = r
    sval = str(value)
    w.field_value = sval
    multiline = r.height > _MULTILINE_MIN_H
    if multiline:
        try:
            w.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE
        except Exception:
            w.field_flags = 1 << 12              # multiline flag bit
    # A: uniform target size, decoupled from box height; shrink only to fit width.
    w.text_fontsize = _fontsize_for(sval, r, multiline)
    annot = page.add_widget(w)
    # B: type-aware justification via /Q (1=center, 2=right). PyMuPDF bakes a
    # left-aligned appearance and omits /Q, so set it low-level; fill_pdf() flags
    # NeedAppearances so conforming viewers re-render aligned (verified: both
    # poppler and PyMuPDF's own renderer honor it). `align` comes from the
    # declared map; falls back to the name heuristic when not supplied.
    if align is None:
        align = _text_align(name)
    if align != fitz.TEXT_ALIGN_LEFT and annot is not None:
        try:
            page.parent.xref_set_key(annot.xref, "Q", str(int(align)))
        except Exception:
            pass


def _text_width(text: str, fontsize: float) -> float:
    try:
        return fitz.get_text_length(text, fontname="helv", fontsize=fontsize)
    except Exception:
        return len(text) * fontsize * 0.5


def _split_for_widgets(value: str, widgets: list[dict],
                       fontsize: float = TARGET_FONTSIZE) -> list[str]:
    """Greedy word-split of `value` across a continuation-widget chain.

    A field with several single-line widgets (a sentence continuing across
    printed lines) used to get the whole value in widget 0 — shrink-to-fit then
    crushed long values to 6pt while the continuation lines stayed empty. Fill
    each widget to its usable width at the target size instead; the last widget
    takes the remainder (its shrink-to-fit absorbs any overflow). A first
    widget tall enough to be a paragraph area keeps the whole value (it wraps).
    """
    if len(widgets) <= 1 or fitz.Rect(widgets[0]["rect"]).height > _MULTILINE_MIN_H:
        return [value]
    words = str(value).split()
    parts: list[str] = []
    idx = 0
    for i, w in enumerate(widgets):
        if i == len(widgets) - 1:
            parts.append(" ".join(words[idx:]))
            return parts
        avail = max(1.0, fitz.Rect(w["rect"]).width - 2 * _PAD)
        cur: list[str] = []
        while idx < len(words):
            cand = " ".join(cur + [words[idx]])
            if _text_width(cand, fontsize) > avail and cur:
                break
            cur.append(words[idx]); idx += 1
            if _text_width(" ".join(cur), fontsize) > avail:
                break        # single overlong word — let shrink-to-fit handle it
        parts.append(" ".join(cur))
        if idx >= len(words):
            parts.extend([""] * (len(widgets) - i - 1))
            return parts
    return parts


def _field_labels(form_id: str, root: pathlib.Path) -> dict[str, str]:
    """field_id -> printed label, for addendum page titles / subjects."""
    try:
        s = json.loads((root / "repo" / "forms" / form_id / "schema.json")
                       .read_text())
        return {f["field_id"]: f.get("label", "") for f in s.get("fields", [])}
    except Exception:
        return {}


_ARTICLE_RE = re.compile(r"^(the|a|an|all|any|each|its|his|her|their)\b", re.I)


def _subject_from_label(label: str, fid: str) -> str:
    """A noun phrase for 'See attached Addendum N for <subject>.'

    Field labels are descriptors ('Personal Property Surety 1 Description'), so
    lower-case the whole phrase for a natural reference ('the personal property
    surety 1 description'); leave already-lowercase / article-led labels alone."""
    s = (label or fid.replace("_", " ")).strip().rstrip(" :.")
    if s and not _ARTICLE_RE.match(s):
        s = "the " + s.lower()
    return s


def _overflow_content(val: str):
    """Split an overflowed value into addendum items, or keep it as prose.

    Newline- or '; '-delimited values are lists (heirs, recipients, property);
    a single block is prose. Lists become numbered items on the addendum."""
    v = str(val)
    if "\n" in v:
        items = [x.strip() for x in v.splitlines() if x.strip()]
        return items if len(items) >= 2 else v
    parts = [x.strip() for x in v.split(";") if x.strip()]
    return parts if len(parts) >= 2 else v


def _load_overflow_catalog(root: pathlib.Path) -> dict:
    """{field_id: spec} per form from catalog/overflow_fields.json (declares
    which fields route long/list/table content to an addendum)."""
    p = root / "catalog" / "overflow_fields.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("forms", {})
    except Exception:
        return {}


def _as_list(raw) -> list:
    """Coerce a raw field value into a list of items (for list/table modes).

    Accepts a Python list (kept), or a string delimited by newlines / ';' (each
    piece an item). A single piece -> a one-item list."""
    if isinstance(raw, (list, tuple)):
        return list(raw)
    s = str(raw or "")
    if "\n" in s:
        return [x.strip() for x in s.splitlines() if x.strip()]
    return [x.strip() for x in s.split(";") if x.strip()]


def _as_records(raw) -> list:
    """Coerce a raw group value into a list of attribute dicts.

    Accepts a list of dicts (kept), or a list of strings (each becomes
    {value: str} for a single-attribute group)."""
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        out.append(item if isinstance(item, dict) else {"value": str(item)})
    return out


def _group_note(doc, geom, g, attrs, cap, subject, no) -> None:
    """Draw a "See Addendum N for <subject>." note just below an overflowed grid.

    Locates the last grid row via the primary column's widget at i=capacity so the
    overflow is visible on the form page, not only on the addendum."""
    cols = g.get("columns") or list(attrs)
    prim = next((c for c in cols if c in attrs), None)
    tmpl = attrs.get(prim) if prim else None
    spec = geom.get(tmpl.format(i=cap)) if tmpl else None
    wdg = (spec or {}).get("widgets") or []
    if not wdg:
        return
    r = fitz.Rect(wdg[0]["rect"])
    try:
        page = doc[wdg[0]["page"]]
        page.insert_text((r.x0, r.y1 + 11),
                         f"See Addendum {no} for {subject}.",
                         fontsize=8.5, fontname="helv", color=(0, 0, 0))
    except Exception:
        pass


def _distribute_groups(groups: dict, raw_facts: dict, resolved: dict,
                       overflows: list, doc=None, geom=None) -> None:
    """Fill numbered repeating-group records (heir_1_name, distributee_3_addr…)
    from a structured list, spilling rows past the form's capacity to an addendum.

    Each group spec: {source, capacity, attrs:{attr: 'fid_{i}_template'},
    columns?, subject?, title?}. Records come from raw_facts[source] as a list of
    dicts; records 1..capacity are injected into `resolved` so the normal widget
    pass writes them, and records beyond capacity become an addendum entry plus an
    in-form "See Addendum N" note below the grid."""
    for entity, g in (groups or {}).items():
        records = _as_records(raw_facts.get(g.get("source", entity)))
        if not records:
            continue
        cap = int(g.get("capacity", len(records)))
        attrs = g.get("attrs", {})
        for i, rec in enumerate(records[:cap], 1):
            for attr, tmpl in attrs.items():
                v = rec.get(attr)
                if v not in (None, ""):
                    resolved[tmpl.format(i=i)] = _render_value(v)
        if len(records) > cap:
            cols = g.get("columns") or list(attrs.keys())
            items = [" — ".join(str(rec.get(c, "")).strip() for c in cols
                                if str(rec.get(c, "")).strip())
                     for rec in records[cap:]]
            subject = g.get("subject") or f"additional {entity.replace('_', ' ')}"
            no = len(overflows) + 1
            overflows.append(addendum.make_entry(
                entity, g.get("title") or entity.replace("_", " ").title(),
                subject, items))
            if doc is not None and geom is not None:
                _group_note(doc, geom, g, attrs, cap, subject, no)


def _table_rows(raw, columns: list) -> list:
    """Normalise raw recipients into [{col_label: value}] for render_table.

    Each item may be a dict (keyed by column label), a [a, b] pair, or a string
    'name, address' (split on the first comma into the first two columns)."""
    rows = []
    labels = [c["label"] for c in columns]
    for item in _as_list(raw):
        if isinstance(item, dict):
            rows.append({k: item.get(k, "") for k in labels})
        elif isinstance(item, (list, tuple)):
            rows.append({labels[i]: (item[i] if i < len(item) else "")
                         for i in range(len(labels))})
        else:
            head, _, tail = str(item).partition(",")
            rows.append({labels[0]: head.strip(),
                         **({labels[1]: tail.strip()} if len(labels) > 1 else {})})
    return rows


def _add_checkbox(page: fitz.Page, rect, name: str) -> None:
    w = fitz.Widget()
    w.field_name = name
    w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    w.rect = fitz.Rect(rect)
    w.field_value = True
    page.add_widget(w)


def fill_pdf(form_id: str, case: dict, source_pdf: str | pathlib.Path,
             out_path: str | pathlib.Path,
             geometry_path: str | pathlib.Path | None = None,
             root: str | pathlib.Path = ROOT,
             verify_mode: str | None = None,
             overflow: bool = True) -> dict:
    root = pathlib.Path(root)
    geometry_path = pathlib.Path(geometry_path) if geometry_path else (
        root / "repo" / "forms" / form_id / "fill_geometry.json")
    if not geometry_path.exists():
        return {"ok": False, "error": f"no fill_geometry.json for {form_id} "
                "(plan-only form — cannot write a PDF)"}
    geom = json.loads(geometry_path.read_text())["fields"]
    plan = build_plan(form_id, case, root=root)
    if not plan.get("ok"):
        return plan
    resolved = plan["resolved"]

    # Guard: the source PDF must be the revision this form's geometry was
    # measured against (catalog/pdf_manifest.json). Otherwise the coordinates
    # can land text in the wrong place. Mismatch warns by default; set
    # MCF_VERIFY_BLANK=strict to refuse, =off to skip. `verify_mode` overrides
    # the env (e.g. the enhance pipeline verifies once at fetch time and fills
    # step-rewritten intermediates that can never match the manifest).
    mode = verify_mode or os.environ.get("MCF_VERIFY_BLANK", "warn")
    source_verified, verify_detail = verify.guard_pdf_detail(
        form_id, source_pdf, mode=mode)

    doc = fitz.open(str(source_pdf))
    # The source PDF must have every page the geometry references. If it doesn't,
    # the source is the wrong/outdated document (forms get re-paginated upstream);
    # fail with a diagnosable message instead of an opaque IndexError on doc[page].
    need = -1
    for spec in geom.values():
        for w in (spec.get("widgets") or []):
            if isinstance(w.get("page"), int):
                need = max(need, w["page"])
        for o in (spec.get("options") or []):
            if isinstance(o.get("page"), int):
                need = max(need, o["page"])
    if need >= doc.page_count:
        pc = doc.page_count
        doc.close()
        return {"ok": False, "error": f"source PDF has {pc} page(s) but "
                f"{form_id} geometry references page {need} — the source is "
                "likely outdated or the wrong document; re-fetch from "
                "metadata.json.source_url"}
    _strip_widgets(doc)
    base_pages = doc.page_count          # the form's own pages, before any addenda
    align_map = _load_alignment(form_id, root)
    labels = _field_labels(form_id, root)
    ov_cat = _load_overflow_catalog(root).get(form_id, {}) if overflow else {}
    raw_facts = case.get("narrative_facts") if isinstance(
        case.get("narrative_facts"), dict) else {}
    overflows: list[dict] = []
    written_text = checked = 0
    skipped_no_geom = []
    # Repeating groups: distribute a structured records list across the numbered
    # record fields (heir_1_name…), spilling past capacity to an addendum. Runs
    # first so injected record values are written by the normal field pass below.
    if overflow:
        _distribute_groups(ov_cat.get("_groups"), raw_facts, resolved, overflows,
                           doc=doc, geom=geom)
    # Table-mode fields (catalog) are drawn from their raw structured value, not
    # from a single geometry widget -- handle them up front so the stray widget
    # is not also written.
    for fid, tspec in ov_cat.items():
        if tspec.get("mode") != "table":
            continue
        rows = _table_rows(raw_facts.get(fid, resolved.get(fid, "")),
                           tspec["columns"])
        if not rows:
            continue
        pg = doc[tspec["page"]]
        if len(rows) > tspec["rows"]:
            no = len(overflows) + 1
            remainder = addendum.render_table(pg, tspec, rows, overflow_no=no)
            items = [" — ".join(str(r.get(c["label"], "")).strip()
                                for c in tspec["columns"]
                                if str(r.get(c["label"], "")).strip())
                     for r in remainder]
            overflows.append(addendum.make_entry(
                fid, labels.get(fid, "") or fid.replace("_", " "),
                tspec.get("subject") or _subject_from_label(labels.get(fid, ""), fid),
                items))
        else:
            addendum.render_table(pg, tspec, rows)
        written_text += min(len(rows), tspec["rows"])
    for fid, val in resolved.items():
        if ov_cat.get(fid, {}).get("mode") == "table":
            continue                                  # drawn above
        spec = geom.get(fid)
        if not spec:
            skipped_no_geom.append(fid); continue
        if spec.get("widgets"):                       # text field(s)
            align = _ALIGN_CONST.get(align_map.get(fid))   # None -> name heuristic
            # Overflow -> addendum: a single paragraph box (the box-below class)
            # whose value will not fit gets a "See attached Addendum N for ..."
            # reference; the full value moves to an appended continuation page.
            # A field marked mode:list routes to an addendum once it has 2+ items
            # even if one line would fit. Single-line / chain fields keep
            # shrink-to-fit + split.
            mode = ov_cat.get(fid, {}).get("mode")
            w0 = spec["widgets"][0]
            r0 = fitz.Rect(w0["rect"])
            is_list = mode == "list" and len(_as_list(raw_facts.get(fid, val))) >= 2
            doesnt_fit = (len(spec["widgets"]) == 1 and r0.height > _MULTILINE_MIN_H
                          and not addendum.fits(str(val), list(r0),
                                                _fontsize_for(str(val), r0, True),
                                                _PAD))
            if overflow and (is_list or doesnt_fit):
                no = len(overflows) + 1
                subject = (ov_cat.get(fid, {}).get("subject")
                           or _subject_from_label(labels.get(fid, ""), fid))
                _add_text(doc[w0["page"]], w0["rect"], fid,
                          addendum.field_reference(subject, no), align=align)
                written_text += 1
                content = (_as_list(raw_facts.get(fid, val)) if is_list
                           else _overflow_content(val))
                overflows.append(addendum.make_entry(
                    fid, labels.get(fid, "") or fid.replace("_", " "),
                    subject, content))
                continue
            parts = _split_for_widgets(str(val), spec["widgets"])
            for i, wdg in enumerate(spec["widgets"]):
                # value flows across the continuation chain width-by-width
                _add_text(doc[wdg["page"]], wdg["rect"],
                          fid if i == 0 else f"{fid}__{i}",
                          parts[i] if i < len(parts) else "", align=align)
                written_text += 1
        elif spec.get("options"):                     # choice field
            # A select_many list reaches here rendered as "a; b" (the plan's
            # _render_value coerces lists to display text) — split it back so
            # every selected option matches, not none of them.
            vals = val if isinstance(val, list) else re.split(r";\s*", str(val))
            wants = {str(v).strip().lower() for v in vals}
            single = len(spec["options"]) == 1
            for j, o in enumerate(spec["options"]):
                ov = str(o.get("value") or "").lower()
                hit = (ov in wants) or (single and str(val).lower() in
                                        ("true", "yes", "1", ov, "on"))
                if hit:
                    _add_checkbox(doc[o["page"]], o["rect"],
                                  f"{fid}__{o.get('value') or j}")
                    checked += 1

    # B: flag NeedAppearances so viewers regenerate field appearances honoring
    # the /Q justification set per-field above (PyMuPDF's baked appearance is
    # left-aligned). Viewers that don't regenerate fall back to left — same as
    # before, so this is safe.
    if written_text:
        try:
            doc.need_appearances(True)
        except Exception:
            pass

    # Append one addendum (1+ continuation sheets) per overflowed field, with a
    # "(continued)" heading and a footer page number continuing the form's pages.
    addenda = addendum.append_pages(doc, overflows, form_id,
                                    base_pages=base_pages) if overflows else {}

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return {
        "ok": True, "form_id": form_id, "out": str(out_path),
        "text_written": written_text, "options_checked": checked,
        "addenda": addenda,
        "source_verified": source_verified,
        "source_verify_detail": verify_detail,
        "resolved_without_geometry": skipped_no_geom,
        "coverage": plan["coverage"],
        "narrative": [n["field_id"] for n in plan["narrative"]],
        "note": "Draft. Narrative fields not yet composed stay blank; place them "
                "under narrative_facts[field_id] and re-run. Verify before filing.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--source", help="flat source PDF (from source_url); omit to "
                    "auto-fetch from metadata.json.source_url")
    ap.add_argument("--fetch", action="store_true",
                    help="re-download the flat source from source_url (bypass "
                    "the cache); implied when --source is omitted")
    ap.add_argument("--out", required=True)
    ap.add_argument("--geometry", help="override fill_geometry.json path")
    ap.add_argument("--no-addendum", action="store_true",
                    help="disable overflow -> addendum continuation pages")
    a = ap.parse_args()
    case = to_case_object(json.loads(pathlib.Path(a.case).read_text()))
    source = a.source
    if not source:
        from fetch import fetch_source            # manifest-verified fetch+cache
        try:
            source = str(fetch_source(a.form, fresh=a.fetch))
        except Exception as e:
            print(json.dumps({"ok": False,
                              "error": f"could not fetch source PDF: {e}"},
                             indent=2))
            return 1
    res = fill_pdf(a.form, case, source, a.out, a.geometry,
                   overflow=not a.no_addendum)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
