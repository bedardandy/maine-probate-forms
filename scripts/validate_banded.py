"""Banded VLM validation: validate the form across overlapping vertical bands
spanning one or more pages, so cross-page groups get seen as continuous content.

The standard validator processes each physical page independently. That misses
groups whose options straddle a page break (PB-007's appointment_type radio:
Limited-Purpose at page 0 y=696, Standard at page 1 y=164, Expanded at page 1
y=327 — three options the VLM can't link across pages).

This validator computes a global y-axis spanning the whole document, slides
overlapping bands down it, and stitches the page slices each band touches into
one image. The VLM sees that image plus the candidates whose y falls inside
the band, with band-local pixel coords. Each candidate is annotated by every
band it appears in (overlap factor); a per-candidate merge then takes the
most-grouped, highest-confidence annotation.

Band geometry (defaults):
  height = 1200pt  (≈1.5 letter pages — guarantees the typical cross-page
                    section transition fits in one band even when sections
                    are 200-400pt tall)
  step   = 600pt   (50% overlap between adjacent bands)

Cost: ~5 VLM calls for a 4-page letter doc (vs 4 per-page). Worth it because
cross-page reasoning lands without an extra reconciliation pass.
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import pathlib
import sys
from collections import defaultdict

import fitz
from openai import OpenAI
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

config.VLM_API_BASE = os.environ.setdefault("VLM_API_BASE", "http://localhost:8088/v1")
config.VLM_MODEL = "Qwen3.6-27B-FP8"
os.environ.setdefault("VLM_MODE", "naming")

from modules import vlm_validator  # noqa: E402
from modules.schema import (  # noqa: E402
    DetectedField,
    FieldType,
    FormDetection,
    GroupRole,
    Rect,
    ValidationResult,
)
from scripts.validate_existing_widgets import widgets_to_detection  # noqa: E402


# Band geometry — tuned empirically. Letter pages are 792pt tall; 800pt
# bands give us about one page of content with enough overlap to catch
# section transitions. Larger bands (1200pt) trigger Qwen3.6-27B to truncate
# its JSON output mid-array on dense forms (PB-007 bands 0/1 lost ~50 cands
# each), so we keep band size moderate.
BAND_HEIGHT_PT = 800.0
BAND_STEP_PT = 400.0
# Render scale for band images. With 1200pt bands and a 2400px long-edge
# target, scale = 2 → ~144 DPI. Readable for the VLM and keeps token cost in
# line with per-page validation.
BAND_LONG_EDGE_PX = 2400


# Per-role priority for cross-band merge: prefer the most "grouped"
# annotation when the same candidate is annotated multiple times.
ROLE_PRIORITY = {"radio": 3, "checkbox_group": 2, "independent": 1}


def _stitch_band(doc: fitz.Document, y_start: float, y_end: float,
                 page_offsets: list[float], dpi: float) -> Image.Image | None:
    """Render the page slices a band touches and stack them vertically."""
    pieces: list[Image.Image] = []
    for pno in range(doc.page_count):
        po_top = page_offsets[pno]
        po_bot = page_offsets[pno + 1]
        if po_bot <= y_start:
            continue
        if po_top >= y_end:
            break
        page = doc[pno]
        clip_top_local = max(0.0, y_start - po_top)
        clip_bot_local = min(page.rect.height, y_end - po_top)
        if clip_bot_local <= clip_top_local:
            continue
        rect = fitz.Rect(0, clip_top_local, page.rect.width, clip_bot_local)
        # PyMuPDF wants int for dpi when set on the pixmap; pass an explicit
        # Matrix instead so any float scale works.
        scale_pt_to_px = dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(scale_pt_to_px, scale_pt_to_px),
            clip=rect, alpha=False,
        )
        pieces.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    if not pieces:
        return None
    if len(pieces) == 1:
        return pieces[0]
    max_w = max(p.width for p in pieces)
    total_h = sum(p.height for p in pieces)
    out = Image.new("RGB", (max_w, total_h), "white")
    y = 0
    for p in pieces:
        out.paste(p, (0, y))
        y += p.height
    return out


def _build_band_field_list(
    candidates: list[tuple[DetectedField, float]],
    band_y_start: float,
    page_offsets: list[float],
    band_img_w_px: int,
    band_img_h_px: int,
    band_height_pt: float,
) -> tuple[str, list[DetectedField]]:
    """Build the same kind of `[idx] heuristic_type=... bbox_px=...` text the
    standard validator sends, but with band-local pixel coordinates."""
    # band_y_pt → band_y_px scale
    scale_y = band_img_h_px / band_height_pt
    # x is page-local; scale matches the rendered band's pixel width / pt width
    # (assumes all pages in this band are same width, which they are for
    # letter forms).
    # Use page 0 width as reference; scale is uniform.
    lines = [
        f"Document slice spanning global y={band_y_start:.0f}..{band_y_start + band_height_pt:.0f}pt.",
        f"Image: {band_img_w_px}x{band_img_h_px} px (origin top-left).",
        "",
        "Candidates (return one decision per row, indexed by `index`):",
    ]
    indexed: list[DetectedField] = []
    for f, local_y in candidates:
        idx = len(indexed)
        scale_x = band_img_w_px / 612.0  # letter page width
        x0 = round(f.rect.x0 * scale_x)
        x1 = round(f.rect.x1 * scale_x)
        y0 = round(local_y * scale_y)
        y1 = round((local_y + (f.rect.y1 - f.rect.y0)) * scale_y)
        nearby = (f.nearby_label or "").strip()[:120] or "(none)"
        section = (f.section_header or "").strip()[:80] or "(none)"
        lines.append(
            f"[{idx}] heuristic_type={f.field_type.value} "
            f"bbox_px=[{x0},{y0},{x1},{y1}] "
            f"section={section!r} nearby_label={nearby!r}"
        )
        indexed.append(f)
    return "\n".join(lines), indexed


def validate_banded(detection: FormDetection,
                    pdf_path: pathlib.Path) -> ValidationResult:
    doc = fitz.open(pdf_path)
    page_heights = [doc[i].rect.height for i in range(doc.page_count)]
    # Cumulative offsets so global_y of a candidate = page_offsets[page] + rect.y0.
    page_offsets = [0.0]
    for h in page_heights:
        page_offsets.append(page_offsets[-1] + h)
    total_height = page_offsets[-1]

    bands: list[tuple[float, float]] = []
    y = 0.0
    while True:
        end = min(y + BAND_HEIGHT_PT, total_height)
        bands.append((y, end))
        if end >= total_height:
            break
        y += BAND_STEP_PT
    print(f"document height = {total_height:.0f}pt; "
          f"{len(bands)} band(s) of {BAND_HEIGHT_PT:.0f}pt step={BAND_STEP_PT:.0f}pt")

    client = OpenAI(base_url=config.VLM_API_BASE, api_key="not-needed")
    mode = os.environ.get("VLM_MODE", config.VLM_MODE).lower()
    system_prompt = (vlm_validator.NAMING_SYSTEM_PROMPT if mode == "naming"
                     else vlm_validator.GATING_SYSTEM_PROMPT)

    # Per-candidate annotations: key = (page, rounded_rect), value = [FieldDecision].
    annotations: dict[tuple, list] = defaultdict(list)

    for band_idx, (y_start, y_end) in enumerate(bands):
        # Pick candidates whose top-left y falls inside the band.
        cands: list[tuple[DetectedField, float]] = []
        for f in detection.fields:
            gy = page_offsets[f.page] + f.rect.y0
            if y_start <= gy < y_end:
                cands.append((f, gy - y_start))
        if not cands:
            print(f"  band {band_idx} y={y_start:.0f}..{y_end:.0f}: 0 candidates, skip")
            continue

        # Render the stitched band image at scale matching long-edge target.
        band_height_actual = y_end - y_start
        long_edge_pt = max(612.0, band_height_actual)
        scale = BAND_LONG_EDGE_PX / long_edge_pt
        dpi = scale * 72.0
        img = _stitch_band(doc, y_start, y_end, page_offsets, dpi)
        if img is None:
            continue
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
        b64 = base64.standard_b64encode(png).decode("utf-8")

        field_text, indexed = _build_band_field_list(
            cands, y_start, page_offsets, img.width, img.height, band_height_actual,
        )

        try:
            resp = client.chat.completions.create(
                model=config.VLM_MODEL,
                max_tokens=config.VLM_MAX_TOKENS,
                temperature=config.VLM_TEMPERATURE,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": field_text},
                    ]},
                ],
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
        except Exception as e:
            print(f"  band {band_idx} FAILED: {e}")
            continue
        raw = resp.choices[0].message.content or ""
        decisions = vlm_validator._parse_decisions(raw)
        decisions_by_idx = {d.index: d for d in decisions}
        n_grouped = 0
        for local_idx, f in enumerate(indexed):
            d = decisions_by_idx.get(local_idx)
            if d is None:
                continue
            key = (f.page, round(f.rect.x0, 1), round(f.rect.y0, 1),
                   round(f.rect.x1, 1), round(f.rect.y1, 1))
            annotations[key].append(d)
            if d.group_role and d.group_role != "independent":
                n_grouped += 1
        usage = resp.usage
        print(f"  band {band_idx} y={y_start:.0f}..{y_end:.0f}: "
              f"{len(indexed)} cands, {len(decisions)} decisions, "
              f"{n_grouped} grouped | tok in/out={usage.prompt_tokens}/{usage.completion_tokens}")

    # Merge: pick the most-grouped, highest-confidence annotation per candidate.
    print(f"\nmerging annotations: {len(annotations)} candidates with at least one decision")
    final_fields: list[DetectedField] = []
    for f in detection.fields:
        key = (f.page, round(f.rect.x0, 1), round(f.rect.y0, 1),
               round(f.rect.x1, 1), round(f.rect.y1, 1))
        ds = annotations.get(key, [])
        if not ds:
            final_fields.append(f.model_copy(update={
                "detection_source": "banded-vlm",
            }))
            continue
        best = max(ds, key=lambda d: (
            ROLE_PRIORITY.get(d.group_role, 0),
            d.confidence,
        ))
        try:
            ftype = FieldType(best.field_type)
        except ValueError:
            ftype = f.field_type
        try:
            grole = GroupRole(best.group_role) if best.group_role else GroupRole.INDEPENDENT
        except ValueError:
            grole = GroupRole.INDEPENDENT
        # Demote orphan radios to checkbox.
        if ftype == FieldType.RADIO and (grole != GroupRole.RADIO or not best.group_id):
            ftype = FieldType.CHECKBOX
        final_fields.append(f.model_copy(update={
            "field_type": ftype,
            "nearby_label": best.semantic_name or f.nearby_label,
            "group_id": best.group_id or None,
            "group_role": grole,
            "group_option": best.group_option,
            "parent_group_id": best.parent_group_id,
            "confidence": best.confidence,
            "detection_source": "banded-vlm",
        }))

    return ValidationResult(
        form_id=detection.form_id,
        filename=detection.filename,
        category=detection.category,
        fields=final_fields,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path,
                    help="Fused/fillable PDF whose widgets become candidates.")
    ap.add_argument("--form-id", default="PB-007")
    ap.add_argument("--category", default="guardian_minor")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    if not args.pdf.exists():
        print(f"missing: {args.pdf}", file=sys.stderr)
        return 2

    detection = widgets_to_detection(
        args.pdf, args.form_id, args.category, args.pdf.name,
    )
    print(f"synthesized {len(detection.fields)} candidates from {args.pdf.name}")
    result = validate_banded(detection, args.pdf)
    args.out.write_text(result.model_dump_json(indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
