#!/usr/bin/env python3
"""Multimodal Opus review pass for tree YAML, via headless Claude Code.

Uses `claude -p --model opus` so we authenticate against the running
Claude Code session instead of needing a separate Anthropic API key.

The retry loop in build_form_tree.py converges on STRUCTURAL validity
(no unknown keys, no duplicate IDs, no missing widgets) but cannot fix
SEMANTIC hallucinations that are only visible when you look at the
rendered page:

  * Invented mutex: LLM emits a `court_type` select_one with options
    {probate, district} for a form that just has two parallel columns
    and no actual radio/checkbox glyph.
  * Numbered-list "Other" → select_many option with no checkbox.
  * Multi-widget label/continuation widgets mapped to the wrong field.
  * Multi-widget consolidation missed when same logical field has
    widgets on two lines.

The corrected YAML is written to a sibling `.opus_review.yaml` for the
user to diff against the original before accepting.
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import fitz
import yaml


PROMPT_INSTRUCTIONS = """\
You are reviewing a YAML tree that describes the fillable structure of a
Maine probate court PDF form. A smaller LLM (Qwen3.6-27B) generated the
tree from a text digest. You have the rendered page images so you can
catch errors that the smaller LLM made because it was working text-only.

Tree schema (canonical):

  form_id: <id>
  rect_overrides:            # OPTIONAL — only for fixing bad widget rects
    Wxxx: [x0, y0, x1, y1]
  nodes:
  - id: <snake_case>
    type: text | date | currency | select_one | select_many | enabler
    prompt: <human-readable label>
    when: <expr>             # OPTIONAL — gate by another field's value
    virtual: true            # OPTIONAL — structural only, no widget
    widget: Wxxx             # one of: widget OR widgets, never both
    widgets: [Wxxx, Wyyy]    # multi-widget consolidation
    options:                 # select_one / select_many only
      - value: <snake_case>
        widget: Wxxx
        label: <human-readable>

Known failure patterns to look for:

1. Invented mutex — a select_one created because the form has two
   parallel column headers like "PROBATE COURT | DISTRICT COURT" with
   NO actual checkbox/radio glyph between them. Drop the select_one,
   make the parallel widgets plain text fields, drop their `when:` gate.

2. Numbered-list "Other" as a select_many option without a checkbox —
   the last item N. of a check-all list. If there's no checkbox glyph
   for item N, drop it from options and emit its contents as plain text
   nodes.

3. Wrapped-label / continuation widget mapped to the wrong field — a
   subject like "evaluate: ___" wraps to a continuation line "___ by
   ___ (date) ..." — BOTH the first widget AND the wrap widget belong
   to the subject (use `widgets: [W072, W073]`), not different fields.

4. Multi-widget consolidation missed — when one logical field (Name,
   Address) has multiple underscored lines, all widgets should be in
   one node's `widgets:` list, not split into pseudo-fields like
   "address_line_1", "address_line_2".

5. Phantom nodes — text/date/currency nodes with empty widgets:[] or
   no widget at all. Drop them.

6. Duplicate node IDs that slipped through validation. Rename.

Input files:
  - digest path: $DIGEST_PATH
  - current tree YAML path: $TREE_PATH
  - page image paths: $IMAGE_PATHS

Read all of these, then output EXACTLY two YAML blocks, in this order:

```yaml diagnosis
issues:
  - <one sentence per problem, naming the node id or widget>
```

```yaml corrected
form_id: <unchanged>
rect_overrides:              # OPTIONAL — only if you see bad widget rects
  Wxxx: [x0, y0, x1, y1]
nodes:
  - ...                      # the FULL corrected tree, every node, in order
```

Do not output anything else outside those two fenced blocks.
"""


def render_pages(pdf_path: pathlib.Path, out_dir: pathlib.Path,
                 dpi: int = 150) -> list[pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    out: list[pathlib.Path] = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=False)
        path = out_dir / f"page_{i + 1}.png"
        pix.save(path)
        out.append(path)
    return out


def extract_block(reply: str, name: str) -> str | None:
    m = re.search(rf"```(?:yaml)?\s*{name}\s*\n(.*?)\n```", reply, re.DOTALL)
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("digest", type=pathlib.Path)
    ap.add_argument("tree", type=pathlib.Path)
    ap.add_argument("pdf", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path,
                    help="output corrected tree (default: <tree>.opus_review.yaml)")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--save-raw", type=pathlib.Path,
                    help="save raw Claude reply for inspection")
    args = ap.parse_args()
    for p in (args.digest, args.tree, args.pdf):
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr); return 2

    out_path = args.out or args.tree.with_suffix(".opus_review.yaml")
    with tempfile.TemporaryDirectory(prefix="opus_review_") as tmpdir:
        tmp = pathlib.Path(tmpdir)
        image_paths = render_pages(args.pdf, tmp, dpi=args.dpi)
        print(f"rendered {len(image_paths)} pages → {tmp}", file=sys.stderr)

        prompt = (
            PROMPT_INSTRUCTIONS
            .replace("$DIGEST_PATH", str(args.digest.resolve()))
            .replace("$TREE_PATH", str(args.tree.resolve()))
            .replace("$IMAGE_PATHS",
                     "\n".join(f"    - {p}" for p in image_paths))
        )

        cmd = [
            "claude", "-p",
            "--model", args.model,
            "--output-format", "text",
            "--no-session-persistence",
            "--allowedTools", "Read",
            "--add-dir", str(args.digest.parent.resolve()),
            "--add-dir", str(args.tree.parent.resolve()),
            "--add-dir", str(tmp),
        ]
        # Drop ANTHROPIC_API_KEY from the env so headless Claude Code
        # uses the OAuth session credentials from keychain instead of
        # treating our session OAuth token as an API key (it isn't).
        import os
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        print(f"running: {' '.join(cmd)}", file=sys.stderr)
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, timeout=600, env=env)
        if proc.returncode != 0:
            print(f"claude exited {proc.returncode}", file=sys.stderr)
            print(proc.stderr, file=sys.stderr)
            return proc.returncode
        reply = proc.stdout

    if args.save_raw:
        args.save_raw.parent.mkdir(parents=True, exist_ok=True)
        args.save_raw.write_text(reply)
        print(f"raw reply → {args.save_raw}", file=sys.stderr)

    diag = extract_block(reply, "diagnosis")
    corrected = extract_block(reply, "corrected")
    if diag:
        print("── diagnosis ──")
        print(diag)
    else:
        print("(no diagnosis block in reply)", file=sys.stderr)
    if corrected is None:
        print("ERROR: no corrected block in reply", file=sys.stderr)
        if not args.save_raw:
            print("Re-run with --save-raw to inspect", file=sys.stderr)
        return 3
    try:
        yaml.safe_load(corrected)
    except yaml.YAMLError as e:
        print(f"ERROR: corrected YAML does not parse: {e}", file=sys.stderr)
        return 3
    out_path.write_text(corrected)
    print(f"\nwrote corrected tree → {out_path}")
    print(f"diff -u {args.tree} {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
