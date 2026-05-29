#!/usr/bin/env python3
"""
Maine Probate Forms — Field Realignment Pipeline

Problem: Previous acroform_writer had coordinate overflow bugs (INT_MAX coords)
and heuristic misalignment. This pipeline:

1. Re-downloads clean original PDFs from maineprobate.net (no AcroForm fields)
2. Renders each page to PNG
3. Sends to Qwen3-VL-8B with prompt to identify field locations visually
4. Writes corrected AcroForm fields at proper coordinates
5. Outputs to output_realigned/

Usage:
    python3 realign_fields.py [--forms AD-003 ...] [--workers 2] [--skip-download] [--dry-run]

Requirements:
    pip install pymupdf pillow httpx requests
"""

import argparse
import base64
import io
import json
import re
import time
import shutil
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import fitz
from PIL import Image

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_DIR   = Path('/path/to/projects/probate-forms')
FORMS_DIR     = PROJECT_DIR / 'forms'
ORIGINALS_DIR = PROJECT_DIR / 'originals_clean'   # fresh downloads, no fields
OUTPUT_DIR    = PROJECT_DIR / 'output_realigned'
RENDERS_DIR   = Path('/tmp/probate_renders')

QWEN_URL   = 'http://localhost:8092/v1/chat/completions'
QWEN_MODEL = 'Qwen/Qwen3-VL-8B-Instruct'
RENDER_DPI = 150
TIMEOUT_S  = 90

# ── Form URL catalog (from download.py) ───────────────────────────────────────
# Import from existing download.py
sys.path.insert(0, str(PROJECT_DIR))
try:
    from download import FORM_URLS
except ImportError:
    FORM_URLS = {}

# ── Qwen prompt ───────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are analyzing a Maine Probate Court form PDF page.
Your job is to identify every fillable field on this page and return their precise locations.

Look for:
- Blank lines (underscores "____" or horizontal rules) → Text fields
- Empty boxes □ or circles ○ → CheckBox or Radio fields  
- Signature lines → Text fields named "signature_*"
- Date lines → Text fields named "date_*"
- Checkboxes that are part of a "choose one" group → Radio fields

For each field, return coordinates in PDF points (the page coordinate system where 
(0,0) is top-left). Page dimensions will be provided.

IMPORTANT coordinate rules:
- x0, y0 = top-left corner of the field
- x1, y1 = bottom-right corner  
- Text fields should be just tall enough for one line (~14-18pt height)
- Checkboxes should be square, ~10-12pt per side
- Fields must stay within page bounds

Return ONLY valid JSON — no markdown, no explanation:
{
  "page_width": 612,
  "page_height": 792,
  "fields": [
    {
      "name": "snake_case_descriptive_name",
      "type": "Text|CheckBox|Radio",
      "x0": 92.5,
      "y0": 145.2,
      "x1": 350.0,
      "y1": 159.0,
      "radio_group": null,
      "confidence": 0.95,
      "nearby_label": "exact label text next to this field"
    }
  ]
}

