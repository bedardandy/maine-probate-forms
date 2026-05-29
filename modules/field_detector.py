"""Stage 3: Heuristic field detection from PDF analysis data.

Improvements over initial version:
- Radio button group detection from clustered checkboxes
- Multi-directional label search (above, left, below, right, inline)
- Section header fallback for context when direct label is missing
- Table column header association for table cell fields
- Implied field detection (whitespace gaps below labeled text)
"""

import logging
import re

import config
from modules.schema import (
    DetectedField,
    DrawingElement,
    FieldType,
    FormAnalysis,
    FormDetection,
    GroupRole,
    PageAnalysis,
    Rect,
    TextLine,
)

logger = logging.getLogger(__name__)


# ── Geometry helpers ───────────────────────────────────────────────────────


def _is_horizontal_line(elem: DrawingElement) -> bool:
    w = elem.rect.width
    h = elem.rect.height
    return (
        elem.kind in ("line", "rect")
        and w >= config.MIN_LINE_WIDTH
        and h <= config.MAX_LINE_HEIGHT
    )


def _is_checkbox_rect(elem: DrawingElement) -> bool:
    w = elem.rect.width
    h = elem.rect.height
    if w < config.CHECKBOX_MIN_SIZE or w > config.CHECKBOX_MAX_SIZE:
        return False
    if h < config.CHECKBOX_MIN_SIZE or h > config.CHECKBOX_MAX_SIZE:
        return False
    if w == 0 or h == 0:
        return False
    aspect = max(w, h) / min(w, h)
    return aspect <= (1.0 + config.CHECKBOX_ASPECT_TOLERANCE)


def _is_full_page_rule(elem: DrawingElement, page_width: float) -> bool:
    return elem.rect.width >= (page_width - 2 * config.FULL_PAGE_WIDTH_MARGIN)


def _horizontal_overlap(a: Rect, b: Rect) -> bool:
    return a.x0 < b.x1 and a.x1 > b.x0


def _vertical_overlap(a: Rect, b: Rect, margin: float = 10.0) -> bool:
    return a.y0 < (b.y1 + margin) and a.y1 > (b.y0 - margin)


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def _get_all_text_lines(page: PageAnalysis) -> list[TextLine]:
    lines = []
    for block in page.text_blocks:
        lines.extend(block.lines)
    return lines


def _get_all_horizontal_lines(page: PageAnalysis) -> list[DrawingElement]:
    return [
        d
        for d in page.drawings
        if _is_horizontal_line(d) and not _is_full_page_rule(d, page.width)
    ]


def _get_all_vertical_lines(page: PageAnalysis) -> list[DrawingElement]:
    return [
        d
        for d in page.drawings
        if d.kind in ("line", "rect")
        and d.rect.height >= config.MIN_LINE_WIDTH
        and d.rect.width <= config.MAX_LINE_HEIGHT
    ]


def _build_grid_line_set(
    h_lines: list[DrawingElement], v_lines: list[DrawingElement]
) -> set[int]:
    grid_indices: set[int] = set()
    for i, hl in enumerate(h_lines):
        intersections = 0
        for vl in v_lines:
            if (
                vl.rect.x0 >= hl.rect.x0 - config.TABLE_GRID_PROXIMITY
                and vl.rect.x0 <= hl.rect.x1 + config.TABLE_GRID_PROXIMITY
                and vl.rect.y0 <= hl.rect.y0 + config.TABLE_GRID_PROXIMITY
                and vl.rect.y1 >= hl.rect.y1 - config.TABLE_GRID_PROXIMITY
            ):
                intersections += 1
        if intersections >= config.MIN_TABLE_INTERSECTIONS:
            grid_indices.add(i)
    return grid_indices


# ── Enhanced label search ─────────────────────────────────────────────────


