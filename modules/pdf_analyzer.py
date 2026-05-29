"""Stage 2: Extract text and drawing structure from PDF pages using PyMuPDF."""

import logging
from pathlib import Path

import fitz  # PyMuPDF

import config
from modules.schema import (
    DrawingElement,
    FormAnalysis,
    PageAnalysis,
    Rect,
    TextBlock,
    TextLine,
    TextSpan,
)

logger = logging.getLogger(__name__)


def _extract_text_blocks(page: fitz.Page) -> list[TextBlock]:
    """Extract all text blocks with span-level detail from a page."""
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # 0 = text block
            continue

        lines = []
        for line in block.get("lines", []):
            spans = []
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                bbox = span["bbox"]
                spans.append(
                    TextSpan(
                        text=text,
                        bbox=Rect(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
                        font=span.get("font", ""),
                        size=span.get("size", 0.0),
                        color=span.get("color", 0),
                        flags=span.get("flags", 0),
                    )
                )
            if spans:
                lbbox = line["bbox"]
                lines.append(
                    TextLine(
                        bbox=Rect(x0=lbbox[0], y0=lbbox[1], x1=lbbox[2], y1=lbbox[3]),
                        spans=spans,
                    )
                )

        if lines:
            bbbox = block["bbox"]
            blocks.append(
                TextBlock(
                    bbox=Rect(x0=bbbox[0], y0=bbbox[1], x1=bbbox[2], y1=bbbox[3]),
                    lines=lines,
                )
            )

    return blocks


def _extract_drawings(page: fitz.Page) -> list[DrawingElement]:
    """Extract all drawing paths (lines, rects, curves) from a page."""
    elements = []

    for path in page.get_drawings():
        color = path.get("color")
        fill = path.get("fill")
        width = path.get("width", 1.0)
        rect = path.get("rect")

        if rect is None:
            continue

        # Classify each item in the path
        for item in path.get("items", []):
            kind = item[0]  # 'l' (line), 're' (rect), 'c' (curve), 'qu' (quad)
            points = []

            if kind == "l":  # line: (start, end)
                p1, p2 = item[1], item[2]
                el_rect = Rect(
                    x0=min(p1.x, p2.x),
                    y0=min(p1.y, p2.y),
                    x1=max(p1.x, p2.x),
                    y1=max(p1.y, p2.y),
                )
                points = [[p1.x, p1.y], [p2.x, p2.y]]
                el_kind = "line"

            elif kind == "re":  # rectangle
                r = item[1]
                el_rect = Rect(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1)
                el_kind = "rect"

            elif kind == "c":  # cubic bezier curve
                pts = item[1:]
                xs = [p.x for p in pts]
                ys = [p.y for p in pts]
                el_rect = Rect(
                    x0=min(xs),
                    y0=min(ys),
                    x1=max(xs),
                    y1=max(ys),
                )
                points = [[p.x, p.y] for p in pts]
                el_kind = "curve"

            elif kind == "qu":  # quad
                q = item[1]
                el_rect = Rect(
                    x0=min(q.ul.x, q.ll.x, q.ur.x, q.lr.x),
                    y0=min(q.ul.y, q.ll.y, q.ur.y, q.lr.y),
                    x1=max(q.ul.x, q.ll.x, q.ur.x, q.lr.x),
                    y1=max(q.ul.y, q.ll.y, q.ur.y, q.lr.y),
                )
                el_kind = "quad"
            else:
                continue

            elements.append(
                DrawingElement(
                    kind=el_kind,
                    rect=el_rect,
                    color=list(color) if color else None,
                    fill=list(fill) if fill else None,
                    width=width or 1.0,
                    points=points,
                )
            )

    return elements


def analyze_form(pdf_path: str | Path, form_id: str, category: str) -> FormAnalysis:
    """Analyze a single PDF form, extracting text and drawing structure."""
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))

    pages = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text_blocks = _extract_text_blocks(page)
        drawings = _extract_drawings(page)

        pages.append(
            PageAnalysis(
                page_number=page_num,
                width=page.rect.width,
                height=page.rect.height,
                rotation=page.rotation,
                text_blocks=text_blocks,
                drawings=drawings,
            )
        )

    doc.close()

    return FormAnalysis(
        form_id=form_id,
        filename=pdf_path.name,
        category=category,
        source_path=str(pdf_path),
        num_pages=len(pages),
        pages=pages,
    )


def analyze_all_forms(forms: list[dict] | None = None) -> list[str]:
    """Analyze all downloaded forms and save JSON output.

    Args:
        forms: List of form dicts from download.list_downloaded_forms().
               If None, discovers forms automatically.

    Returns:
        List of output JSON file paths.
    """
    if forms is None:
        from download import list_downloaded_forms

        forms = list_downloaded_forms()

    config.ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    output_paths = []

    for form_info in forms:
        form_id = form_info["form_id"]
        out_path = config.ANALYSIS_DIR / f"{form_id}.json"

        if out_path.exists():
            logger.debug("Skipping analysis (exists): %s", form_id)
            output_paths.append(str(out_path))
            continue

        logger.info("Analyzing: %s (%s)", form_id, form_info["filename"])
        try:
            analysis = analyze_form(form_info["path"], form_id, form_info["category"])
            out_path.write_text(analysis.model_dump_json(indent=2))
            output_paths.append(str(out_path))
            logger.info(
                "  → %d pages, %d text blocks, %d drawings",
                analysis.num_pages,
                sum(len(p.text_blocks) for p in analysis.pages),
                sum(len(p.drawings) for p in analysis.pages),
            )
        except Exception as e:
            logger.error("Failed to analyze %s: %s", form_id, e)

    return output_paths


def load_analysis(form_id: str) -> FormAnalysis | None:
    """Load a previously saved analysis from JSON."""
    path = config.ANALYSIS_DIR / f"{form_id}.json"
    if not path.exists():
        return None
    return FormAnalysis.model_validate_json(path.read_text())
