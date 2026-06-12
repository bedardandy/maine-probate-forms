"""Diff Layer-1 v1 vs v2 field naming for the 5-form panel.

v1 names: intermediate/naming/{fid}.json (from full sweep)
v2 names: intermediate_layer1/naming/{fid}.json (from current panel test)

Pairs fields by (page, rounded x0, rounded y0). Shows each field's
section_header (from detection JSON) alongside both names so we can see
*why* names changed — was it section preservation, prompt rewrite, or
qwen stochasticity?

Outputs reports/v1_vs_v2_naming.md.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ["GS-001", "AD-001", "DE-201(I)", "PP-205", "N-115"]
OUT = ROOT / "reports" / "v1_vs_v2_naming.md"


def _key(f: dict) -> tuple:
    r = f["rect"]
    return (f["page"], round(r["x0"], 1), round(r["y0"], 1))


def _index(fields: list[dict]) -> dict[tuple, dict]:
    return {_key(f): f for f in fields}


def diff_form(fid: str) -> list[str]:
    v1_nam = json.load(open(ROOT / "intermediate" / "naming" / f"{fid}.json"))
    v2_nam = json.load(open(ROOT / "intermediate_layer1" / "naming" / f"{fid}.json"))
    v1_det = json.load(open(ROOT / "intermediate" / "detection" / f"{fid}.json"))
    v2_det = json.load(open(ROOT / "intermediate_layer1" / "detection" / f"{fid}.json"))

    v1_n = _index(v1_nam["fields"])
    v2_n = _index(v2_nam["fields"])
    v1_d = _index(v1_det["fields"])
    v2_d = _index(v2_det["fields"])

    common = sorted(set(v1_n) & set(v2_n))
    v1_only = sorted(set(v1_n) - set(v2_n))
    v2_only = sorted(set(v2_n) - set(v1_n))

    lines = [
        f"## {fid}",
        "",
        f"v1 fields={len(v1_n)} v2 fields={len(v2_n)} "
        f"common={len(common)} v1_only={len(v1_only)} v2_only={len(v2_only)}",
        "",
    ]

    # Tabulate name differences
    changed = []
    for k in common:
        n1 = v1_n[k].get("field_name", "")
        n2 = v2_n[k].get("field_name", "")
        if n1 != n2:
            s1 = v1_d.get(k, {}).get("section_header", "")
            s2 = v2_d.get(k, {}).get("section_header", "")
            nl = v1_n[k].get("nearby_label", "") or v2_n[k].get("nearby_label", "")
            changed.append((k, n1, n2, s1, s2, nl))

    lines.append(f"**Changed names: {len(changed)} of {len(common)} common fields**")
    lines.append("")
    if changed:
        lines.append("| page | y | nearby_label | v1 section → name | v2 section → name |")
        lines.append("|---:|---:|---|---|---|")
        for (page, x0, y0), n1, n2, s1, s2, nl in changed:
            nl_short = (nl or "")[:40].replace("|", "\\|")
            s1_short = (s1 or "")[:30].replace("|", "\\|")
            s2_short = (s2 or "")[:30].replace("|", "\\|")
            lines.append(
                f"| {page} | {y0:.0f} | {nl_short} | "
                f"`{s1_short}` → `{n1}` | `{s2_short}` → `{n2}` |"
            )
    lines.append("")

    if v1_only:
        lines.append(f"**Fields only in v1: {len(v1_only)}**")
        for k in v1_only[:8]:
            f = v1_n[k]
            lines.append(f"- p{f['page']} y={k[2]:.0f} `{f.get('field_name','')}`")
        if len(v1_only) > 8:
            lines.append(f"- ... ({len(v1_only) - 8} more)")
        lines.append("")
    if v2_only:
        lines.append(f"**Fields only in v2: {len(v2_only)}**")
        for k in v2_only[:8]:
            f = v2_n[k]
            lines.append(f"- p{f['page']} y={k[2]:.0f} `{f.get('field_name','')}`")
        if len(v2_only) > 8:
            lines.append(f"- ... ({len(v2_only) - 8} more)")
        lines.append("")
    return lines


def main() -> None:
    out = ["# Layer 1 v1 vs v2 Naming Diff (5-form panel)", ""]
    for fid in PANEL:
        if not (ROOT / "intermediate_layer1" / "naming" / f"{fid}.json").exists():
            out.append(f"## {fid} — v2 naming not yet produced, skipping")
            out.append("")
            continue
        out.extend(diff_form(fid))
    OUT.write_text("\n".join(out) + "\n")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