def _find_nearby_label(
    field_rect: Rect,
    text_lines: list[TextLine],
    search_above: float = config.LABEL_SEARCH_ABOVE,
    search_left: float = config.LABEL_SEARCH_LEFT,
    search_below: float = config.LABEL_SEARCH_BELOW,
    search_right: float = config.LABEL_SEARCH_RIGHT,
) -> str:
    """Find the nearest text label in any direction around a field.

    Search priority: left inline → above → below → right.
    Filters out section numbers (pure digits like '1.', '2.') and
    very short fragments.
    """
    candidates: list[tuple[float, str, str]] = []  # (distance, text, direction)

    for tl in text_lines:
        text = tl.text.strip()
        if not text or len(text) < 2:
            continue
        # Skip pure numbering like "1.", "2.", "3a."
        if re.match(r"^\d+[a-z]?\.?$", text):
            continue

        # LEFT: label right edge is to the left of field start, same row
        if (
            tl.bbox.x1 <= field_rect.x0 + 5
            and (field_rect.x0 - tl.bbox.x1) <= search_left
            and _vertical_overlap(tl.bbox, field_rect)
        ):
            dist = max(0, field_rect.x0 - tl.bbox.x1)
            candidates.append((dist, text, "left"))

        # ABOVE: label bottom above field top, with horizontal overlap
        elif (
            tl.bbox.y1 <= field_rect.y0 + 2
            and (field_rect.y0 - tl.bbox.y1) <= search_above
            and _horizontal_overlap(tl.bbox, field_rect)
        ):
            dist = max(0, field_rect.y0 - tl.bbox.y1)
            candidates.append((dist + 0.1, text, "above"))  # slight penalty vs left

        # BELOW: label top below field bottom
        elif (
            tl.bbox.y0 >= field_rect.y1 - 2
            and (tl.bbox.y0 - field_rect.y1) <= search_below
            and _horizontal_overlap(tl.bbox, field_rect)
        ):
            dist = max(0, tl.bbox.y0 - field_rect.y1)
            candidates.append((dist + 5.0, text, "below"))  # larger penalty

        # RIGHT: label left edge is to the right of field end (for checkboxes)
        elif (
            tl.bbox.x0 >= field_rect.x1 - 5
            and (tl.bbox.x0 - field_rect.x1) <= search_right
            and _vertical_overlap(tl.bbox, field_rect, margin=5.0)
        ):
            dist = max(0, tl.bbox.x0 - field_rect.x1)
            candidates.append((dist + 0.5, text, "right"))

    if not candidates:
        return ""

    # Return closest label
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


_SECTION_KEYWORDS = (
    "information",
    "petitioner",
    "co-petitioner",
    "respondent",
    "minor",
    "decedent",
    "guardian",
    "conservator",
    "heir",
    "devisee",
    "fiduciary",
    "attorney",
    "applicant",
    "ward",
    "estate",
    "interested person",
)


def _is_section_header_text(text: str) -> bool:
    """True if a line of text is plausibly a section heading.

    Real section headers on Maine probate forms are one of:
      - Numbered items with a short title: "1. Petitioner Information"
      - "<Category> Information:" pattern: "Co-Petitioner Information:"
      - All-caps headings: "PROPOSED GUARDIAN INFORMATION"

    Filtered out:
      - Plain field labels: "Name:", "Date of Birth:" (≤1 word after strip)
      - Body-text questions: "9. If the minor is 14 years of age..." (>7 words)
      - Bullet-list relief items not bound to fields: "2. Make the requested
        appointment; and" (no role keyword)
    """
    t = text.strip()
    if not t or len(t) < 5:
        return False
    # Reject statutory citations even when they look all-caps or short
    # ("M.R.S. § 3943(8).", "18-C M.R.S. § 5-210", "42 U.S.C. § 1983").
    if "§" in t:
        return False
    if re.search(r"\bM\.?\s*R\.?\s*S\.?\b", t) or re.search(r"\bU\.?\s*S\.?\s*C\.?\b", t):
        return False
    # Reject fillable underlines that read as all-caps headings
    # ("_______________________ COUNTY").
    if "___" in t:
        return False
    lower = t.lower()
    # Strip a leading number for word-count purposes; "1. Petitioner" should
    # be counted as one informative word, not two.
    stripped = re.sub(r"^\d+[a-z]?\.\s*", "", t).strip()
    n_words = len(stripped.split())

    # Numbered + short + mentions a section role + not a question/sentence.
    # Real numbered headings end in ":" or have no terminal punctuation;
    # bullet-list questions end in "?" or "." which we reject.
    if (
        re.match(r"^\d+[a-z]?\.\s+\S", t)
        and not stripped.endswith(("?", "."))
        and 2 <= n_words <= 7
        and any(kw in lower for kw in _SECTION_KEYWORDS)
    ):
        return True
    # All-caps heading (filters body text via mixed case). Require a role
    # keyword so the venue line ("PROBATE COURT", "DISTRICT COURT", "STATE
    # OF MAINE", "IN RE") doesn't masquerade as a section header.
    letters = [c for c in t if c.isalpha()]
    if (
        letters
        and all(c.isupper() for c in letters)
        and 2 <= len(t.split()) <= 6
        and any(kw in lower for kw in _SECTION_KEYWORDS)
    ):
        return True
    # "<role> Information:" — colon + role keyword + short.
    if (
        t.endswith(":")
        and n_words >= 2
        and len(t.split()) <= 6
        and any(kw in lower for kw in _SECTION_KEYWORDS)
    ):
        return True
    return False


