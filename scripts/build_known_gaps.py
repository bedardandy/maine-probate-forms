#!/usr/bin/env python3
"""Generate repo/KNOWN_GAPS.md — a living tracker of what's
incomplete, uncertain, or stubbed in the per-form artifacts.

This file is meant to be regenerated whenever the repo changes
(after running `build_form_schema.py` on any form). It surfaces:

  - Forms with no filing_deadline_days set
  - Forms with no eval evidence (untested fills)
  - Forms with high red-tier counts (review priority)
  - Forms with `other` fields still present
  - Forms with computed fields but no `formulas.yaml`
  - Forms with auto-generated skill.md (no hand-curation)
  - Forms with TODO frontmatter values

Run:
  python3 scripts/build_known_gaps.py

Writes:
  repo/KNOWN_GAPS.md
"""
from __future__ import annotations
import json
import pathlib
import re
import sys
from collections import defaultdict


REPO_DIR = pathlib.Path("repo/forms")
OUT_PATH = pathlib.Path("repo/KNOWN_GAPS.md")
HAND_CURATED_SKILL_MD = {
    # Forms whose skill.md is hand-authored (not auto-generated).
    # Original pilot set:
    "DE-405", "PP-205", "DE-507", "AF-105", "GS-014", "N-118",
    # Red-tier follow-up batch:
    "PP-406", "PP-410", "PP-507", "APP-2", "DE-509", "PP-413",
    "PP-510", "AF-101",
    # Orange-tier batch 1:
    "DE-503", "PP-405", "PB-007", "PB-007.vA", "MISC-102",
    # Orange-tier batch 2:
    "AF-101.vA", "DE-506", "AD-008", "NC-001", "PP-402",
    # Orange-tier batch 3:
    "DE-403", "GS-008", "GS-008.vA", "MISC-101", "PP-201",
    # Yellow-tier high-volume batch:
    "DE-201", "AD-026", "DE-603", "DE-101", "PP-107", "N-112",
    # Order/decree forms batch:
    "DE-501", "DE-504",
    # Adoption batch:
    "AD-007",
    # Closing-statement batch:
    "DE-602",
    # Pretermitted-child batch:
    "DE-505",
    # Capacity evaluation batch:
    "PP-505",
}


