#!/usr/bin/env python3
"""Conservative, source-aware cleanup for generated fill geometry.

The optimizer only changes inferred rectangles. A field, widget, or option is
protected when it carries ``locked: true`` or a manual/override provenance.
This lets generator improvements coexist with hand-tuned geometry.
"""
from __future__ import annotations

import copy
import re

import fitz

MANUAL_SOURCES = {"manual", "hand_tuned", "rect_override"}
TEXT_TYPES = {"text", "date", "currency"}
COURT_ONLY_RE = re.compile(
    r"(?:judge|register|registrar|certified_copy|court_use|filing_fee|"
    r"mailing_notices_fee|publication_fee|abstracts_fee|surcharge|other_fee)",
    re.I,
)


def _protected(*items: dict) -> bool:
    return any(
        item.get("locked") is True
        or item.get("geometry_source") in MANUAL_SOURCES
        for item in items
        if isinstance(item, dict)
    )


def _schema_map(schema: dict) -> dict[str, dict]:
    return {field["field_id"]: field for field in schema.get("fields", [])}


def should_suppress(field_id: str, contract: dict) -> bool:
    """Return true for fields that should not receive a user-fillable widget."""
    if contract.get("suppress_geometry") is True:
        return True
    strategy = contract.get("fill_strategy") or {}
    if strategy.get("source") == "wet_ink":
        return True
    if contract.get("category") == "signature" and contract.get("type") == "signature":
        return True
    if strategy.get("source") == "left_blank" and COURT_ONLY_RE.search(field_id):
        return True
    if contract.get("court_only") is True:
        return True
    return False


def _words(page: fitz.Page) -> list[fitz.Rect]:
    out = []
    for word in page.get_text("words"):
        text = str(word[4]).strip()
        if len(text) < 2 or set(text) == {"_"}:
            continue
        out.append(fitz.Rect(word[:4]))
    return out


def _rules(page: fitz.Page) -> list[tuple[float, float, float]]:
    rules = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) <= 0.8 and abs(a.x - b.x) >= 18:
                    rules.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
            elif item[0] == "re":
                rect = item[1]
                if rect.height <= 1.5 and rect.width >= 18:
                    rules.append((rect.x0, rect.x1, (rect.y0 + rect.y1) / 2))
    return rules


def _trim_county(rect: fitz.Rect, page: fitz.Page) -> fitz.Rect:
    for word in page.get_text("words"):
        if str(word[4]).upper() != "COUNTY":
            continue
        wr = fitz.Rect(word[:4])
        same_row = min(rect.y1, wr.y1) - max(rect.y0, wr.y0) > 2
        if same_row and rect.x0 < wr.x0 and rect.x1 > wr.x0 - 4:
                rect.x1 = max(rect.x0 + 20, wr.x0 - 4)
    # Some forms print the underline and COUNTY as one text span
    # ("____________COUNTY"), so word extraction cannot separate them.
    try:
        raw = page.get_text("rawdict")
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    chars = span.get("chars") or []
                    text = "".join(char.get("c", "") for char in chars).upper()
                    pos = text.find("COUNTY")
                    if pos < 0 or pos >= len(chars):
                        continue
                    county_x = chars[pos]["bbox"][0]
                    span_rect = fitz.Rect(span["bbox"])
                    same_row = min(rect.y1, span_rect.y1) - max(rect.y0, span_rect.y0) > 2
                    if same_row and rect.x0 < county_x:
                        rect.x1 = min(rect.x1, county_x - 4)
    except Exception:
        pass
    return rect


