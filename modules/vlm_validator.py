"""Stage 4: Local-VLM validator — gates heuristic field proposals.

Per page:
  1. Render the PDF page as PNG, sized so the long edge ≤ 2400px.
  2. Send (page image, candidate field list with pixel bboxes) to the local
     llama-router (qwen3.6-35b + mmproj) over its OpenAI-compatible API.
  3. Parse the JSON-array response into per-candidate decisions.
  4. Filter the heuristic output: keep confirmed fields; attach the model's
     semantic name as `nearby_label` for the AcroForm writer downstream.

Model: qwen3.6-35b (vision-capable via mmproj-F16) hosted on the local
fleet's llama-router. The router systemd service auto-loads + auto-unloads
the model after 30 min idle, so cold-start adds ~10s on the first request
of a quiet period.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Optional

import fitz  # PyMuPDF
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

import config
from modules.schema import (
    DetectedField,
    FieldType,
    FormDetection,
    GroupRole,
    Rect,
    ValidationResult,
)

logger = logging.getLogger(__name__)


NAMING_SYSTEM_PROMPT = """You assign canonical names to AcroForm field candidates on Maine probate court PDF forms.

A geometric heuristic detected fillable regions on a page. For each candidate, look at the rendered page image at the candidate's pixel bbox and emit a snake_case semantic name + field_type. We are NOT gating; every candidate will be kept. Your job is naming + typing only.

