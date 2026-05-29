"""Render a tree YAML as a Mermaid flowchart for human review.

The Mermaid diagram lets a reviewer audit the form's logical structure
in seconds rather than grepping through YAML. Visual cues:

  Node shape / color:
    select_one   — rounded box, blue        (radio question)
    select_many  — rounded box, green       (multi-checkbox)
    enabler      — diamond, yellow          (parent gate)
    text/date/currency — square box, grey   (free-form fill)

  Edges:
    Solid arrow                — sequential next-question
    Dashed arrow with label    — `when:` gating (label = the condition)

Output: a `.mmd` file that can be pasted into mermaid.live or rendered
via `mmdc` (mermaid-cli) to PNG/SVG.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml


TYPE_STYLE = {
    "select_one":  ("(", ")",  "fill:#dbeafe,stroke:#2563eb,color:#1e3a8a"),  # blue
    "select_many": ("(", ")",  "fill:#dcfce7,stroke:#16a34a,color:#14532d"),  # green
    "enabler":     ("{", "}",  "fill:#fef3c7,stroke:#d97706,color:#78350f"),  # yellow diamond
    "text":        ("[", "]",  "fill:#f3f4f6,stroke:#6b7280,color:#1f2937"),  # grey
    "date":        ("[", "]",  "fill:#f3f4f6,stroke:#6b7280,color:#1f2937"),
    "currency":    ("[", "]",  "fill:#f3f4f6,stroke:#6b7280,color:#1f2937"),
}


def _esc(s: str) -> str:
    """Escape for Mermaid node label — quotes are tricky inside parens."""
    if s is None:
        return ""
    s = s.replace('"', "'").replace("\n", " ").strip()
    return s[:80]


def _node_label(node: dict) -> str:
    nid = node.get("id", "?")
    ntype = node.get("type", "?")
    prompt = _esc(node.get("prompt", ""))
    options = node.get("options") or []
    parts = [f"<b>{nid}</b>", f"<i>{ntype}</i>", prompt]
    if ntype in ("select_one", "select_many") and options:
        opts_text = " · ".join(
            _esc(o.get("label") or o.get("value", "?"))
            for o in options if isinstance(o, dict)
        )
        parts.append(opts_text)
    if node.get("widgets"):
        parts.append(f"⊟ {len(node['widgets'])}w")
    return "<br/>".join(parts)


def render_mermaid(tree: dict) -> str:
    nodes = tree.get("nodes") or []
    out: list[str] = ["flowchart TD"]
    style_lines: list[str] = []
    edge_lines: list[str] = []

    prev_id: str | None = None
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        nid = node.get("id", f"n{i}")
        ntype = node.get("type", "text")
        open_, close, style = TYPE_STYLE.get(ntype, TYPE_STYLE["text"])
        label = _node_label(node)
        out.append(f'  {nid}{open_}"{label}"{close}')
        style_lines.append(f"  style {nid} {style}")

        when = node.get("when")
        if when:
            # Dashed gating edge from the referenced node.
            # Find a node id mentioned in the `when` clause and link from it.
            ref = _first_node_ref(when, {n.get("id", "") for n in nodes if isinstance(n, dict)})
            if ref:
                cond = _esc(str(when))
                edge_lines.append(f'  {ref} -. "{cond}" .-> {nid}')
            elif prev_id:
                edge_lines.append(f"  {prev_id} --> {nid}")
        elif prev_id:
            edge_lines.append(f"  {prev_id} --> {nid}")
        prev_id = nid

    out += edge_lines
    out += style_lines
    return "\n".join(out) + "\n"


def _first_node_ref(when_clause: str, ids: set[str]) -> str | None:
    """Find the first node id referenced in a `when` expression."""
    for tok in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", when_clause):
        if tok in ids:
            return tok
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree_yaml", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()
    if not args.tree_yaml.exists():
        print(f"missing: {args.tree_yaml}", file=sys.stderr)
        return 2

    tree = yaml.safe_load(args.tree_yaml.read_text())
    if not isinstance(tree, dict):
        print("YAML did not parse to a dict", file=sys.stderr)
        return 3

    out = args.out or args.tree_yaml.with_suffix(".mmd")
    text = render_mermaid(tree)
    out.write_text(text)
    nodes = tree.get("nodes") or []
    print(f"wrote {out} ({len(text)} bytes, {len(nodes)} nodes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