def _find_section_header(
    field_rect: Rect,
    text_lines: list[TextLine],
) -> str:
    """Find the nearest section header above a field as fallback context."""
    best_header = ""
    best_y = -1.0

    for tl in text_lines:
        text = tl.text.strip()
        if not text:
            continue
        # Must be above the field
        if tl.bbox.y1 > field_rect.y0:
            continue
        # Within search range
        if (field_rect.y0 - tl.bbox.y1) > config.SECTION_HEADER_SEARCH:
            continue
        if _is_section_header_text(text) and tl.bbox.y0 > best_y:
            best_y = tl.bbox.y0
            # Preserve the leading number so multi-instance sections
            # ("1. Petitioner Information" / "2. Petitioner Information")
            # stay distinct downstream — stripping the number collapses
            # them to the same string and the VLM cannot disambiguate
            # Petitioner 1 from Petitioner 2.
            cleaned = text.strip()
            if len(cleaned) > 60:
                cleaned = cleaned[:60].rsplit(" ", 1)[0]
            best_header = cleaned

    return best_header


def _find_table_column_header(
    cell_rect: Rect,
    text_lines: list[TextLine],
    h_lines: list[DrawingElement],
) -> str:
    """Find the column header text above a table cell.

    Looks for text that is horizontally aligned with the cell and appears
    in the header row (above the first grid line).
    """
    candidates: list[tuple[float, str]] = []

    for tl in text_lines:
        text = tl.text.strip()
        if not text or len(text) < 2:
            continue
        # Must be above the cell
        if tl.bbox.y1 > cell_rect.y0 + 5:
            continue
        if (cell_rect.y0 - tl.bbox.y0) > config.TABLE_HEADER_SEARCH:
            continue
        # Must be horizontally within the column
        col_center = (cell_rect.x0 + cell_rect.x1) / 2
        if tl.bbox.x0 <= col_center <= tl.bbox.x1:
            dist = cell_rect.y0 - tl.bbox.y1
            candidates.append((dist, text))
        elif _horizontal_overlap(tl.bbox, cell_rect):
            # Partial overlap — less preferred
            dist = cell_rect.y0 - tl.bbox.y1
            candidates.append((dist + 50, text))

    if not candidates:
        return ""

    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


# ── Field type classification ─────────────────────────────────────────────


def _classify_text_field(label: str, line_rect: Rect) -> FieldType:
    if (
        _contains_keyword(label, config.SIGNATURE_KEYWORDS)
        and line_rect.width >= config.SIGNATURE_MIN_WIDTH
    ):
        return FieldType.SIGNATURE
    if _contains_keyword(label, config.DATE_KEYWORDS):
        return FieldType.DATE
    if _contains_keyword(label, config.CURRENCY_KEYWORDS):
        return FieldType.CURRENCY
    return FieldType.TEXT


# ── Core detectors ────────────────────────────────────────────────────────


def _detect_text_fields(
    page: PageAnalysis,
    page_num: int,
    text_lines: list[TextLine],
    h_lines: list[DrawingElement],
    grid_indices: set[int],
) -> list[DetectedField]:
    fields = []

    for i, hl in enumerate(h_lines):
        if i in grid_indices:
            continue

        label = _find_nearby_label(hl.rect, text_lines)
        section = _find_section_header(hl.rect, text_lines)

        # Fallback: section header used as label when no direct label found.
        # Section is also stored separately so the VLM gets both pieces.
        if not label:
            label = section

        field_type = _classify_text_field(label, hl.rect)

        field_rect = Rect(
            x0=hl.rect.x0,
            y0=hl.rect.y0 - config.FIELD_HEIGHT_ABOVE_LINE,
            x1=hl.rect.x1,
            y1=hl.rect.y1,
        )

        confidence = config.CONFIDENCE_HIGH if label else config.CONFIDENCE_MEDIUM

        fields.append(
            DetectedField(
                page=page_num,
                rect=field_rect,
                field_type=field_type,
                nearby_label=label,
                section_header=section,
                confidence=confidence,
                detection_source="heuristic",
            )
        )

    return fields


def _detect_checkboxes(
    page: PageAnalysis,
    page_num: int,
    text_lines: list[TextLine],
) -> list[DetectedField]:
    """Detect checkbox fields. Uses right-side label search for checkboxes."""
    fields = []

    for elem in page.drawings:
        if elem.kind != "rect":
            continue
        if not _is_checkbox_rect(elem):
            continue

        # Checkboxes typically have labels to the right, not left
        label = _find_nearby_label(
            elem.rect,
            text_lines,
            search_above=15.0,
            search_left=30.0,
            search_below=5.0,
            search_right=250.0,
        )
        section = _find_section_header(elem.rect, text_lines)

        # If no direct label, try section header
        if not label:
            label = section

        fields.append(
            DetectedField(
                page=page_num,
                rect=elem.rect,
                field_type=FieldType.CHECKBOX,
                nearby_label=label,
                section_header=section,
                confidence=config.CONFIDENCE_MEDIUM if label else config.CONFIDENCE_LOW,
                detection_source="heuristic",
            )
        )

    return fields


