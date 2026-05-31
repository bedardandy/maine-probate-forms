#!/usr/bin/env python3
"""Generate the human-readable statute-consideration reference from the sidecars.

Reads each repo/forms/<FORM>/statutes.json (authored by scripts/author_statutes.py)
plus the form's metadata.json/schema.json (for titles and field labels) and emits:

    docs/statute-reference/<FORM>.md     one page per form
    docs/statute-reference/README.md     index grouped by category + transition rule

This is generated output — edit the sidecars (or scripts/author_statutes.py) and
regenerate, don't hand-edit the .md files. Mirrors the spirit of
scripts/form_to_markdown.py (compact, link-rich, LLM/human readable).

Usage:
    python3 scripts/build_statute_reference.py
"""
from __future__ import annotations

import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
FORMS_DIR = REPO / "repo" / "forms"
OUT = REPO / "docs" / "statute-reference"
IDX = OUT / "_index"

CATEGORY_LABELS = {
    "estates": "Decedent's Estates — Probate & Administration",
    "affidavits": "Affidavits & Alternative Procedures",
    "guardianship": "Guardianship & Conservatorship",
    "minor_guardianship": "Guardianship & Conservatorship — Minors",
    "adoption": "Adoption",
    "name_change": "Name Change",
    "notices": "Notices & Service",
    "appeals": "Appeals & Court Procedure",
    "misc": "Miscellaneous",
}
# Fallback grouping by form-ID prefix when metadata.category is absent/unknown.
PREFIX_CATEGORY = {
    "DE": "estates", "AF": "affidavits", "PP": "guardianship", "GS": "minor_guardianship",
    "PB": "minor_guardianship", "AD": "adoption", "CN": "name_change", "NC": "name_change",
    "N-": "notices", "APP": "appeals", "MISC": "misc",
}

DISCLAIMER_BANNER = (
    "> ⚠️ **Experimental — AI/LLM-generated, not legal advice.** The statute and "
    "case-law references on this page are generated and annotated by an AI model and "
    "have **not** been reviewed by an attorney. They list issues an LLM or person "
    "filling the form may want to *consider* — not what to do or conclude — and are no "
    "substitute for a licensed Maine attorney. Statute section text is quoted from "
    "legislature.maine.gov; the *selection of statutes/cases, the relevance notes, and "
    "any case-law holdings are the model's experimental annotations and may be wrong*. "
    "Which code applies can turn on the date of death — see the transition note. "
    "**Verify everything against the current statute and the actual opinions.**"
)


def load_meta(form_id: str) -> dict:
    p = FORMS_DIR / form_id / "metadata.json"
    return json.loads(p.read_text()) if p.exists() else {}


def field_labels(form_id: str) -> dict[str, str]:
    p = FORMS_DIR / form_id / "schema.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    out = {}
    for f in data.get("fields") or []:
        fid = f.get("field_id") or f.get("id")
        if fid:
            out[fid] = f.get("label") or fid
    return out


def category_of(form_id: str, meta: dict) -> str:
    cat = (meta.get("category") or "").lower()
    if cat in CATEGORY_LABELS:
        # Refine guardianship into minor vs adult by prefix where useful.
        if cat == "guardianship" and form_id.split("-")[0] in ("GS", "PB"):
            return "minor_guardianship"
        return cat
    for pref, c in PREFIX_CATEGORY.items():
        if form_id.startswith(pref):
            return c
    return "misc"


def cite_link(item: dict) -> str:
    cite = item["cite"]
    url = item.get("url")
    return f"[{cite}]({url})" if url else cite