For Radio buttons that belong together (same question, choose one), set radio_group to the same string (e.g., "disposition_choice").
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def render_page_clean(pdf_path: Path, page_num: int) -> tuple[bytes, float, float]:
    """Render a clean PDF page (no overlays). Returns (jpeg_bytes, width_pts, height_pts)."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    pw, ph = page.rect.width, page.rect.height
    mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img_bytes = pix.tobytes('png')
    doc.close()

    # Convert to JPEG for smaller payload
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=88)
    return buf.getvalue(), pw, ph


def call_qwen_for_fields(jpeg_bytes: bytes, page_width: float, page_height: float,
                          form_name: str, page_num: int) -> dict:
    """Ask Qwen to identify field locations on this page."""
    b64 = base64.b64encode(jpeg_bytes).decode()

    user_text = (
        f"This is page {page_num + 1} of Maine Probate form {form_name}.\n"
        f"Page size: {page_width:.1f} x {page_height:.1f} PDF points.\n"
        f"Identify ALL fillable fields and return their precise coordinates in PDF points."
    )

    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": user_text}
            ]}
        ],
        "temperature": 0.1,
        "max_tokens": 6000,
    }

    try:
        r = httpx.post(QWEN_URL, json=payload, timeout=TIMEOUT_S)
        r.raise_for_status()
        text = r.json()['choices'][0]['message']['content'].strip()

        # Strip markdown fences
        if text.startswith('```'):
            lines = text.split('\n')
            text = '\n'.join(lines[1:])
            text = re.sub(r'```\s*$', '', text).strip()

        parsed = json.loads(text)

        # Validate/clamp coordinates to page bounds
        valid_fields = []
        for f in parsed.get('fields', []):
            x0, y0, x1, y1 = f.get('x0', 0), f.get('y0', 0), f.get('x1', 0), f.get('y1', 0)

            # Sanity checks
            if abs(x0) > 10000 or abs(y0) > 10000:
                continue  # skip INT_MAX overflow fields
            if x1 <= x0 or y1 <= y0:
                continue  # skip zero/negative size
            if x0 < 0 or y0 < 0:
                continue  # skip off-page

            # Clamp to page
            x0 = max(0, min(x0, page_width - 5))
            y0 = max(0, min(y0, page_height - 5))
            x1 = max(x0 + 5, min(x1, page_width))
            y1 = max(y0 + 3, min(y1, page_height))

            f['x0'], f['y0'], f['x1'], f['y1'] = x0, y0, x1, y1
            valid_fields.append(f)

        return {"ok": True, "fields": valid_fields, "page_width": page_width, "page_height": page_height}

    except json.JSONDecodeError as e:
        # Try regex extraction
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
                return {"ok": True, "fields": parsed.get('fields', []), "raw": text[:200]}
            except:
                pass
        return {"ok": False, "error": f"JSON parse: {e}", "raw": text[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def write_acroform_fields(source_pdf: Path, all_page_fields: dict, output_path: Path) -> dict:
    """
    Write AcroForm widgets into a PDF at corrected positions.
    all_page_fields: {page_num: [field_dict, ...]}
    """
    doc = fitz.open(str(source_pdf))

    # First, strip any existing broken widgets
    for page in doc:
        # Remove all existing widgets
        for widget in page.widgets():
            page.delete_widget(widget)

    # Write new fields
    field_count = 0
    radio_groups_written = {}  # group_name → list of written widgets

    for page_num, fields in sorted(all_page_fields.items()):
        if page_num >= len(doc):
            continue
        page = doc[page_num]

        for f in fields:
            name = f.get('name', f'field_{field_count}')
            ftype = f.get('type', 'Text')
            x0, y0, x1, y1 = f['x0'], f['y0'], f['x1'], f['y1']
            rect = fitz.Rect(x0, y0, x1, y1)
            group = f.get('radio_group')

            widget = fitz.Widget()
            widget.rect = rect
            widget.field_name = name

            if ftype == 'Text':
                widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                widget.text_fontsize = 10
                widget.text_color = (0, 0, 0)
                widget.fill_color = (1, 1, 1)  # white bg
                widget.border_color = (0.5, 0.5, 0.5)
                widget.border_width = 0.5

            elif ftype == 'CheckBox':
                widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
                widget.field_value = 'Off'
                widget.fill_color = (1, 1, 1)
                widget.border_color = (0, 0, 0)
                widget.border_width = 0.75

            elif ftype == 'Radio':
                widget.field_type = fitz.PDF_WIDGET_TYPE_RADIOBUTTON
                # Radio buttons in same group share the field_name
                widget.field_name = group if group else name
                widget.fill_color = (1, 1, 1)
                widget.border_color = (0, 0, 0)
                widget.border_width = 0.75

            try:
                page.add_widget(widget)
                field_count += 1
            except Exception as e:
                pass  # skip bad widgets silently

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()

    return {"fields_written": field_count}


def get_clean_pdf(form_path: Path) -> Path:
    """
    Get a clean version of the PDF (no AcroForm fields).
    Strip existing widgets by rendering to a flat PDF if needed.
    """
    clean_path = ORIGINALS_DIR / form_path.parent.name / form_path.name
    if clean_path.exists():
        return clean_path

    clean_path.parent.mkdir(parents=True, exist_ok=True)

    # Check if source already has fields - if so, flatten them out
    doc = fitz.open(str(form_path))
    has_widgets = any(list(p.widgets()) for p in doc)
    doc.close()

    if has_widgets:
        # Strip widgets by copying doc and deleting all widgets
        doc = fitz.open(str(form_path))
        for page in doc:
            widgets_to_delete = list(page.widgets())
            for w in widgets_to_delete:
                page.delete_widget(w)
        doc.save(str(clean_path), garbage=4, deflate=True)
        doc.close()
    else:
        shutil.copy(form_path, clean_path)

    return clean_path


# ── Per-form processing ────────────────────────────────────────────────────────

def process_form(form_path: Path, dry_run: bool = False) -> dict:
    form_name = form_path.stem
    category = form_path.parent.name
    out_path = OUTPUT_DIR / category / (form_name + '_realigned.pdf')

    if out_path.exists():
        return {"form": form_name, "skipped": "already done"}

    # Get clean PDF (strip existing broken widgets)
    try:
        clean_pdf = get_clean_pdf(form_path)
    except Exception as e:
        return {"form": form_name, "error": f"clean_pdf: {e}"}

    # Get page count
    doc = fitz.open(str(clean_pdf))
    num_pages = doc.page_count
    doc.close()

    all_page_fields = {}
    total_fields = 0
    errors = []

    RENDERS_DIR.mkdir(parents=True, exist_ok=True)

    for page_num in range(num_pages):
        cache_key = RENDERS_DIR / f"{form_name}_p{page_num}.jpg"
        if not cache_key.exists():
            try:
                jpeg, pw, ph = render_page_clean(clean_pdf, page_num)
                cache_key.write_bytes(jpeg)
            except Exception as e:
                errors.append(f"render p{page_num}: {e}")
                continue
        else:
            # Get dimensions from doc
            doc = fitz.open(str(clean_pdf))
            p = doc[page_num]
            pw, ph = p.rect.width, p.rect.height
            doc.close()
            jpeg = cache_key.read_bytes()

        result = call_qwen_for_fields(jpeg, pw, ph, form_name, page_num)
        if not result['ok']:
            errors.append(f"p{page_num}: {result.get('error','?')}")
            continue

        fields = result.get('fields', [])
        # Add page number to each field
        for f in fields:
            f['page'] = page_num
        all_page_fields[page_num] = fields
        total_fields += len(fields)

    if not all_page_fields:
        return {"form": form_name, "error": "no fields detected", "errors": errors}

    if not dry_run:
        try:
            write_result = write_acroform_fields(clean_pdf, all_page_fields, out_path)
            fields_written = write_result['fields_written']
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"form": form_name, "error": f"write: {e}", "errors": errors}
    else:
        fields_written = total_fields

    status = "✅" if not errors else "⚠️ "
    print(f"  {status} {form_name}: {num_pages}p, {fields_written} fields written", flush=True)

    return {
        "form": form_name,
        "category": category,
        "pages": num_pages,
        "fields_detected": total_fields,
        "fields_written": fields_written,
        "errors": errors,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--forms', nargs='*', help='Specific form stems to process')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--skip-download', action='store_true', help='Use existing forms/ PDFs as source')
    parser.add_argument('--category', help='Process only this category (e.g. estates, gc_adults)')
    args = parser.parse_args()

    # Verify Qwen is up
    try:
        r = httpx.get(f'http://localhost:8092/v1/models', timeout=5)
        model = r.json()['data'][0]['id']
        print(f"✅ Qwen ready: {model}")
    except Exception as e:
        print(f"❌ Qwen not available at {QWEN_URL}: {e}")
        print("   Start it on your Qwen host, e.g.: nohup python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-VL-8B-Instruct --port 8092 --host 0.0.0.0 --max-model-len 12288 --gpu-memory-utilization 0.50 > /tmp/qwen3_vl.log 2>&1 &")
        sys.exit(1)

    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)

    # Discover forms
    if args.forms:
        all_pdfs = []
        for f in args.forms:
            matches = list(FORMS_DIR.rglob(f"*{f}*.pdf"))
            all_pdfs.extend(matches)
    elif args.category:
        all_pdfs = sorted((FORMS_DIR / args.category).glob('*.pdf'))
    else:
        all_pdfs = sorted(FORMS_DIR.rglob('*.pdf'))

    print(f"{'DRY RUN — ' if args.dry_run else ''}Processing {len(all_pdfs)} forms | workers={args.workers}")
    print()

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_form, p, args.dry_run): p for p in all_pdfs}
        done = 0
        for fut in as_completed(futures):
            form_path = futures[fut]
            done += 1
            try:
                result = fut.result(timeout=300)
                results.append(result)
            except Exception as e:
                import traceback
                print(f"  ❌ {form_path.stem}: {e}", flush=True)
                traceback.print_exc()
                results.append({"form": form_path.stem, "error": str(e)})
            if done % 10 == 0 or done == len(futures):
                print(f"── {done}/{len(futures)} ──", flush=True)

    # Summary
    total_fields = sum(r.get('fields_written', 0) for r in results)
    errors = [r for r in results if r.get('errors') or r.get('error')]
    print(f"\n=== DONE ===")
    print(f"Forms: {len(results)}")
    print(f"Fields written: {total_fields:,}")
    print(f"Forms with errors: {len(errors)}")
    print(f"Output: {OUTPUT_DIR}")

    summary_path = PROJECT_DIR / 'realign_results.json'
    summary_path.write_text(json.dumps({
        "forms": len(results),
        "total_fields": total_fields,
        "error_count": len(errors),
        "dry_run": args.dry_run,
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "results": results,
    }, indent=2))
    print(f"Summary → {summary_path}")


if __name__ == '__main__':
    main()
