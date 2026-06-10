"""MCP server exposing PyMuPDF PDF form field operations."""

from pathlib import Path

import fitz  # PyMuPDF
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pdf-forms")

FIELD_TYPE_NAMES = {
    fitz.PDF_WIDGET_TYPE_TEXT: "text",
    fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
    fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    fitz.PDF_WIDGET_TYPE_COMBOBOX: "combobox",
    fitz.PDF_WIDGET_TYPE_LISTBOX: "listbox",
    fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature",
}


@mcp.tool()
def list_fields(pdf_path: str) -> list[dict]:
    """List all form fields in a PDF with their names, types, positions, and values."""
    doc = fitz.open(pdf_path)
    fields = []
    for page_num, page in enumerate(doc):
        for widget in page.widgets():
            fields.append(
                {
                    "name": widget.field_name,
                    "type": FIELD_TYPE_NAMES.get(
                        widget.field_type, f"unknown({widget.field_type})"
                    ),
                    "page": page_num,
                    "rect": [
                        widget.rect.x0,
                        widget.rect.y0,
                        widget.rect.x1,
                        widget.rect.y1,
                    ],
                    "value": widget.field_value or "",
                }
            )
    doc.close()
    return fields


@mcp.tool()
def get_page_dimensions(pdf_path: str) -> list[dict]:
    """Get the width and height of each page in a PDF."""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        r = page.rect
        pages.append({"page": i, "width": r.width, "height": r.height})
    doc.close()
    return pages


@mcp.tool()
def update_field_rects(
    pdf_path: str,
    updates: list[dict],
    output_path: str | None = None,
) -> dict:
    """Batch-update field rectangles. Each update: {field_name, rect: [x0, y0, x1, y1]}."""
    if output_path is None:
        output_path = str(Path(pdf_path).with_stem(Path(pdf_path).stem + "_aligned"))

    doc = fitz.open(pdf_path)
    update_map = {u["field_name"]: fitz.Rect(u["rect"]) for u in updates}
    changed = []

    for page in doc:
        for widget in page.widgets():
            if widget.field_name in update_map:
                old = list(widget.rect)
                widget.rect = update_map[widget.field_name]
                widget.update()
                changed.append(
                    {
                        "field_name": widget.field_name,
                        "old_rect": old,
                        "new_rect": list(widget.rect),
                    }
                )

    doc.save(output_path)
    doc.close()
    matched = {c["field_name"] for c in changed}
    unmatched = sorted(n for n in update_map if n not in matched)
    return {"output_path": output_path, "updated": changed,
            "unmatched": unmatched}


@mcp.tool()
def align_fields(
    pdf_path: str,
    reference_field: str,
    target_fields: list[str],
    axis: str = "y",
    output_path: str | None = None,
) -> dict:
    """Align target fields to a reference field's position along an axis ('x', 'y', or 'both')."""
    if output_path is None:
        output_path = str(Path(pdf_path).with_stem(Path(pdf_path).stem + "_aligned"))

    doc = fitz.open(pdf_path)

    # Find reference rect
    ref_rect = None
    for page in doc:
        for widget in page.widgets():
            if widget.field_name == reference_field:
                ref_rect = widget.rect
                break
        if ref_rect:
            break

    if ref_rect is None:
        doc.close()
        return {"error": f"Reference field '{reference_field}' not found"}

    target_set = set(target_fields)
    changed = []

    for page in doc:
        for widget in page.widgets():
            if widget.field_name in target_set:
                old = list(widget.rect)
                r = widget.rect
                if axis in ("y", "both"):
                    height = r.y1 - r.y0
                    r.y0 = ref_rect.y0
                    r.y1 = ref_rect.y0 + height
                if axis in ("x", "both"):
                    width = r.x1 - r.x0
                    r.x0 = ref_rect.x0
                    r.x1 = ref_rect.x0 + width
                widget.rect = r
                widget.update()
                changed.append(
                    {
                        "field_name": widget.field_name,
                        "old_rect": old,
                        "new_rect": list(widget.rect),
                    }
                )

    doc.save(output_path)
    doc.close()
    return {
        "output_path": output_path,
        "reference": reference_field,
        "reference_rect": list(ref_rect),
        "axis": axis,
        "updated": changed,
    }


if __name__ == "__main__":
    mcp.run()
