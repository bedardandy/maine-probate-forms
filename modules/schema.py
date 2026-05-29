"""Pydantic models for all intermediate data in the pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Shared primitives ──────────────────────────────────────────────────────


class Rect(BaseModel):
    """Bounding box in PDF points (origin = bottom-left in PDF, but PyMuPDF
    uses top-left, so these are top-left coordinates)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)


class FieldType(str, Enum):
    TEXT = "text"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SIGNATURE = "signature"
    DATE = "date"
    CURRENCY = "currency"


class GroupRole(str, Enum):
    """Membership role of a checkbox/radio in a labeled question group.

    INDEPENDENT — standalone box, no semantic group (the default).
    RADIO       — member of a mutually-exclusive group; exactly one selectable.
    CHECKBOX_GROUP — member of a labeled multi-select group ("check all that
                  apply"); zero or more selectable, but logically tied.
    """

    INDEPENDENT = "independent"
    RADIO = "radio"
    CHECKBOX_GROUP = "checkbox_group"


# ── Stage 2: PDF Analysis ─────────────────────────────────────────────────


class TextSpan(BaseModel):
    text: str
    bbox: Rect
    font: str
    size: float
    color: int  # sRGB packed integer
    flags: int = 0  # bold/italic flags


class TextLine(BaseModel):
    bbox: Rect
    spans: list[TextSpan]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.spans)


class TextBlock(BaseModel):
    bbox: Rect
    lines: list[TextLine]


class DrawingElement(BaseModel):
    """A single drawing path element extracted from the page."""

    kind: str  # "line", "rect", "curve", "quad"
    rect: Rect  # bounding box of the element
    color: Optional[list[float]] = None  # stroke color
    fill: Optional[list[float]] = None  # fill color
    width: float = 1.0  # stroke width
    points: list[list[float]] = Field(default_factory=list)  # raw control points


class PageAnalysis(BaseModel):
    page_number: int  # 0-indexed
    width: float
    height: float
    rotation: int = 0
    text_blocks: list[TextBlock]
    drawings: list[DrawingElement]


class FormAnalysis(BaseModel):
    form_id: str  # e.g. "DE-101"
    filename: str
    category: str
    source_path: str
    num_pages: int
    pages: list[PageAnalysis]


# ── Stage 3: Field Detection ──────────────────────────────────────────────


class DetectedField(BaseModel):
    page: int  # 0-indexed
    rect: Rect
    field_type: FieldType
    nearby_label: str = ""
    section_header: str = ""  # closest heading above the field (e.g. "Co-Petitioner")
    confidence: float = 0.5
    detection_source: str = "heuristic"  # "heuristic", "vlm", "merged"
    group_id: Optional[str] = None  # shared key across siblings of the same group
    group_role: GroupRole = GroupRole.INDEPENDENT
    # Snake_case label of THIS option within the group (e.g. "limited_purpose"
    # for the "☐ Limited Purpose" box in a 3-way appointment-type group).
    # Used as the radio button's export ("on") value so the parent field's
    # value tells the form filler which option was selected.
    group_option: str = ""
    # Slash-separated path of enclosing groups for nested structure. Empty
    # for top-level groups. Example: "appointment_type/standard" for a
    # checkbox inside the "Standard Appointment" sub-list of a top-level
    # appointment-type radio. Metadata only — no PDF-level effect (PDF only
    # enforces mutual exclusion at the immediate radio group). Downstream
    # consumers (UI, form filler) use this to hide/show nested fields based
    # on the parent's selection.
    parent_group_id: str = ""


class FormDetection(BaseModel):
    form_id: str
    filename: str
    category: str
    fields: list[DetectedField]


# ── Stage 4: VLM Validation ──────────────────────────────────────────────


class VLMField(BaseModel):
    """A field detected by the VLM."""

    page: int
    rect: Rect
    field_type: FieldType
    label: str = ""
    confidence: float = 0.7


class ValidationResult(BaseModel):
    form_id: str
    filename: str
    category: str
    fields: list[DetectedField]  # merged final fields
    vlm_only_fields: list[VLMField] = []  # fields only VLM found
    conflicts: list[str] = []  # human review notes
    review_summary: str = ""


# ── Stage 5: Field Naming ────────────────────────────────────────────────


class NamedField(BaseModel):
    page: int
    rect: Rect
    field_type: FieldType
    field_name: str  # taxonomy-consistent name, e.g. "decedent_last_name"
    nearby_label: str = ""
    confidence: float = 0.5
    group_id: Optional[str] = None
    group_role: GroupRole = GroupRole.INDEPENDENT
    group_option: str = ""
    parent_group_id: str = ""


class FormNaming(BaseModel):
    form_id: str
    filename: str
    category: str
    fields: list[NamedField]


# ── Stage 6: AcroForm Writer (output metadata) ──────────────────────────


class WrittenField(BaseModel):
    field_name: str
    field_type: FieldType
    page: int
    rect: Rect
    group_id: Optional[str] = None
    group_role: GroupRole = GroupRole.INDEPENDENT
    group_option: str = ""
    parent_group_id: str = ""


class FormOutput(BaseModel):
    form_id: str
    filename: str
    source_path: str
    output_path: str
    field_count: int
    fields: list[WrittenField]