def _trim_header_underline(rect: fitz.Rect, page: fitz.Page) -> fitz.Rect:
    """Restrict a caption/header field to its printed underscore run."""
    runs = []
    try:
        raw = page.get_text("rawdict")
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    chars = span.get("chars") or []
                    start = None
                    for index, char in enumerate(chars + [{"c": ""}]):
                        if char.get("c") == "_":
                            start = index if start is None else start
                        elif start is not None:
                            first, last = chars[start], chars[index - 1]
                            run = fitz.Rect(
                                first["bbox"][0], span["bbox"][1],
                                last["bbox"][2], span["bbox"][3],
                            )
                            if (
                                min(rect.y1, run.y1) - max(rect.y0, run.y0) > 2
                                and min(rect.x1, run.x1) - max(rect.x0, run.x0) > 10
                            ):
                                runs.append(run)
                            start = None
    except Exception:
        return rect
    if runs:
        run = max(runs, key=lambda item: (rect & item).width)
        rect.x0 = max(rect.x0, run.x0)
        rect.x1 = min(rect.x1, run.x1)
    return rect


def _snap_single_line(rect: fitz.Rect, page: fitz.Page) -> fitz.Rect:
    candidates = [
        rule for rule in _rules(page)
        if abs(rule[2] - rect.y1) <= 9
        and min(rule[1], rect.x1) - max(rule[0], rect.x0) >= 12
    ]
    if not candidates:
        return rect
    x0, x1, y = max(
        candidates,
        key=lambda r: min(r[1], rect.x1) - max(r[0], rect.x0),
    )
    return fitz.Rect(max(rect.x0, x0), y - 13, min(rect.x1, x1), y)


def _avoid_following_text(rect: fitz.Rect, page: fitz.Page) -> fitz.Rect:
    """Keep a tall field from covering the next printed question or label."""
    if rect.height <= 24:
        return rect
    blockers = []
    for wr in _words(page):
        horizontal_overlap = min(rect.x1, wr.x1) - max(rect.x0, wr.x0)
        # Wide narrative rectangles commonly begin after an inline label
        # ("Other ____") while the following numbered question begins at the
        # left margin. Treat any following printed row as a blocker for these
        # page-wide fields.
        if horizontal_overlap < 8 and rect.width < 250:
            continue
        if rect.y0 + 4 < wr.y0 < rect.y1:
            blockers.append(wr.y0)
    if not blockers:
        return rect
    new_bottom = min(blockers) - 2
    if new_bottom - rect.y0 >= 13:
        rect.y1 = new_bottom
    else:
        # A detector often emits a tall box beginning on a short "Other ___"
        # line. In that case use the first line only.
        rect.y1 = rect.y0 + 13
    return rect


def optimize_geometry(
    geometry: dict,
    schema: dict,
    doc: fitz.Document,
) -> tuple[dict, list[dict]]:
    """Return optimized geometry and a machine-readable change ledger."""
    result = copy.deepcopy(geometry)
    contracts = _schema_map(schema)
    changes: list[dict] = []

    for field_id in list(result.get("fields", {})):
        spec = result["fields"][field_id]
        contract = contracts.get(field_id, {})
        if should_suppress(field_id, contract) and not _protected(spec):
            if spec.get("widgets") or spec.get("options"):
                spec["widgets"] = []
                spec["options"] = []
                spec["geometry_source"] = "suppressed"
                changes.append({"field_id": field_id, "action": "suppress"})
            continue

        if spec.get("type") not in TEXT_TYPES or _protected(spec):
            continue
        for widget in spec.get("widgets") or []:
            if _protected(spec, widget):
                continue
            page = doc[int(widget["page"])]
            before = fitz.Rect(widget["rect"])
            after = fitz.Rect(before)
            if "county" in field_id.lower():
                after = _trim_county(after, page)
            if "caption" in field_id.lower() or field_id.startswith("estate_of_"):
                after = _trim_header_underline(after, page)
            after = _avoid_following_text(after, page)
            if after.width >= 18 and after.height >= 8:
                rounded = [round(value, 1) for value in after]
                if rounded != [round(value, 1) for value in before]:
                    widget["rect"] = rounded
                    widget.setdefault("geometry_source", "optimized_inferred")
                    changes.append({
                        "field_id": field_id,
                        "action": "trim",
                        "before": [round(value, 1) for value in before],
                        "after": rounded,
                    })
    return result, changes