def _promote_radio_groups(fields: list[DetectedField]) -> list[DetectedField]:
    """Post-process checkboxes: promote clusters of nearby checkboxes with
    mutually-exclusive labels to radio button groups.

    Heuristics for radio groups:
    1. 2-5 checkboxes within RADIO_GROUP_MAX_DISTANCE vertical distance
    2. Same page, similar x-position (aligned vertically)
    3. Labels contain radio keywords (Yes/No, Male/Female, etc.)
    4. OR: labels share a common section header suggesting exclusive choice
    """
    checkboxes = [f for f in fields if f.field_type == FieldType.CHECKBOX]
    non_checkboxes = [f for f in fields if f.field_type != FieldType.CHECKBOX]

    if len(checkboxes) < 2:
        return fields

    # Group checkboxes by page and approximate x-position (within 20pt)
    groups: list[list[int]] = []
    used: set[int] = set()

    # Sort by page, then y position
    sorted_indices = sorted(
        range(len(checkboxes)),
        key=lambda i: (checkboxes[i].page, checkboxes[i].rect.y0),
    )

    for i in sorted_indices:
        if i in used:
            continue
        cb = checkboxes[i]
        group = [i]
        used.add(i)

        for j in sorted_indices:
            if j in used:
                continue
            other = checkboxes[j]
            if other.page != cb.page:
                continue
            # Check vertical proximity — must be within range of the group's span
            group_y0 = min(checkboxes[g].rect.y0 for g in group)
            max(checkboxes[g].rect.y1 for g in group)
            if (other.rect.y0 - group_y0) > config.RADIO_GROUP_MAX_DISTANCE:
                continue
            # Check horizontal alignment — similar x position
            if abs(other.rect.x0 - cb.rect.x0) > 30:
                continue
            group.append(j)
            used.add(j)

        if 2 <= len(group) <= 5:
            groups.append(group)

    # Evaluate each group for radio-ness
    promoted: set[int] = set()
    group_counter = 0

    for group in groups:
        labels = [checkboxes[i].nearby_label.lower() for i in group]
        all_labels = " ".join(labels)

        # Check if labels suggest mutually exclusive choices
        is_radio = False

        # Pattern 1: Contains classic radio keywords
        if any(kw in all_labels for kw in config.RADIO_KEYWORDS):
            is_radio = True

        # Pattern 2: Yes/No pair
        has_yes = any("yes" in label for label in labels)
        has_no = any("no" in label for label in labels)
        if has_yes and has_no:
            is_radio = True

        # Pattern 3: Exactly 2-3 options with short, distinct labels
        if len(group) <= 3 and all(len(label) < 30 for label in labels if label):
            non_empty = [label for label in labels if label]
            if len(non_empty) >= 2:
                is_radio = True

        if is_radio:
            group_counter += 1
            group_id = f"radio_group_{group_counter}"
            for idx in group:
                checkboxes[idx].field_type = FieldType.RADIO
                checkboxes[idx].group_id = group_id
                checkboxes[idx].group_role = GroupRole.RADIO
                # Heuristic option label is the nearby text; downstream
                # taxonomy/VLM passes typically replace this with a cleaner
                # snake_case name. Fall back to the index so each kid has
                # a distinct export value even without VLM input.
                option = labels[group.index(idx)] if labels else ""
                option = option.strip().lower().replace(" ", "_")[:32] or f"opt_{idx}"
                checkboxes[idx].group_option = option
                promoted.add(idx)

    return non_checkboxes + checkboxes


