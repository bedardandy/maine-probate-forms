#!/usr/bin/env python3
"""Convert a tree YAML to a compact markdown representation suitable for
LLM consumption. Minimal — just what the model needs to fill it out:
prompt, type, options for select fields, and gating (when:) conditions.

Output schema per field:
    ## {id}
    - **type**: text | date | currency | select_one | select_many | enabler
    - **prompt**: {free-form description}
    - **options**: (for select_*) list of {value, label}
    - **when**: (if conditional) gating expression

Sections separated by ---. No widget IDs (the LLM doesn't need them).
"""
from __future__ import annotations
import argparse
import pathlib
import sys
import yaml


def field_md(node: dict) -> str:
    lines = [f"## {node['id']}"]
    lines.append(f"- type: `{node.get('type', 'text')}`")
    if prompt := node.get("prompt"):
        lines.append(f"- prompt: {prompt}")
    if label := node.get("label"):
        if label != prompt:
            lines.append(f"- label: {label}")
    if when := node.get("when"):
        lines.append(f"- when: `{when}`")
    if options := node.get("options"):
        lines.append("- options:")
        for opt in options:
            if not isinstance(opt, dict):
                continue
            if opt.get("virtual"):
                continue
            lines.append(f"  - `{opt.get('value')}` — {opt.get('label', '')}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()
    if not args.tree.exists():
        print(f"missing: {args.tree}", file=sys.stderr)
        return 2
    tree = yaml.safe_load(args.tree.read_text())
    form_id = tree.get("form_id", args.tree.stem)
    sections = [f"# Form {form_id}", ""]
    for node in tree.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("virtual"):
            continue
        sections.append(field_md(node))
        sections.append("")
    out_text = "\n".join(sections)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_text)
        print(f"wrote {args.out} ({len(out_text)} bytes, "
              f"{out_text.count('## ')} fields)")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