Each candidate line gives you `section`, `nearby_label`, and `bbox_px`. USE THE SECTION CONTEXT when it is meaningful — it is the single biggest lever for disambiguating generic labels. Forms repeat "Name", "Address", "Date of Birth", "Telephone" across multiple sections (Petitioner / Co-Petitioner / Minor / Heir #1 / Conservator); the section heading is the only way to disambiguate.

`section` may be:
  (a) a role heading like "Petitioner Information", "Conservator Information", "Adoptee Information" — USE these.
  (b) a numbered role heading like "1. Petitioner Information" / "2. Petitioner Information" — USE these AND keep the instance number to disambiguate (`petitioner_1_name`, `petitioner_2_name`).
  (c) a doc title or all-caps boilerplate like "PROBATE COURT", "STATE OF MAINE", "IN RE:" — these aren't role context; rely on `nearby_label` and the visible image instead. Don't invent a `state_of_maine_<x>` prefix.
  (d) empty `(none)` — there is no section context. Use only `nearby_label` and visual context.

NAMING — snake_case semantic name:
- Prepend the section role when section is type (a) or (b): co_petitioner_name, minor_dob, heir_address_row3, conservator_telephone, petitioner_1_name, petitioner_2_dob.
- NEVER emit a bare generic name (`name`, `address`, `telephone`, `dob`) when role context is available — the downstream disambiguator produces useless suffixes like `name_2`.
- Reflect section/question number when visible at the field: q3_decedent_dob, section_b_attorney_phone.
- Tabular cells: prefix column role + row index: property_description_row1, value_row3, encumbrance_row7, heir_name_row4.
- Stable canonical names for structural fields regardless of section: docket_no, county_probate_court, case_caption, decedent_name, pr_full_legal_name.
- IGNORE nearby_label values that look like body-text paragraphs (>50 chars, ends with a period, or reads like a sentence) — they are not labels. Use the section heading + role from the image instead.
- If the field's purpose is genuinely unclear from the image AND no section is given, fall back to `<section>_field_<idx>` or — only as last resort — `text_field_p1_y200`. Never leave the name blank.

FIELD_TYPE — pick the most specific:
- text — handwritten/typed entry line
- checkbox — small square to be checked (independent or part of a multi-select set)
- radio — small square in a mutually-exclusive set (only one of N selectable)
- signature — long line near "Signature"/"Sign"/"Subscribed"
- date — line near "Date"/"DOB"/"MM/DD/YYYY"
- currency — line preceded by "$" or near "Amount"/"Value"/"Total"

GROUPING — for every checkbox/radio candidate, classify its group membership:
- group_role:
   "independent"    — a standalone box (e.g. "☐ I waive bond" by itself)
   "radio"          — mutually-exclusive: cues are "either/or", "one of",
                      "Yes ☐ No ☐", or a labeled question whose options are
                      logically alternatives like "Limited Purpose / Standard /
                      Expanded"
   "checkbox_group" — labeled multi-select set: cues are "Check all that
                      apply", "Investigate the following:", numbered/lettered
                      lists where multiple items can be true at once
- group_id: snake_case key shared across ALL siblings of the same question
   (e.g. "appointment_type" for the Limited/Standard/Expanded triple).
   Use the same group_id on every option of one question. Empty for
   independent boxes.
- group_option: snake_case label of THIS specific box within its group
   (e.g. "limited_purpose", "standard", "expanded"). Empty for
   independent and for non-checkbox/radio fields.
- parent_group_id: slash-separated path of ENCLOSING groups when this
   field's group is nested inside a larger choice. Forms often have
   "Section 4: choose A, B, or C" (a top-level radio) with sub-options
   under each letter (multi-select checkboxes that only apply if that
   letter is selected). The sub-options' immediate group_id describes
   THEIR set; parent_group_id locates them inside the outer choice.
   Example for a sub-checkbox under "B. Standard Appointment" of an
   "appointment_type" radio: group_id="standard_duties",
   group_role="checkbox_group", parent_group_id="appointment_type/standard".
   Empty when the group is top-level (no enclosing choice).

If you set field_type=radio you MUST set group_role=radio and provide a
group_id shared with the other options.

confidence 0.0-1.0 — your certainty about the naming AND grouping.

OUTPUT — return ONLY a raw JSON array, one object per candidate IN ORDER:
[{"index":0,"semantic_name":"docket_no","field_type":"text","confidence":0.95},
 {"index":3,"semantic_name":"appointment_type_limited_purpose","field_type":"radio","group_id":"appointment_type","group_role":"radio","group_option":"limited_purpose","confidence":0.92},
 {"index":4,"semantic_name":"appointment_type_standard","field_type":"radio","group_id":"appointment_type","group_role":"radio","group_option":"standard","confidence":0.92}, ...]

Omit group_* fields entirely when group_role=independent. No markdown fences, no prose, no decision field."""


GATING_SYSTEM_PROMPT = """You validate AcroForm field proposals on Maine probate court PDF forms.

A geometric heuristic detected candidate fillable regions on a page. For each candidate, look at the rendered page at the bbox and decide keep/reject, give a snake_case semantic name, and pick a field_type.

DEFAULT BIAS: when uncertain, KEEP. False positives are cheap to fix later via the alignment MCP; false negatives become missing fields the user has to add by hand.

KEEP these (writeable regions):
- Any horizontal underline above which a person would handwrite/type
- Any small (~6-14pt) square or empty box where a person would tick/check
- Cells inside an inventory/account/balance table — these are writeable even when bordered by visible grid lines. Tabular fields appear under column headers like "Description", "Value", "Amount", "Date", "Quantity", "Total", "Encumbrance", "Property Description". If the candidate is a cell in a numbered table row, KEEP it.
- Multi-line answer boxes for narrative responses ("Explain", "Describe", "List...")
- Long signature lines near "Signature"/"Sign"/"Subscribed"
- Header fields: docket no, case no, county, estate of, etc.

REJECT these (decorative only):
- Page-width horizontal rules used as section dividers (no associated label)
- Underlines that sit beneath a complete pre-printed sentence (decoration, not fillable)
- Frame/border decorations around a section with no internal structure
- Folio/page-number rules at the bottom margin
- Empty grid rectangles in a header/footer table with no associated column header text

NAMING — snake_case semantic name for kept fields:
- Reflect section/question number when present: q3_decedent_dob, section_b_attorney_phone
- Reflect the data role, not just the label text: attorney_phone (not phone), heir_name_row1 (not name)
- Tabular cells: prefix column + row index: property_description_row1, value_row3, encumbrance_row7
- Stable canonical names for structural fields: docket_no, county_probate_court, case_caption, decedent_name

FIELD_TYPE — pick the most specific:
- text — handwritten/typed entry line
- checkbox — small square to be checked (independent or multi-select)
- radio — small square in a mutually-exclusive set (only one of N selectable)
- signature — long line near "Signature"/"Sign"/"Subscribed"
- date — line near "Date"/"DOB"/"MM/DD/YYYY"
- currency — line preceded by "$" or near "Amount"/"Value"/"Total"

GROUPING — for every kept checkbox/radio candidate, classify its group:
- group_role: "independent" | "radio" | "checkbox_group"
   "radio" cues: "either/or", "one of", "Yes ☐ No ☐", labeled options that
     are logical alternatives (e.g. Limited Purpose / Standard / Expanded).
   "checkbox_group" cues: "Check all that apply", "Investigate the
     following:", numbered/lettered lists where multiple items can be true.
   "independent" otherwise.
- group_id: snake_case key shared across ALL siblings of one question.
- group_option: snake_case label of THIS box within the group.
- parent_group_id: slash-separated path of enclosing groups when nested
   (e.g. sub-checkboxes under a "choose A, B, or C" radio get
   parent_group_id="<radio_name>/<selected_option>"). Empty if top-level.
If field_type=radio, group_role MUST be "radio" and group_id MUST be set.

confidence 0.0-1.0 — certainty about keep/reject AND naming AND grouping.

OUTPUT — return ONLY a raw JSON array, one object per candidate IN ORDER:
[{"index":0,"decision":"keep","semantic_name":"docket_no","field_type":"text","confidence":0.95},
 {"index":1,"decision":"reject","semantic_name":"","field_type":"text","confidence":0.85},
 {"index":4,"decision":"keep","semantic_name":"appointment_type_standard","field_type":"radio","group_id":"appointment_type","group_role":"radio","group_option":"standard","confidence":0.9}, ...]

Omit group_* fields entirely when group_role=independent. No markdown fences, no prose."""


# ── Pydantic schemas (mirror the JSON shape) ──────────────────────────────


class FieldDecision(BaseModel):
    index: int
    decision: str = "keep"  # "keep" or "reject" — naming mode always keeps
    semantic_name: str = ""
    field_type: str = "text"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    # Group classification for checkbox/radio candidates. The model emits a
    # snake_case `group_id` shared across siblings of the same labeled
    # question, plus `group_role`:
    #   "independent"     — standalone box (default)
    #   "radio"           — mutually-exclusive set (one selectable)
    #   "checkbox_group"  — labeled multi-select set (zero+ selectable)
    # `group_option` is THIS box's snake_case option label
    # (e.g. "limited_purpose" for the "☐ Limited Purpose" box).
    group_id: str = ""
    group_role: str = "independent"
    group_option: str = ""
    # Slash-separated path of enclosing groups for nested structure.
    # Example: a sub-checkbox inside section "B. Standard Appointment" of a
    # top-level appointment-type radio gets parent_group_id="appointment_type/standard".
    # Empty when the field's group is top-level.
    parent_group_id: str = ""


# ── Heuristic-name sanitization ──────────────────────────────────────────


def _looks_like_body_text(s: str) -> bool:
    """True if the string looks like a wrapped sentence rather than a label.

    Body text leaks into nearby_label when no real label exists near a field
    and `_find_nearby_label` falls back to the closest paragraph above. Snake-
    casing a 200-char paragraph produces useless field names like
    `or_pension_amounts_as_well_as_the_source_and_amount_of_any_4`.

    Real labels are short noun phrases (1-4 words). Body text is 5+ words,
    long, or ends in punctuation.
    """
    s = s.strip()
    if not s:
        return False
    if len(s) > 50:
        return True
    if s.endswith((".", ",", ";")):
        return True
    # Snake_case body text loses spaces but keeps underscores; count those too.
    word_count = max(len(s.split()), s.count("_") + 1)
    if word_count >= 5:
        return True
    return False


def _heuristic_name_fallback(f) -> str:
    """Pick a usable heuristic-derived name when the VLM didn't supply one.

    Prefer nearby_label when it looks label-like; otherwise fall back to the
    section header so taxonomy.py has something semantic to work with rather
    than snake-casing a body-text paragraph.
    """
    label = (f.nearby_label or "").strip()
    section = (f.section_header or "").strip()
    if label and not _looks_like_body_text(label):
        return label
    if section and not _looks_like_body_text(section):
        return section
    return ""  # taxonomy.py will assign field_<n>


# ── Rendering ─────────────────────────────────────────────────────────────


def _render_page_png(pdf_path: str, page_num: int) -> tuple[bytes, float, float, float]:
    """Render at long-edge ≤ TARGET. Returns (png, zoom, w_pt, h_pt)."""
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    width_pt, height_pt = page.rect.width, page.rect.height
    zoom = config.RENDER_TARGET_LONG_EDGE_PX / max(width_pt, height_pt)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    doc.close()
    return pix.tobytes("png"), zoom, width_pt, height_pt


# ── Field-list serialization ──────────────────────────────────────────────


def _build_field_list_text(
    fields: list[DetectedField],
    page_num: int,
    zoom: float,
    img_w_px: int,
    img_h_px: int,
) -> tuple[str, list[DetectedField]]:
    page_fields = [f for f in fields if f.page == page_num]
    lines = [
        f"Page {page_num + 1} — {len(page_fields)} candidates from heuristic.",
        f"Image: {img_w_px}x{img_h_px} px (origin top-left).",
        "",
        "Candidates (return one decision per row, same order, indexed by `index`):",
    ]
    for idx, f in enumerate(page_fields):
        x0 = round(f.rect.x0 * zoom)
        y0 = round(f.rect.y0 * zoom)
        x1 = round(f.rect.x1 * zoom)
        y1 = round(f.rect.y1 * zoom)
        nearby = (f.nearby_label or "").strip()[:120] or "(none)"
        section = (f.section_header or "").strip()[:80] or "(none)"
        lines.append(
            f"[{idx}] heuristic_type={f.field_type.value} "
            f"bbox_px=[{x0},{y0},{x1},{y1}] "
            f"section={section!r} nearby_label={nearby!r}"
        )
    return "\n".join(lines), page_fields


# ── JSON-array parser (multi-strategy, copied pattern from prior Kimi version) ──


def _parse_decisions(raw: str) -> list[FieldDecision]:
    text = raw.strip()
    text = re.sub(r"```(?:json|JSON)?\s*\n?", "", text)
    # Trim leading prose so the array starts at position 0; tolerate missing `]`
    # (model may have hit max_tokens mid-array).
    lb = text.find("[")
    if lb < 0:
        logger.warning("No JSON array in VLM response (first 300): %s", text[:300])
        return []
    body = text[lb:]
    m_close = re.search(r"\][^\[\]]*$", body, re.DOTALL)
    closed = body[: m_close.end()] if m_close else body

    # Strategy 1: parse the closed (or whole) array as JSON, with trailing-comma fix.
    for attempt in (closed, re.sub(r",\s*([\]}])", r"\1", closed)):
        try:
            items = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        out = []
        for it in items:
            try:
                out.append(FieldDecision(**it))
            except (ValidationError, TypeError):
                continue
        if out:
            return out

    # Strategy 2: per-object extraction — works on truncated arrays.
    out = []
    for obj_str in re.findall(r"\{[^{}]+\}", body):
        try:
            it = json.loads(re.sub(r",\s*}", "}", obj_str))
            out.append(FieldDecision(**it))
        except (json.JSONDecodeError, ValidationError, TypeError):
            continue
    if out:
        logger.info("    recovered %d decisions via per-object fallback", len(out))
    else:
        logger.warning("Parse failed (first 300): %s", text[:300])
    return out


# ── Per-page query ────────────────────────────────────────────────────────


def _decide_page(
    client: OpenAI,
    pdf_path: str,
    page_num: int,
    fields: list[DetectedField],
) -> tuple[list[DetectedField], list[FieldDecision]]:
    page_fields = [f for f in fields if f.page == page_num]
    if not page_fields:
        return [], []

    img_bytes, zoom, w_pt, h_pt = _render_page_png(pdf_path, page_num)
    img_w_px = round(w_pt * zoom)
    img_h_px = round(h_pt * zoom)
    img_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")

    field_list_text, indexed = _build_field_list_text(
        fields, page_num, zoom, img_w_px, img_h_px
    )

    mode = os.environ.get("VLM_MODE", config.VLM_MODE).lower()
    system_prompt = NAMING_SYSTEM_PROMPT if mode == "naming" else GATING_SYSTEM_PROMPT
    source_label = "qwen-vl-named" if mode == "naming" else "qwen-vl-gated"

    response = client.chat.completions.create(
        model=config.VLM_MODEL,
        max_tokens=config.VLM_MAX_TOKENS,
        temperature=config.VLM_TEMPERATURE,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                    {"type": "text", "text": field_list_text},
                ],
            },
        ],
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    raw = response.choices[0].message.content or ""
    decisions = _parse_decisions(raw)
    decisions_by_idx = {d.index: d for d in decisions}

    kept: list[DetectedField] = []
    for local_idx, f in enumerate(indexed):
        d = decisions_by_idx.get(local_idx)
        if mode == "gating":
            if d is None or d.decision != "keep":
                continue
        # Naming mode: always keep, fall back to heuristic name+type when
        # the VLM didn't decide on this candidate. Reject nearby_label
        # values that look like body text (long, sentence-shaped) so they
        # don't get snake_cased into 200-char field names downstream.
        new_type = f.field_type
        new_name = _heuristic_name_fallback(f)
        new_conf = f.confidence
        new_group_id = f.group_id
        new_group_role = f.group_role
        new_group_option = f.group_option
        new_parent_group_id = f.parent_group_id
        if d is not None:
            try:
                new_type = FieldType(d.field_type)
            except ValueError:
                pass
            if d.semantic_name:
                new_name = d.semantic_name
            new_conf = d.confidence
            try:
                role = GroupRole(d.group_role)
            except ValueError:
                role = GroupRole.INDEPENDENT
            # VLM grouping wins over heuristic only when the model claims a
            # non-independent role; otherwise we keep whatever
            # field_detector heuristically inferred (the legacy radio sweep).
            if role != GroupRole.INDEPENDENT and d.group_id:
                new_group_role = role
                new_group_id = d.group_id
                new_group_option = d.group_option or new_name
            # parent_group_id is independent of the role decision — a leaf
            # checkbox can have any role but still be nested inside a parent
            # choice. Trust the model when it provides a path.
            if d.parent_group_id:
                new_parent_group_id = d.parent_group_id
            # If model claims field_type=radio but didn't supply a group_id,
            # demote to checkbox so we don't write an orphan radio button.
            if new_type == FieldType.RADIO and (
                new_group_role != GroupRole.RADIO or not new_group_id
            ):
                new_type = FieldType.CHECKBOX
        kept.append(
            DetectedField(
                page=f.page,
                rect=f.rect,
                field_type=new_type,
                nearby_label=new_name,
                confidence=new_conf,
                detection_source=source_label,
                group_id=new_group_id,
                group_role=new_group_role,
                group_option=new_group_option,
                parent_group_id=new_parent_group_id,
            )
        )

    usage = response.usage
    logger.info(
        "  page %d: %d/%d kept (%s) | tokens in/out=%d/%d",
        page_num + 1,
        len(kept),
        len(indexed),
        mode,
        usage.prompt_tokens,
        usage.completion_tokens,
    )
    return kept, decisions


# ── Public API ────────────────────────────────────────────────────────────


def validate_form(
    detection: FormDetection,
    pdf_path: str,
    page_dims: Optional[list[tuple[float, float]]] = None,
) -> ValidationResult:
    if page_dims is None:
        doc = fitz.open(pdf_path)
        page_dims = [(doc[i].rect.width, doc[i].rect.height) for i in range(len(doc))]
        doc.close()

    api_key = os.environ.get(config.VLM_API_KEY_ENV, "not-needed")
    base_url = os.environ.get("VLM_API_BASE", config.VLM_API_BASE)
    client = OpenAI(base_url=base_url, api_key=api_key)

    all_kept: list[DetectedField] = []
    review_lines = [f"Form: {detection.form_id}"]

    for page_num in range(len(page_dims)):
        try:
            kept, _ = _decide_page(client, pdf_path, page_num, detection.fields)
        except Exception as e:
            logger.error("  page %d FAILED: %s", page_num + 1, e)
            review_lines.append(f"  page {page_num + 1}: ERROR {e}")
            continue
        all_kept.extend(kept)
        review_lines.append(
            f"  page {page_num + 1}: {len(kept)}/"
            f"{len([f for f in detection.fields if f.page == page_num])} kept"
        )

    review_lines.append(
        f"Total: {len(all_kept)}/{len(detection.fields)} kept "
        f"({100 * len(all_kept) / max(1, len(detection.fields)):.0f}%)"
    )

    return ValidationResult(
        form_id=detection.form_id,
        filename=detection.filename,
        category=detection.category,
        fields=all_kept,
        vlm_only_fields=[],
        conflicts=[],
        review_summary="\n".join(review_lines),
    )


def validate_all_forms(form_ids: Optional[list[str]] = None) -> list[str]:
    from modules.field_detector import load_detection
    from download import list_downloaded_forms

    config.VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    output_paths: list[str] = []

    forms = list_downloaded_forms()
    pdf_map = {f["form_id"]: f["path"] for f in forms}

    if form_ids is None:
        detection_files = sorted(config.DETECTION_DIR.glob("*.json"))
        form_ids = [f.stem for f in detection_files]

    for form_id in form_ids:
        out_path = config.VALIDATION_DIR / f"{form_id}.json"
        if out_path.exists():
            logger.debug("Skipping (exists): %s", form_id)
            output_paths.append(str(out_path))
            continue

        detection = load_detection(form_id)
        if detection is None:
            logger.warning("No detection for %s; skipping", form_id)
            continue

        pdf_path = pdf_map.get(form_id)
        if not pdf_path:
            logger.warning("No PDF for %s; skipping", form_id)
            continue

        logger.info("Validating with VLM: %s", form_id)
        result = validate_form(detection, pdf_path)
        out_path.write_text(result.model_dump_json(indent=2))
        output_paths.append(str(out_path))
        logger.info("  → %s", result.review_summary.split("\n")[-1])

    return output_paths


def load_validation(form_id: str) -> Optional[ValidationResult]:
    path = config.VALIDATION_DIR / f"{form_id}.json"
    if not path.exists():
        return None
    return ValidationResult.model_validate_json(path.read_text())