def _detect_case_number(
    page: PageAnalysis,
    page_num: int,
    text_lines: list[TextLine],
    h_lines: list[DrawingElement],
) -> list[DetectedField]:
    fields = []

    for tl in text_lines:
        if tl.bbox.y0 > config.HEADER_Y_THRESHOLD:
            continue
        if not _contains_keyword(tl.text, config.CASE_NUMBER_KEYWORDS):
            continue

        best_line = None
        best_dist = float("inf")
        for hl in h_lines:
            if hl.rect.y0 > config.HEADER_Y_THRESHOLD * 1.5:
                continue
            if _vertical_overlap(tl.bbox, hl.rect) and hl.rect.x0 >= tl.bbox.x1 - 10:
                dist = hl.rect.x0 - tl.bbox.x1
                if dist < best_dist:
                    best_dist = dist
                    best_line = hl
            elif (
                hl.rect.y0 >= tl.bbox.y1
                and hl.rect.y0 - tl.bbox.y1 < 20
                and _horizontal_overlap(tl.bbox, hl.rect)
            ):
                dist = hl.rect.y0 - tl.bbox.y1
                if dist < best_dist:
                    best_dist = dist
                    best_line = hl

        if best_line:
            field_rect = Rect(
                x0=best_line.rect.x0,
                y0=best_line.rect.y0 - config.FIELD_HEIGHT_ABOVE_LINE,
                x1=best_line.rect.x1,
                y1=best_line.rect.y1,
            )
            fields.append(
                DetectedField(
                    page=page_num,
                    rect=field_rect,
                    field_type=FieldType.TEXT,
                    nearby_label="Docket No.",
                    confidence=config.CONFIDENCE_HIGH,
                    detection_source="heuristic",
                )
            )

    return fields


def _detect_address_blocks(
    page: PageAnalysis,
    page_num: int,
    text_lines: list[TextLine],
    h_lines: list[DrawingElement],
    grid_indices: set[int],
) -> list[DetectedField]:
    fields = []

    address_label_rects = []
    for tl in text_lines:
        if _contains_keyword(tl.text, config.ADDRESS_KEYWORDS):
            address_label_rects.append(tl.bbox)

    if not address_label_rects:
        return fields

    non_grid = [hl for i, hl in enumerate(h_lines) if i not in grid_indices]
    sorted_lines = sorted(non_grid, key=lambda el: el.rect.y0)

    groups: list[list[DrawingElement]] = []
    current_group: list[DrawingElement] = []

    for hl in sorted_lines:
        if not current_group:
            current_group.append(hl)
            continue
        prev = current_group[-1]
        y_gap = hl.rect.y0 - prev.rect.y1
        x_overlap = min(hl.rect.x1, prev.rect.x1) - max(hl.rect.x0, prev.rect.x0)
        if 0 < y_gap < 30 and x_overlap > 50:
            current_group.append(hl)
        else:
            if len(current_group) >= config.ADDRESS_MIN_LINES:
                groups.append(current_group)
            current_group = [hl]

    if len(current_group) >= config.ADDRESS_MIN_LINES:
        groups.append(current_group)

    for group in groups:
        group_y0 = min(el.rect.y0 for el in group)
        group_y1 = max(el.rect.y1 for el in group)
        group_x0 = min(el.rect.x0 for el in group)
        group_x1 = max(el.rect.x1 for el in group)

        if group_y1 - group_y0 > config.ADDRESS_STACK_MAX_VERTICAL:
            continue

        near_label = False
        for lr in address_label_rects:
            if abs(lr.y1 - group_y0) < 40 and _horizontal_overlap(
                lr, Rect(x0=group_x0, y0=group_y0, x1=group_x1, y1=group_y1)
            ):
                near_label = True
                break

        if not near_label:
            continue

        for idx, hl in enumerate(group):
            suffix = ["street", "city_state_zip", "line3", "line4", "line5"][
                min(idx, 4)
            ]
            field_rect = Rect(
                x0=hl.rect.x0,
                y0=hl.rect.y0 - config.FIELD_HEIGHT_ABOVE_LINE,
                x1=hl.rect.x1,
                y1=hl.rect.y1,
            )
            fields.append(
                DetectedField(
                    page=page_num,
                    rect=field_rect,
                    field_type=FieldType.TEXT,
                    nearby_label=f"address_{suffix}",
                    confidence=config.CONFIDENCE_HIGH,
                    detection_source="heuristic",
                )
            )

    return fields


