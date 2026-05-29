#!/usr/bin/env python3
"""Substitute Qwen's answers back into the form markdown, producing a
human-readable "what the user would see" rendering for the Opus evaluator.

Each field shows: prompt, type (and options if select), the filled value,
the confidence, and the model's reasoning.
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
import yaml


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=pathlib.Path)
    ap.add_argument("filled_json", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    if not args.tree.exists() or not args.filled_json.exists():
        print("missing inputs", file=sys.stderr)
        return 2
    tree = yaml.safe_load(args.tree.read_text())
    filled = json.loads(args.filled_json.read_text())
    answers = filled.get("answers", {})
    pattern_id = filled.get("pattern_id", "?")
    form_id = tree.get("form_id", args.tree.stem)

    lines = [f"# Form {form_id} — pattern {pattern_id}", ""]
    for node in tree.get("nodes", []):
        if not isinstance(node, dict) or node.get("virtual"):
            continue
        fid = node["id"]
        a = answers.get(fid, {})
        value = a.get("value", "<not filled>")
        conf = a.get("confidence")
        reasoning = a.get("reasoning", "")
        lines.append(f"## {fid}")
        if prompt := node.get("prompt"):
            lines.append(f"- prompt: {prompt}")
        elif label := node.get("label"):
            lines.append(f"- prompt: {label}")
        lines.append(f"- type: `{node.get('type', 'text')}`")
        if when := node.get("when"):
            lines.append(f"- when: `{when}`")
        if options := node.get("options"):
            opt_summary = ", ".join(
                f"`{o.get('value')}`" for o in options
                if isinstance(o, dict) and not o.get("virtual")
            )
            lines.append(f"- options: {opt_summary}")
        conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "n/a"
        lines.append(f"- **value**: {value!r}")
        lines.append(f"- **confidence**: {conf_str}")
        lines.append(f"- reasoning: {reasoning}")
        lines.append("")
    out_text = "\n".join(lines)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(out_text)
    print(f"wrote {args.out} ({len(out_text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