def render_form(form_id: str, sidecar: dict, meta: dict, labels: dict[str, str]) -> str:
    title = meta.get("title") or form_id
    lines = [f"# {form_id} — {title}", ""]
    if sidecar.get("summary"):
        lines += [f"*{sidecar['summary']}*", ""]
    lines += [DISCLAIMER_BANNER, "", f"**Applies:** {sidecar.get('applies','')}", ""]

    lines += ["## Governing statutes", ""]
    if sidecar.get("governing"):
        for g in sidecar["governing"]:
            lines.append(f"- **{cite_link(g)}** — {g['title']}  \n  {g['why']}")
    else:
        lines.append("- *(none recorded)*")
    lines.append("")

    if sidecar.get("per_question"):
        lines += ["## Per-question considerations", "",
                  "Material questions on this form and the statutes to weigh when answering them.", ""]
        for pq in sidecar["per_question"]:
            fid = pq["field_id"]
            label = labels.get(fid, fid)
            lines.append(f"### `{fid}` — {label}")
            for c in pq["considerations"]:
                if c.get("cite"):
                    lines.append(f"- {cite_link(c)} ({c['title']}): {c['note']}")
                else:
                    lines.append(f"- {c['note']}")
            lines.append("")

    lines += ["## 18-A transition", "", sidecar.get("transition_18a", ""), ""]

    if sidecar.get("cross_refs"):
        lines += ["## Cross-references (outside Title 18-C)", ""]
        for x in sidecar["cross_refs"]:
            lines.append(f"- **{cite_link(x)}** — {x['title']}")
        lines.append("")

    if sidecar.get("caselaw"):
        lines += ["## Maine Law Court cases to consider", "",
                  "⚠️ **AI/LLM-generated, experimental — not attorney-reviewed.** "
                  "Decisions an AI model tied to this form through the statute(s) they "
                  "bear on; the selection and the holding summaries are the model's "
                  "annotations and may be wrong. Read the opinion and confirm it is "
                  "still good law before relying on it.", ""]
        for c in sidecar["caselaw"]:
            era = f" · decided under {c['decided_under']}" if c.get("decided_under") else ""
            src = " · *holding not independently verified from the opinion*" if c.get("holding_source") == "secondary" else ""
            via = ", ".join(c["via"])
            lines.append(f"- **[{c['name']}, {c['cite']}]({c['url']})** ({c['year']}{era}) — "
                         f"{c['topic']}. *Tied via {via}.*{src}  \n  {c['holding']}")
        lines.append("")

    lines += ["---", "",
              f"_Generated from `repo/forms/{form_id}/statutes.json`. "
              "Statute titles/links verified against `_index/18c-sections.json`. "
              "Edit the sidecar and run `make statutes`, not this file._", ""]
    return "\n".join(lines)