def _detect_table_cells(
    page: PageAnalysis,
    page_num: int,
    text_lines: list[TextLine],
    h_lines: list[DrawingElement],
    v_lines: list[DrawingElement],
    grid_indices: set[int],
) -> list[DetectedField]:
    """Detect table cells with improved column header association."""
    fields = []

    if not grid_indices:
        return fields

    grid_h = [h_lines[i] for i in sorted(grid_indices)]
    if len(grid_h) < 2:
        return fields

    grid_h.sort(key=lambda el: el.rect.y0)

    for row_idx in range(len(grid_h) - 1):
        top_line = grid_h[row_idx]
        bot_line = grid_h[row_idx + 1]
        row_y0 = top_line.rect.y1
        row_y1 = bot_line.rect.y0

        if row_y1 - row_y0 < 5:
            continue

        row_x_boundaries = set()
        for vl in v_lines:
            if (
                vl.rect.y0 <= row_y0 + config.TABLE_GRID_PROXIMITY
                and vl.rect.y1 >= row_y1 - config.TABLE_GRID_PROXIMITY
            ):
                row_x_boundaries.add(round(vl.rect.x0, 1))

        x_sorted = sorted(row_x_boundaries)
        if len(x_sorted) < 2:
            continue

        for col_idx in range(len(x_sorted) - 1):
            cell_rect = Rect(
                x0=x_sorted[col_idx] + 1,
                y0=row_y0 + 1,
                x1=x_sorted[col_idx + 1] - 1,
                y1=row_y1 - 1,
            )
            if cell_rect.width < 10 or cell_rect.height < 5:
                continue

            # First: check for text INSIDE the cell (pre-printed label)
            label = ""
            for tl in text_lines:
                if (
                    tl.bbox.x0 >= cell_rect.x0 - 5
                    and tl.bbox.x1 <= cell_rect.x1 + 5
                    and tl.bbox.y0 >= cell_rect.y0 - 2
                    and tl.bbox.y1 <= cell_rect.y1 + 2
                ):
                    label = tl.text.strip()
                    break

            # If no label inside, look for column header
            if not label:
                label = _find_table_column_header(cell_rect, text_lines, h_lines)

            # Add row index to help with disambiguation
            if label:
                label = f"{label} (row {row_idx + 1})"

            section = _find_section_header(cell_rect, text_lines)
            confidence = config.CONFIDENCE_MEDIUM if label else config.CONFIDENCE_LOW

            fields.append(
                DetectedField(
                    page=page_num,
                    rect=cell_rect,
                    field_type=_classify_text_field(label, cell_rect),
                    nearby_label=label,
                    section_header=section,
                    confidence=confidence,
                    detection_source="heuristic",
                )
            )

    return fields


def _detect_implied_fields(
    page: PageAnalysis,
    page_num: int,
    text_lines: list[TextLine],
    h_lines: list[DrawingElement],
    existing_fields: list[DetectedField],
) -> list[DetectedField]:
    """Detect fields implied by labeled text followed by whitespace (no drawn line).

    Many probate forms have numbered sections like:
        '1. Full legal name of Petitioner:'
    followed by a blank area for writing, but without an explicit drawn line.
    """
    fields = []

    # Find text lines that look like field labels (end with colon or contain
    # phrases like "name of", "address of", "date of")
    label_patterns = [
        r":$",  # ends with colon
        r"name of\b",
        r"address of\b",
        r"date of\b",
        r"telephone\b",
        r"phone\b",
        r"email\b",
    ]

    for tl in text_lines:
        text = tl.text.strip()
        if len(text) < 5:
            continue

        is_label = any(re.search(p, text, re.IGNORECASE) for p in label_patterns)
        if not is_label:
            continue

        # Check if there's already a detected field near this label
        already_covered = False
        for ef in existing_fields:
            if ef.page != page_num:
                continue
            # Check if any existing field is close to this label
            if abs(ef.rect.y0 - tl.bbox.y1) < 25 and _horizontal_overlap(
                ef.rect, tl.bbox
            ):
                already_covered = True
                break
            # Also check if label is to the left of an existing field on same line
            if (
                _vertical_overlap(ef.rect, tl.bbox, margin=5)
                and ef.rect.x0 > tl.bbox.x1 - 10
                and ef.rect.x0 - tl.bbox.x1 < config.LABEL_SEARCH_LEFT
            ):
                already_covered = True
                break

        if already_covered:
            continue

        # Check there's a gap below before the next text/drawing element
        next_y = page.height  # default to page bottom
        for other_tl in text_lines:
            if other_tl.bbox.y0 > tl.bbox.y1 + 5:
                if _horizontal_overlap(other_tl.bbox, tl.bbox):
                    next_y = min(next_y, other_tl.bbox.y0)
        for hl in h_lines:
            if hl.rect.y0 > tl.bbox.y1 + 5:
                if _horizontal_overlap(hl.rect, tl.bbox):
                    next_y = min(next_y, hl.rect.y0)

        gap = next_y - tl.bbox.y1
        if gap < config.IMPLIED_FIELD_MIN_GAP or gap > config.IMPLIED_FIELD_MAX_GAP:
            continue

        # Create an implied field in the gap
        field_width = min(config.IMPLIED_FIELD_WIDTH, page.width - tl.bbox.x0 - 40)
        field_rect = Rect(
            x0=tl.bbox.x0,
            y0=tl.bbox.y1 + 2,
            x1=tl.bbox.x0 + field_width,
            y1=next_y - 2,
        )

        # Clean up the label text
        label = re.sub(r"^\d+[a-z]?\.\s*", "", text).strip().rstrip(":")
        section = _find_section_header(field_rect, text_lines)

        fields.append(
            DetectedField(
                page=page_num,
                rect=field_rect,
                field_type=_classify_text_field(label, field_rect),
                nearby_label=label,
                section_header=section,
                confidence=config.CONFIDENCE_MEDIUM,
                detection_source="heuristic_implied",
            )
        )

    return fields


