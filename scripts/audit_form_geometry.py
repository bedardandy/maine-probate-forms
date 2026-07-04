#!/usr/bin/env python3
"""Audit shipped form geometry against the official PDF's printed layout.

Flags high-value mapper failure modes:
  * generic/orphan field names;
  * wet-ink signature fields that still have PDF widgets;
  * choice widgets that are not checkbox-sized;
  * text widgets that are checkbox-sized;
  * county fields colliding with a printed COUNTY label;
  * text rectangles extending materially beyond nearby printed rules;
  * text rectangles extending past their printed blank (rules AND underscore
    runs), escalated when the overrun crosses printed words;
  * choice widgets sitting off a nearby printed checkbox (misanchored);
  * long printed rules with no overlapping field (missing-field candidates);
  * suspicious multi-widget chains with large unexplained gaps.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch import fetch_source  # noqa: E402

GENERIC_RE = re.compile(
    r"(?:^|_)(?:unlabeled|orphan|unknown|text_p\d|field_\d|certification_text)",
    re.I,
)


def horizontal_rules(page: fitz.Page) -> list[tuple[float, float, float]]:
    rules = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) <= 0.8 and abs(p2.x - p1.x) >= 25:
                    rules.append((min(p1.x, p2.x), max(p1.x, p2.x), p1.y))
            elif item[0] == "re":
                rect = item[1]
                if rect.width >= 25 and rect.height <= 1.5:
                    rules.append((rect.x0, rect.x1, rect.y0))
    return rules


# Box-outline glyphs some forms print for checkboxes (Wingdings/Dingbats
# private-use squares plus the Unicode ballot boxes).
_BOX_GLYPHS = {"\uf0a8", "\uf06f", "\uf071", "\u25a1", "\u25a2", "\u2610"}


def printed_checkboxes(page: fitz.Page) -> list[fitz.Rect]:
    """Printed checkbox outlines: small drawn rects, small closed paths
    (some forms stroke the four sides as line segments), and box glyphs."""
    boxes: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if 4 <= rect.width <= 18 and 4 <= rect.height <= 18:
            boxes.append(fitz.Rect(rect))
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    if char["c"] in _BOX_GLYPHS:
                        boxes.append(fitz.Rect(char["bbox"]))
    return boxes


def widgets(geometry: dict) -> list[dict]:
    out = []
    for field_id, spec in geometry["fields"].items():
        for index, item in enumerate(spec.get("widgets") or []):
            out.append(
                {
                    "field_id": field_id,
                    "kind": spec.get("type"),
                    "index": index,
                    "page": item["page"],
                    "rect": fitz.Rect(item["rect"]),
                }
            )
        for index, item in enumerate(spec.get("options") or []):
            out.append(
                {
                    "field_id": field_id,
                    "kind": "choice",
                    "index": index,
                    "page": item["page"],
                    "rect": fitz.Rect(item["rect"]),
                }
            )
    return out


def finding(form_id: str, code: str, severity: str, **detail) -> dict:
    return {"form_id": form_id, "code": code, "severity": severity, **detail}


def audit_form(form_id: str) -> list[dict]:
    package = ROOT / "repo" / "forms" / form_id
    geometry = json.loads((package / "fill_geometry.json").read_text())
    schema = json.loads((package / "schema.json").read_text())
    schema_by_id = {f["field_id"]: f for f in schema.get("fields", [])}
    doc = fitz.open(str(fetch_source(form_id)))
    all_widgets = widgets(geometry)
    findings = []

    # Two generated fields must never occupy the same meaningful area. Small
    # edge contacts are expected for table cells, so require positive overlap
    # in both dimensions and at least 12 square points.
    for index, left in enumerate(all_widgets):
        for right in all_widgets[index + 1:]:
            if left["page"] != right["page"]:
                continue
            # Two options of the same choice field may intentionally share a
            # printed box (a value that requires ticking a shared lead-in box
            # plus its own -- e.g. PB-007's written-report variants).
            if (
                left["field_id"] == right["field_id"]
                and left["kind"] == "choice"
                and right["kind"] == "choice"
            ):
                continue
            intersection = left["rect"] & right["rect"]
            if (
                intersection.width > 2
                and intersection.height > 2
                and intersection.get_area() >= 12
            ):
                findings.append(
                    finding(
                        form_id,
                        "widget_widget_collision",
                        "high",
                        page=left["page"] + 1,
                        field_id=left["field_id"],
                        other_field_id=right["field_id"],
                        overlap=[round(x, 1) for x in intersection],
                    )
                )

    for field_id, spec in geometry["fields"].items():
        if GENERIC_RE.search(field_id):
            findings.append(
                finding(form_id, "generic_or_orphan_name", "high", field_id=field_id)
            )
        contract = schema_by_id.get(field_id, {})
        is_date_field = (
            contract.get("type") == "date"
            or contract.get("data_type") == "date"
            or "date" in field_id.lower()
        )
        if (
            contract.get("category") == "signature"
            or contract.get("fill_strategy", {}).get("source") == "wet_ink"
        ) and not is_date_field and (spec.get("widgets") or spec.get("options")):
            findings.append(
                finding(form_id, "wet_ink_widget_present", "high", field_id=field_id)
            )
        chain = spec.get("widgets") or []
        for previous, current in zip(chain, chain[1:]):
            if (
                previous["page"] == current["page"]
                and current["rect"][1] - previous["rect"][3] > 22
            ):
                findings.append(
                    finding(
                        form_id,
                        "suspicious_widget_chain_gap",
                        "medium",
                        field_id=field_id,
                        gap=round(current["rect"][1] - previous["rect"][3], 1),
                    )
                )

    for item in all_widgets:
        rect = item["rect"]
        page = doc[item["page"]]
        if item["kind"] == "choice" and (rect.width > 24 or rect.height > 24):
            findings.append(
                finding(
                    form_id,
                    "choice_not_checkbox_sized",
                    "high",
                    field_id=item["field_id"],
                    page=item["page"] + 1,
                    rect=list(rect),
                )
            )
        if item["kind"] in ("text", "date") and rect.width <= 18 and rect.height <= 18:
            findings.append(
                finding(
                    form_id,
                    "text_is_checkbox_sized",
                    "high",
                    field_id=item["field_id"],
                    page=item["page"] + 1,
                    rect=list(rect),
                )
            )

        words = page.get_text("words")
        for word in words:
            wr = fitz.Rect(word[:4])
            text = word[4]
            semantic_text = text.strip("_").strip()
            intersection = rect & wr
            word_center = fitz.Point(
                (wr.x0 + wr.x1) / 2,
                (wr.y0 + wr.y1) / 2,
            )
            # Fields are intentionally placed above printed rules, but should
            # not cover native words. Ignore tiny antialiasing/edge contacts.
            if (
                item["kind"] in ("text", "date")
                and len(text.strip()) >= 2
                and set(text.strip()) != {"_"}
                and text.count("_") <= len(text) / 2
                and rect.contains(word_center)
                and intersection.get_area() >= 8
            ):
                findings.append(
                    finding(
                        form_id,
                        "widget_native_text_collision",
                        "high",
                        field_id=item["field_id"],
                        page=item["page"] + 1,
                        native_text=text,
                        overlap=[round(x, 1) for x in intersection],
                    )
                )
            same_line = min(rect.y1, wr.y1) - max(rect.y0, wr.y0) > 2
            if (
                "county" in item["field_id"].lower()
                and semantic_text == "COUNTY"
                and same_line
                and 0 <= wr.x0 - rect.x1 < 10
            ):
                if rect.x1 > wr.x0 - 2:
                    findings.append(
                        finding(
                            form_id,
                            "county_label_collision",
                            "high",
                            field_id=item["field_id"],
                            page=item["page"] + 1,
                            rect=list(rect),
                            county_x=round(wr.x0, 1),
                        )
                    )
                findings.append(
                    finding(
                        form_id,
                        "county_value_should_be_uppercase",
                        "medium",
                        field_id=item["field_id"],
                        page=item["page"] + 1,
                    )
                )

        # Paragraph boxes legitimately span several ragged answer rules, so
        # only single-line widgets are judged against a rule's x-extent.
        if item["kind"] in ("text", "date") and rect.height <= 20:
            nearby = [
                rule
                for rule in horizontal_rules(page)
                if abs(rule[2] - rect.y1) <= 7
                and min(rule[1], rect.x1) - max(rule[0], rect.x0) >= 15
            ]
            if nearby:
                rule = max(
                    nearby,
                    key=lambda r: min(r[1], rect.x1) - max(r[0], rect.x0),
                )
                left = rule[0] - rect.x0
                right = rect.x1 - rule[1]
                if left > 5 or right > 5:
                    findings.append(
                        finding(
                            form_id,
                            "widget_overruns_rule",
                            "medium",
                            field_id=item["field_id"],
                            page=item["page"] + 1,
                            left_overrun=round(left, 1),
                            right_overrun=round(right, 1),
                        )
                    )

    # Blank-anchored horizontal fit, over rules AND underscore-run blanks
    # (most of these forms print underscores; the rule check above misses
    # them). A single-line widget should start at its blank and stop at its
    # blank -- the union of same-row blanks tolerates multi-slot sentences.
    # Escalated to high when the overrun crosses printed words, because the
    # filled value will render on top of them.
    from snap_to_blank import blanks as page_blanks  # noqa: E402  (late: circular)

    blanks_cache = {i: page_blanks(doc[i]) for i in range(doc.page_count)}
    words_cache = {i: doc[i].get_text("words") for i in range(doc.page_count)}
    for item in all_widgets:
        if item["kind"] not in ("text", "date"):
            continue
        rect = item["rect"]
        if rect.height > 20:
            continue
        row = [
            (x0, x1, y)
            for x0, x1, y in blanks_cache[item["page"]]
            if x1 - x0 >= 18
            and abs(y - rect.y1) < 6
            and min(rect.x1, x1) - max(rect.x0, x0) > 15
        ]
        if not row:
            continue
        span_x0 = min(x0 for x0, _x1, _y in row)
        span_x1 = max(x1 for _x0, x1, _y in row)
        row_y = row[0][2]
        for edge, over in (("right", rect.x1 - span_x1), ("left", span_x0 - rect.x0)):
            if over <= 6:
                continue
            lo, hi = (span_x1, rect.x1) if edge == "right" else (rect.x0, span_x0)
            crossed = [
                w[4]
                for w in words_cache[item["page"]]
                if abs((w[1] + w[3]) / 2 - (row_y - 5)) < 7
                and w[0] < hi - 2
                and w[2] > lo + 2
                and set(w[4].strip()) != {"_"}
            ]
            findings.append(
                finding(
                    form_id,
                    "widget_overruns_blank",
                    "high" if crossed else "medium",
                    field_id=item["field_id"],
                    page=item["page"] + 1,
                    edge=edge,
                    overrun=round(over, 1),
                    into=" ".join(crossed[:4]),
                )
            )

    # A choice/enabler box should sit on the printed checkbox it answers.
    # Only flagged when a printed box is found nearby but offset -- if no box
    # is detectable at all (some forms draw them in ways the parser cannot
    # see), stay silent rather than guess. Likewise, if the nearest detected
    # box is already claimed by a different widget, this widget's own box was
    # probably just undetectable -- skip instead of blaming the neighbour's.
    boxes_cache = {i: printed_checkboxes(doc[i]) for i in range(doc.page_count)}
    check_items = [w for w in all_widgets if w["kind"] in ("choice", "enabler")]

    def _center(r):
        return ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)

    claimed = set()
    for item in check_items:
        c = _center(item["rect"])
        for bi, box in enumerate(boxes_cache[item["page"]]):
            bc = _center(box)
            if max(abs(bc[0] - c[0]), abs(bc[1] - c[1])) <= 4.5:
                claimed.add((item["page"], bi))
    for item in check_items:
        c = _center(item["rect"])
        best = None
        for bi, box in enumerate(boxes_cache[item["page"]]):
            bc = _center(box)
            d = max(abs(bc[0] - c[0]), abs(bc[1] - c[1]))
            if best is None or d < best[0]:
                best = (d, bi)
        if (
            best is not None
            and 4.5 < best[0] <= 40
            and (item["page"], best[1]) not in claimed
        ):
            findings.append(
                finding(
                    form_id,
                    "checkbox_off_printed_box",
                    "medium",
                    field_id=item["field_id"],
                    page=item["page"] + 1,
                    rect=[round(x, 1) for x in item["rect"]],
                    offset=round(best[0], 1),
                )
            )

    # Long uncovered rules are useful missing-field candidates. Suppress footer
    # rules and lines already substantially covered by any widget.
    for page_no, page in enumerate(doc):
        page_widgets = [w for w in all_widgets if w["page"] == page_no]
        for x0, x1, y in horizontal_rules(page):
            if y > page.rect.height - 45 or x1 - x0 < 65:
                continue
            covered = any(
                abs(w["rect"].y1 - y) <= 8
                and min(x1, w["rect"].x1) - max(x0, w["rect"].x0)
                >= 0.55 * (x1 - x0)
                for w in page_widgets
            )
            if not covered:
                findings.append(
                    finding(
                        form_id,
                        "uncovered_printed_rule",
                        "low",
                        page=page_no + 1,
                        rect=[round(x0, 1), round(y - 13, 1), round(x1, 1), round(y, 1)],
                    )
                )
    doc.close()
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forms", help="Comma-separated form IDs; default all")
    parser.add_argument(
        "--out", type=pathlib.Path, default=ROOT / "catalog" / "geometry_audit.json"
    )
    args = parser.parse_args()
    forms = (
        [x.strip() for x in args.forms.split(",")]
        if args.forms
        else sorted(p.parent.name for p in (ROOT / "repo" / "forms").glob("*/fill_geometry.json"))
    )
    findings = []
    for form_id in forms:
        findings.extend(audit_form(form_id))
    report = {
        "forms_audited": len(forms),
        "finding_count": len(findings),
        "by_code": {},
        "findings": findings,
    }
    for item in findings:
        report["by_code"][item["code"]] = report["by_code"].get(item["code"], 0) + 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "findings"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