def render_readme(forms: list[tuple[str, dict, dict]]) -> str:
    diffs = json.loads((IDX / "18a-key-diffs.json").read_text())
    tr = diffs["transition"]
    lines = [
        "# Maine Probate Forms — Statutes for Consideration",
        "",
        DISCLAIMER_BANNER,
        "",
        "A per-form layer mapping each court form to the Maine Uniform Probate Code "
        "(**Title 18-C**) sections worth considering when answering its questions, with a "
        "transition note for the former **Title 18-A** and pointers to related resources.",
        "",
        "## How this is built",
        "",
        "- **`_index/18c-sections.json`** — the trusted index of every 18-C section "
        "(verbatim from legislature.maine.gov). Every citation below resolves to it.",
        "- **`_index/18a-key-diffs.json`** — the 18-A transition rule + material differences.",
        "- **`_index/cross-refs.json`** — non-Title-18-C citations the forms touch (estate tax, etc.).",
        "- **`_index/caselaw.json`** + **[`caselaw.md`](caselaw.md)** — Maine "
        "Law Court (Supreme Judicial Court) estate/probate decisions, tied to forms "
        "through the statutes they construe. ⚠️ The case selection and holding "
        "summaries are AI/LLM-generated, experimental, and not attorney-reviewed — "
        "read the opinion and confirm it is still good law.",
        "- **`../digital-assets-access.md`** — accessing a deceased person's online accounts "
        "(grounded in 18-C Article 10, the Maine RUFADAA).",
        "- Per-form pages are generated from `repo/forms/<FORM>/statutes.json`.",
        "",
        "## The transition rule (read this first)",
        "",
        f"Title 18-C took effect **{tr['effective_date']}** ({tr['governing_section']}). {tr['rule']}",
        "",
        f"> **Practical test:** {tr['practical_test']}",
        "",
        "See [`_index/18a-key-diffs.json`](_index/18a-key-diffs.json) for the material "
        "18-A→18-C differences (elective share, intestate shares, guardianship rewrite, "
        "small-estate threshold, TOD deeds, digital assets).",
        "",
        "## Forms by category",
        "",
    ]
    by_cat: dict[str, list] = {}
    for form_id, sidecar, meta in forms:
        by_cat.setdefault(category_of(form_id, meta), []).append((form_id, sidecar, meta))
    for cat in list(CATEGORY_LABELS) + [c for c in by_cat if c not in CATEGORY_LABELS]:
        if cat not in by_cat:
            continue
        lines.append(f"### {CATEGORY_LABELS.get(cat, cat)}")
        lines.append("")
        for form_id, sidecar, meta in sorted(by_cat[cat]):
            title = meta.get("title") or form_id
            n_gov = len(sidecar.get("governing", []))
            n_pq = len(sidecar.get("per_question", []))
            lines.append(f"- [{form_id}]({form_id}.md) — {title} "
                         f"({n_gov} governing, {n_pq} per-question)")
        lines.append("")
    return "\n".join(lines)


def render_caselaw_index(forms: list[tuple[str, dict, dict]]) -> str:
    cl = json.loads((IDX / "caselaw.json").read_text())
    # form-by-case reverse map
    forms_by_case: dict[str, list[str]] = {}
    for form_id, sidecar, _ in forms:
        for c in sidecar.get("caselaw", []):
            forms_by_case.setdefault(c["cite"], []).append(form_id)
    lines = [
        "# Maine Law Court — Estate & Probate Cases to Consider",
        "",
        DISCLAIMER_BANNER,
        "",
        cl.get("note", ""),
        "",
        "Each case is tied to forms through the statute(s) it bears on (see the "
        "per-form pages). 18-A-era decisions are mapped forward to the current 18-C "
        "section carrying the same rule.",
        "",
    ]
    for case_id, c in cl["cases"].items():
        forms_list = sorted(set(forms_by_case.get(c["cite"], [])))
        src = "read from the opinion" if c.get("holding_source") == "primary" else "from published summaries — verify against the opinion"
        lines.append(f"## [{c['name']}, {c['cite']}]({c['url']}) ({c['year']})")
        lines.append("")
        lines.append(f"- **Topic:** {c['topic']}")
        lines.append(f"- **Decided under:** {c.get('decided_under','?')}")
        lines.append(f"- **Bears on:** {', '.join(c.get('statutes', []))}")
        if forms_list:
            lines.append(f"- **Tied to forms:** {', '.join(f'[{f}]({f}.md)' for f in forms_list)}")
        lines.append(f"- **Holding ({src}):** {c['holding']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    forms = []
    for d in sorted(FORMS_DIR.iterdir()):
        sc = d / "statutes.json"
        if not sc.is_dir() and sc.exists():
            sidecar = json.loads(sc.read_text())
            meta = load_meta(d.name)
            forms.append((d.name, sidecar, meta))
            (OUT / f"{d.name}.md").write_text(
                render_form(d.name, sidecar, meta, field_labels(d.name)), encoding="utf-8")
    (OUT / "README.md").write_text(render_readme(forms), encoding="utf-8")
    (OUT / "caselaw.md").write_text(render_caselaw_index(forms), encoding="utf-8")
    print(f"wrote {len(forms)} per-form pages + README.md + caselaw.md to {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