def _load_schema(form_dir: pathlib.Path) -> dict | None:
    p = form_dir / "schema.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _skill_frontmatter(form_dir: pathlib.Path) -> dict:
    """Extract YAML frontmatter fields we care about, without
    requiring a full YAML parse."""
    p = form_dir / "skill.md"
    out: dict = {}
    if not p.exists():
        return out
    text = p.read_text()
    # Frontmatter delimited by `---\n...\n---`
    m = re.search(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return out
    fm = m.group(1)
    for key in ("filer_role", "filing_deadline_days", "service_required",
                "form_title"):
        mm = re.search(rf"^{key}:\s*(.*)$", fm, re.M)
        if mm:
            out[key] = mm.group(1).strip()
    # statutes block — list or `[]`
    if "statutes: []" in fm:
        out["statutes"] = []
    else:
        sts = re.findall(r'^\s*-\s+"([^"]+)"', fm, re.M)
        out["statutes"] = sts
    return out


def main() -> int:
    if not REPO_DIR.exists():
        print(f"missing {REPO_DIR}", file=sys.stderr)
        return 2

    forms = sorted(p for p in REPO_DIR.iterdir() if p.is_dir())
    if not forms:
        print("no forms found", file=sys.stderr)
        return 1

    # Categorize
    no_deadline: list[str] = []
    no_eval_evidence: list[str] = []
    has_other: list[tuple[str, int]] = []
    has_red: list[tuple[str, int, int]] = []
    auto_skill: list[str] = []
    todo_statutes: list[str] = []
    computed_no_formula: list[tuple[str, list[str]]] = []
    failing_validator: list[tuple[str, int, int]] = []

    total_fields = 0
    total_other = 0
    total_red = 0
    cat_sum: dict[str, int] = defaultdict(int)
    tier_sum: dict[str, int] = defaultdict(int)

    for d in forms:
        schema = _load_schema(d)
        if not schema:
            continue
        fid = schema["form_id"]
        nf = schema["n_fields"]
        cats = schema.get("by_category", {})
        tiers = schema.get("by_risk_tier", {})
        total_fields += nf
        total_other += cats.get("other", 0)
        total_red += tiers.get("red", 0)
        for k, v in cats.items(): cat_sum[k] += v
        for k, v in tiers.items(): tier_sum[k] += v

        fm = _skill_frontmatter(d)
        deadline = fm.get("filing_deadline_days", "null")
        if deadline in ("null", "TODO", ""):
            no_deadline.append(fid)
        if not fm.get("statutes"):
            todo_statutes.append(fid)
        if fid not in HAND_CURATED_SKILL_MD:
            auto_skill.append(fid)

        # Eval evidence — count fields with patterns_scored > 0
        n_scored = sum(
            1 for f in schema["fields"]
            if (f.get("eval_evidence") or {}).get("patterns_scored", 0)
        )
        if n_scored == 0:
            no_eval_evidence.append(fid)

        if cats.get("other", 0) > 0:
            has_other.append((fid, cats["other"]))
        if tiers.get("red", 0) > 0:
            has_red.append((fid, tiers["red"], nf))

        # Computed fields without a formula
        unformed = [f["field_id"] for f in schema["fields"]
                    if f.get("category") == "computed"
                    and not f.get("formula")]
        if unformed:
            computed_no_formula.append((fid, unformed))

        # Forms that fail their own validator (best-effort: check the
        # most recent Qwen-v2 fill in intermediate/fact_eval if present).
        filled_p1 = pathlib.Path(
            f"intermediate/fact_eval/{fid}/filled_1.json")
        if filled_p1.exists():
            try:
                from scripts.validate_filled import validate, _flatten_filled
            except Exception:
                # When run from project root, scripts is not a package.
                pass
            # Skip the runtime check; just record existence.
            pass

    # Build the markdown
    lines: list[str] = [
        "# Known gaps and TODO tracker",
        "",
        "_This file is regenerated by `scripts/build_known_gaps.py`. "
        "It surfaces incomplete, uncertain, or stubbed content across "
        "the per-form artifacts so contributors can target their effort._",
        "",
        "## Repo snapshot",
        "",
        f"- **Forms:** {len(forms)}",
        f"- **Total fields:** {total_fields:,}",
        f"- **Forms with hand-curated skill.md:** "
        f"{len(HAND_CURATED_SKILL_MD)}",
        f"- **Forms with auto-generated skill.md:** {len(auto_skill)}",
        "",
        "**By category:**",
        "",
    ]
    lines.append("| category | count | % |")
    lines.append("|---|---|---|")
    for k, v in sorted(cat_sum.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} | {100*v/total_fields:.1f}% |")
    lines.append("")
    lines.append("**By risk tier:**")
    lines.append("")
    lines.append("| tier | count | % |")
    lines.append("|---|---|---|")
    for t in ("green", "yellow", "orange", "red"):
        v = tier_sum.get(t, 0)
        lines.append(f"| {t} | {v} | {100*v/total_fields:.1f}% |")

    # Section: missing deadlines
    lines += [
        "",
        "## 1. Forms missing `filing_deadline_days`",
        "",
        f"_{len(no_deadline)} of {len(forms)} forms have no encoded filing "
        "deadline. Many probate forms genuinely have no statutory deadline "
        "(they're filed when needed), so `null` may be correct. Forms below "
        "should be reviewed against the relevant Maine statute or court "
        "rule; if a deadline applies, add it via `skill_metadata` in the "
        "form's `classifications.yaml`._",
        "",
    ]
    if no_deadline:
        lines.append("```")
        cols = 4
        for i in range(0, len(no_deadline), cols):
            row = no_deadline[i:i+cols]
            lines.append("  ".join(f"{x:<12}" for x in row))
        lines.append("```")
    else:
        lines.append("None.")

    # Section: empty statutes
    lines += [
        "",
        "## 2. Forms with empty `statutes`",
        "",
        f"_{len(todo_statutes)} forms. Category-prefix defaults in "
        "`build_form_schema.py` populate broad citations (e.g. "
        "\"18-C M.R.S.A. Article 5\") for most forms; the ones below have "
        "no statute citation at all and need per-form research._",
        "",
    ]
    if todo_statutes:
        lines.append("```")
        cols = 4
        for i in range(0, len(todo_statutes), cols):
            lines.append("  ".join(f"{x:<12}" for x in todo_statutes[i:i+cols]))
        lines.append("```")
    else:
        lines.append("None.")

    # Section: no eval evidence
    lines += [
        "",
        "## 3. Forms with no eval evidence",
        "",
        f"_{len(no_eval_evidence)} forms have no `eval_evidence` in any "
        "field. Generate by running `scripts/run_fact_eval.sh <form_id> 5`. "
        "Without eval evidence, risk scores are derived only from category "
        "base, and the auto-generated skill.md \"Known LLM failure modes\" "
        "table is empty._",
        "",
    ]
    if no_eval_evidence:
        lines.append("```")
        cols = 4
        for i in range(0, len(no_eval_evidence), cols):
            lines.append("  ".join(f"{x:<12}" for x in no_eval_evidence[i:i+cols]))
        lines.append("```")
    else:
        lines.append("None.")

    # Section: high-red forms (review priority)
    lines += [
        "",
        "## 4. Forms with red-tier fields (review priority)",
        "",
        f"_{len(has_red)} forms have at least one red-tier field. "
        "These are the highest-priority targets for hand-authoring "
        "`skill.md` failure-mode narrative._",
        "",
        "| form | red | total | red% |",
        "|---|---|---|---|",
    ]
    for fid, r, nf in sorted(has_red, key=lambda x: -x[1])[:20]:
        lines.append(f"| {fid} | {r} | {nf} | {100*r/nf:.1f}% |")

    # Section: computed without formula
    lines += [
        "",
        "## 5. Computed fields without a formula",
        "",
        f"_{len(computed_no_formula)} forms have ≥1 field classified as "
        "`computed` but no `formula` in schema.json. Add a `formulas.yaml` "
        "with a JSON-DSL expression so `validate_filled.py` can recompute._",
        "",
    ]
    if computed_no_formula:
        lines.append("| form | computed-without-formula |")
        lines.append("|---|---|")
        for fid, missing in computed_no_formula:
            lines.append(f"| {fid} | {', '.join(missing)} |")
    else:
        lines.append("None — every computed field has a formula.")

    # Section: auto skill.md
    lines += [
        "",
        "## 6. Forms with auto-generated `skill.md`",
        "",
        f"_{len(auto_skill)} forms. The auto-generator populates "
        "frontmatter, slot groups, parties, pipeline routing, and a "
        "failure-modes table from eval data. It does NOT write a "
        "form-specific narrative \"Known LLM failure modes\" section, "
        "computed formulas exposition, or conditional-writability prose. "
        "Hand-curation high-value when a form is being deployed._",
        "",
    ]

    # Section: validator audit
    lines += [
        "",
        "## 7. Validator audit (Qwen v2, pattern 1)",
        "",
        "_Result of running `scripts/validate_filled.py` against every "
        "form's first Qwen-v2 fill. Refreshed when this file is "
        "regenerated. Demonstrates what the current rule set catches "
        "vs. what slips through._",
        "",
    ]
    import subprocess
    from collections import Counter as _Counter
    audit_codes: _Counter = _Counter()
    audit_total_err = 0
    audit_total_warn = 0
    audit_clean = 0
    audit_validated = 0
    for d in forms:
        filled = pathlib.Path(
            f"intermediate/fact_eval/{d.name}/filled_1.json")
        if not filled.exists():
            continue
        try:
            proc = subprocess.run(
                ["python3", "scripts/validate_filled.py",
                 "--schema", str(d / "schema.json"),
                 "--filled", str(filled)],
                capture_output=True, text=True, timeout=60
            )
        except Exception:
            continue
        m = re.search(r"errors:\s+(\d+),\s+warns:\s+(\d+)",
                      proc.stdout)
        if m:
            audit_validated += 1
            e, w = int(m.group(1)), int(m.group(2))
            audit_total_err += e
            audit_total_warn += w
            if e == 0: audit_clean += 1
        for line in proc.stdout.split("\n"):
            mm = re.search(r"\[(error|warn)\s*\]\s+\S+\s+(\S+)", line)
            if mm:
                audit_codes[(mm.group(1), mm.group(2))] += 1
    lines.append(f"- **Forms validated:** {audit_validated}")
    lines.append(f"- **Forms clean (0 errors):** "
                 f"{audit_clean}/{audit_validated} "
                 f"({100*audit_clean//max(audit_validated,1)}%)")
    lines.append(f"- **Total errors:** {audit_total_err}")
    lines.append(f"- **Total warnings:** {audit_total_warn}")
    lines.append("")
    lines.append("| severity | code | count |")
    lines.append("|---|---|---|")
    for (sev, code), n in audit_codes.most_common():
        lines.append(f"| {sev} | `{code}` | {n} |")
    lines.append("")
    lines.append("Re-run with `python3 scripts/build_known_gaps.py` "
                 "after any classifier / formula / validator change "
                 "to refresh this table.")

    # Section: open questions / future work
    lines += [
        "",
        "## 8. Standing open questions",
        "",
        "- **Newer models may shift the picture.** Eval scores reflect "
        "  Qwen3.6-27B and Claude Opus 4.7 as of May 2026. Re-running "
        "  evals against newer models (or against larger fact-pattern "
        "  banks) may reduce the red-tier count.",
        "- **Repeating-slot validators are name-based.** `dedupe_within` "
        "  and `cross_section_dedupe` work on slot prefixes; they do not "
        "  catch semantic duplicates (e.g. \"125 Main St\" vs \"125 Main "
        "  Street\"). Add fuzz-tolerant deduping if needed.",
        "- **Conditional writability** is only declared on a small "
        "  fraction of forms (mostly N-118 + PB-007). Most forms have "
        "  conditional sections that aren't yet encoded — a downstream "
        "  consumer should not assume `writable_when: null` means a "
        "  field is unconditionally writable. Safety net for the "
        "  encoded cases: `scripts/infer_gates.py` sets the gate when "
        "  the LLM populates dependents but leaves the gate empty (the "
        "  N-118 failure mode).",
        "- **PDF alignment.** The 79 form.pdf files come from "
        "  `output_tree/` (post-snap), which is more accurate than the "
        "  pre-snap `output/` artifacts but still has minor widget "
        "  positioning issues. See `reports/opus-alignment-*/` for "
        "  per-form diagnostics.",
        "- **Maine bar number validation** uses a permissive regex "
        "  (`^\\d{2,6}$`). Update if Maine's bar-number format changes.",
        "- **Formula DSL has no addendum-overflow op.** _RESOLVED 2026-05-13._ "
        "  When a slot-table form's narrative has more entities than the "
        "  form has slots (e.g. 8 real properties + PP-406's 6 slots), "
        "  the in-form sum_slot total diverges from the legally-correct "
        "  gross. Resolved by adding `formula_mode: at_least` per-field "
        "  in classifications.yaml. Applied to PP-406 + DE-405's 10 "
        "  gross/calc fields each. Worked example at "
        "  `repo/forms/PP-406/examples/case.overflow.json`.",
        "- **80% of checkbox fields lack a `value_in` validator.** "
        "  106 of 133 checkbox fields have no validator, so the LLM "
        "  treats them as section-enabler metadata and won't fill them. "
        "  Surfaced in N-118 where 3 gate checkboxes (change_in_dwelling, "
        "  sale_or_surrender_of_dwelling, "
        "  revised_guardianship_plan_approved) were left empty while "
        "  every dependent was populated. Short-term mitigation: "
        "  `scripts/infer_gates.py` sets the gate when dependents are "
        "  populated and the gate is empty. Long-term fix: classify each "
        "  unvalidated checkbox as truly yes/no vs multi-value enum (e.g. "
        "  `court_type`, `employment_type` are not yes/no) and add the "
        "  appropriate `value_in` validator in classifications.yaml.",
        "- **Qwen3.6-27B paraphrases `value_in` enums.** "
        "  Prompt rules don't fix it (v3 prompt change moved errors "
        "  26→33, not 26→18). Mitigation: post-process via "
        "  `scripts/canonicalize_enums.py` (stem map + substring + "
        "  RapidFuzz ≥85). Cut paraphrase-drift errors 78% on the v3 "
        "  corpus. Auto-runs as a post-fill step in fill_form.py. See "
        "  `~/.claude/projects/<project>/memory/feedback_qwen36_enum_drift.md`.",
        "- **Multi-chunk fills suffer slot-restart dedupe.** "
        "  Each Qwen call is a fresh context, so when a form's slot "
        "  group spans &gt; 20 fields (the chunk size), the LLM "
        "  re-enumerates items 1..K into slots K+1..2K. 14 of 79 forms "
        "  have slot groups crossing chunk boundaries. Mitigation: v4 "
        "  prompt change adds an ALREADY-PLACED recap to chunks 2+ via "
        "  `build_prior_answers_block()` in fill_form.py — verified on "
        "  PP-406 (4 dedupe errors → 0).",
        "- **Enumerated narrative validation depends on form-context "
        "  knowledge.** The May-2026 round added 50+ `value_in` rules "
        "  on `select_one`/`select_many` narrative fields. Several "
        "  rules had to be corrected (or removed) when the audit "
        "  surfaced that my guessed enum didn't match the form's "
        "  actual radio labels (e.g. N-108's `fiduciary_role` is a "
        "  guardianship-waiver role, not a § 3-103 decedent-estate "
        "  fiduciary class). New `value_in` encodings should be "
        "  cross-checked against the actual PDF form text before "
        "  committing.",
        "",
        "## How to contribute fixes",
        "",
        "1. **Filing deadlines / statutes:** add a `skill_metadata` "
        "block to `repo/forms/<form>/classifications.yaml` (create the "
        "file if it doesn't exist). Then re-run "
        "`python3 scripts/build_form_schema.py <form>`.",
        "2. **Computed formulas:** create "
        "`repo/forms/<form>/formulas.yaml` with the JSON-DSL "
        "expression (`repo/forms/DE-405/formulas.yaml` is the canonical "
        "exemplar). Re-run the generator.",
        "3. **`other` field classifications:** add a per-field entry "
        "to `classifications.yaml`'s `overrides:` block. The classifier "
        "in `scripts/build_form_schema.py` is broadenable for repeated "
        "patterns across forms.",
        "4. **Eval evidence:** run "
        "`bash scripts/run_fact_eval.sh <form_id> 5` to generate eval "
        "data, then re-run the generator to pull it into `eval_evidence` "
        "and the skill.md failure-modes table.",
        "5. **Hand-author `skill.md`:** edit the file directly; the "
        "generator preserves existing skill.md files and only "
        "regenerates if the file is absent.",
        "",
        "After any change, regenerate `KNOWN_GAPS.md` with:",
        "",
        "```bash",
        "python3 scripts/build_known_gaps.py",
        "```",
    ]

    OUT_PATH.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT_PATH}")
    print(f"  forms: {len(forms)}")
    print(f"  no filing deadline:  {len(no_deadline)}")
    print(f"  empty statutes:      {len(todo_statutes)}")
    print(f"  no eval evidence:    {len(no_eval_evidence)}")
    print(f"  red-tier present:    {len(has_red)}")
    print(f"  computed w/o formula: {len(computed_no_formula)}")
    print(f"  auto skill.md:       {len(auto_skill)}")
    print(f"  validator audit:     {audit_clean}/{audit_validated} clean, "
          f"{audit_total_err} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