# ── Main detection orchestrator ───────────────────────────────────────────


def _detect_underscore_lines(
    page: PageAnalysis,
    page_num: int,
    text_lines: list[TextLine],
    existing_h_lines: list[DrawingElement],
) -> list[DetectedField]:
    """Synthesize text-input fields from runs of underscore characters.

    Many forms use ASCII '_' rendered as text instead of drawn underlines
    (e.g. N-115 'Estate of __________'). The base text-field detector only
    sees drawn lines, so it misses these entirely. Here we scan text spans
    for underscore runs ≥5 chars, approximate the run's pixel range using
    proportional character widths, and emit a DetectedField if it's wide
    enough and doesn't overlap an existing drawn line.
    """
    fields: list[DetectedField] = []
    for tb in page.text_blocks:
        for tl in tb.lines:
            for sp in tl.spans:
                if not sp.text:
                    continue
                # Find every contiguous underscore run in this span.
                for m in re.finditer(r"_{5,}", sp.text):
                    cw = (sp.bbox.x1 - sp.bbox.x0) / max(1, len(sp.text))
                    x0 = sp.bbox.x0 + m.start() * cw
                    x1 = sp.bbox.x0 + m.end() * cw
                    if (x1 - x0) < config.MIN_LINE_WIDTH:
                        continue
                    # Skip if a drawn line already lives at this y (don't dup).
                    line_y = sp.bbox.y1
                    if any(
                        abs(hl.rect.y0 - line_y) < 4
                        and not (hl.rect.x1 < x0 - 5 or x1 + 5 < hl.rect.x0)
                        for hl in existing_h_lines
                    ):
                        continue
                    field_rect = Rect(
                        x0=x0,
                        y0=sp.bbox.y0,
                        x1=x1,
                        y1=sp.bbox.y1,
                    )
                    label = _find_nearby_label(field_rect, text_lines)
                    section = _find_section_header(field_rect, text_lines)
                    fields.append(
                        DetectedField(
                            page=page_num,
                            rect=field_rect,
                            field_type=_classify_text_field(label, field_rect),
                            nearby_label=label,
                            section_header=section,
                            confidence=config.CONFIDENCE_HIGH if label else config.CONFIDENCE_MEDIUM,
                            detection_source="underscore-line",
                        )
                    )
    return fields


def _detect_glyph_checkboxes(
    page: PageAnalysis,
    page_num: int,
    text_lines: list[TextLine],
) -> list[DetectedField]:
    """Detect tiny square text glyphs that render as checkbox shapes.

    Wingdings/Symbol-font checkboxes are emitted by PyMuPDF as TextSpans
    with empty (or private-use-area) text and ~7-12pt square bboxes. The
    drawing-rect detector misses them because nothing is drawn — the glyph
    IS the checkbox. We require an adjacent label-like word to the right
    so we don't misclassify random small empty bboxes.
    """
    fields: list[DetectedField] = []
    seen: set[tuple[int, int]] = set()
    CHECKBOX_GLYPHS = {"☐", "☒", "✓", "✗", "❑", "❒", "□", "■", "◇", "◆"}

    for tb in page.text_blocks:
        for tl in tb.lines:
            for sp in tl.spans:
                w = sp.bbox.x1 - sp.bbox.x0
                h = sp.bbox.y1 - sp.bbox.y0
                if not (4 <= w <= 14 and 4 <= h <= 14):
                    continue
                if min(w, h) <= 0:
                    continue
                if max(w, h) / min(w, h) > 1.6:
                    continue
                stripped = sp.text.strip()
                # Accept: empty, known checkbox glyphs, or PUA wingdings codepoints
                ok = (
                    not stripped
                    or stripped in CHECKBOX_GLYPHS
                    or any(0xE000 <= ord(c) <= 0xF8FF for c in stripped)
                )
                if not ok:
                    continue
                key = (round(sp.bbox.x0 / 2), round(sp.bbox.y0 / 2))
                if key in seen:
                    continue
                seen.add(key)
                # Require a meaningful label word within 200pt to the right;
                # filters out random tiny empty positions in tables/headers.
                label = _find_nearby_label(
                    sp.bbox,
                    text_lines,
                    search_above=12.0,
                    search_left=30.0,
                    search_below=8.0,
                    search_right=220.0,
                )
                if not label:
                    continue
                section = _find_section_header(sp.bbox, text_lines)
                fields.append(
                    DetectedField(
                        page=page_num,
                        rect=sp.bbox,
                        field_type=FieldType.CHECKBOX,
                        nearby_label=label,
                        section_header=section,
                        confidence=config.CONFIDENCE_LOW,
                        detection_source="glyph-checkbox",
                    )
                )
    return fields


def detect_fields(analysis: FormAnalysis) -> FormDetection:
    """Run all heuristic detections on a form analysis."""
    all_fields: list[DetectedField] = []

    for page in analysis.pages:
        page_num = page.page_number
        text_lines = _get_all_text_lines(page)
        h_lines = _get_all_horizontal_lines(page)
        v_lines = _get_all_vertical_lines(page)
        grid_indices = _build_grid_line_set(h_lines, v_lines)

        # Core detectors
        text_fields = _detect_text_fields(
            page, page_num, text_lines, h_lines, grid_indices
        )
        checkbox_fields = _detect_checkboxes(page, page_num, text_lines)
        case_fields = _detect_case_number(page, page_num, text_lines, h_lines)
        address_fields = _detect_address_blocks(
            page, page_num, text_lines, h_lines, grid_indices
        )
        table_fields = _detect_table_cells(
            page, page_num, text_lines, h_lines, v_lines, grid_indices
        )
        underscore_fields = _detect_underscore_lines(
            page, page_num, text_lines, h_lines
        )
        glyph_checkbox_fields = _detect_glyph_checkboxes(page, page_num, text_lines)

        page_fields = (
            text_fields
            + checkbox_fields
            + case_fields
            + address_fields
            + table_fields
            + underscore_fields
            + glyph_checkbox_fields
        )

        # Implied field detection (fills gaps where no drawn line exists)
        implied_fields = _detect_implied_fields(
            page, page_num, text_lines, h_lines, page_fields
        )
        page_fields.extend(implied_fields)

        all_fields.extend(page_fields)

    # Post-processing: promote checkbox clusters to radio groups
    all_fields = _promote_radio_groups(all_fields)

    # Deduplicate
    all_fields = _deduplicate_fields(all_fields)

    return FormDetection(
        form_id=analysis.form_id,
        filename=analysis.filename,
        category=analysis.category,
        fields=all_fields,
    )


def _deduplicate_fields(
    fields: list[DetectedField], tolerance: float = 5.0
) -> list[DetectedField]:
    if not fields:
        return fields

    unique: list[DetectedField] = []
    for f in fields:
        is_dup = False
        for u in unique:
            if (
                f.page == u.page
                and abs(f.rect.x0 - u.rect.x0) < tolerance
                and abs(f.rect.y0 - u.rect.y0) < tolerance
                and abs(f.rect.x1 - u.rect.x1) < tolerance
                and abs(f.rect.y1 - u.rect.y1) < tolerance
            ):
                if f.confidence > u.confidence:
                    unique.remove(u)
                    unique.append(f)
                is_dup = True
                break
        if not is_dup:
            unique.append(f)

    return unique


def detect_all_forms(
    form_ids: list[str] | None = None, force: bool = False
) -> list[str]:
    """Run detection on all analyzed forms and save JSON output.

    Args:
        form_ids: If provided, only detect for these form IDs.
        force: If True, overwrite existing detection files.

    Returns:
        List of output JSON file paths.
    """
    from modules.pdf_analyzer import load_analysis

    config.DETECTION_DIR.mkdir(parents=True, exist_ok=True)
    output_paths = []

    if form_ids is None:
        analysis_files = sorted(config.ANALYSIS_DIR.glob("*.json"))
        form_ids = [f.stem for f in analysis_files]

    for form_id in form_ids:
        out_path = config.DETECTION_DIR / f"{form_id}.json"

        if out_path.exists() and not force:
            logger.debug("Skipping detection (exists): %s", form_id)
            output_paths.append(str(out_path))
            continue

        analysis = load_analysis(form_id)
        if analysis is None:
            logger.warning("No analysis found for %s, skipping", form_id)
            continue

        logger.info("Detecting fields: %s", form_id)
        detection = detect_fields(analysis)
        out_path.write_text(detection.model_dump_json(indent=2))
        output_paths.append(str(out_path))
        logger.info("  → %d fields detected", len(detection.fields))

    return output_paths


def load_detection(form_id: str) -> FormDetection | None:
    path = config.DETECTION_DIR / f"{form_id}.json"
    if not path.exists():
        return None
    return FormDetection.model_validate_json(path.read_text())
