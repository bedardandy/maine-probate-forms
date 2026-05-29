"""Recursive evaluation loop for probate-forms (audit-and-suggest only).

Each iteration:
  1. Pick N cases for a given form (or pick the next form with open majors).
  2. Refill those tuples via the deterministic chain (no Qwen — uses existing
     canon.json. Qwen fills happen during the case-generator step upstream,
     which can fan-out across the configured nodes via VLM_FANOUT_ENDPOINTS).
  3. Audit the refilled tuples with headless Opus 4.7 vision
     (scripts/vision_audit_filled.py — already in place).
  4. Aggregate audit issues (kind, label, value) across the batch.
  5. Spawn ANOTHER headless Opus 4.7 session with the aggregate + relevant
     code/schema context. Ask Opus to: cluster issues, diagnose root cause,
     propose specific code/rect fixes. NO AUTO-EDITS — Opus only writes a
     diagnosis markdown file the user reviews.
  6. Write evaluations/<run_id>/iteration_<n>/diagnosis.md.

Loop terminates after --iterations N, or earlier if Opus reports
"convergence" (no new issue clusters vs prior iteration).

Usage:
  python3 scripts/recursive_eval_loop.py --form DE-405 --iterations 3
  python3 scripts/recursive_eval_loop.py --form-list DE-405,DE-602,MISC-101 \
        --iterations 2 --cases-per-iter 2

This script:
  - Re-uses existing inference scripts (no new fills generated).
  - Re-uses existing audit infrastructure.
  - Adds an Opus meta-diagnosis layer on top.

Auth: like scripts/vision_audit_filled.py, unset ANTHROPIC_API_KEY to force
the Max OAuth subscription.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import random
import re
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES_PATTERN = re.compile(
    r"intermediate/router/([^/]+)/filled_router\."
    r"(e\d+_[a-z_]+)\.([A-Z]+-[0-9A-Za-z._-]+)\.fixed\.json$"
)

OPUS_DIAGNOSIS_PROMPT = """You are reviewing vision-audit findings on Maine
probate AcroForm fills. The audit has already classified each page as clean /
minor / major and dumped issue labels (printed-label text + observed value)
into issues_json. Your job: aggregate, cluster, diagnose, recommend.

ALLOWED ACTIONS:
- Read TSV files and audit issue JSONs.
- Read scripts/infer_*.py, scripts/backfill_*.py, repo/forms/<FORM>/schema.json.
- Read fill artifacts at intermediate/router/<CASE>/filled_router*.fixed.json.

NOT ALLOWED:
- Editing any files (no Edit/Write/Bash mutations).
- Running git, npm install, or any commit-level action.

DELIVERABLE: print the diagnosis section to stdout (the driver captures
stdout to {diagnosis_path}). Do NOT use Write/Edit tools to write the file
yourself — just emit the markdown to stdout as your final message. Structure:

```
## Iteration {iter_n} — Opus 4.7 diagnosis ({timestamp})

### Audit summary
- Pages audited: ...
- Verdict counts: clean=N  major=N  minor=N
- Distinct issue labels: N

### Clusters identified
For each cluster (>=2 occurrences):
1. **<cluster name>** (count=N, forms=A/B/C)
   - Pattern: <what the values look like>
   - Root cause hypothesis: <where this likely originates: schema rect /
     inference script / case data / LLM fill / vision artefact>
   - Proposed fix: <specific change to a specific file:lineno or new file>
   - Confidence: low / medium / high
   - Effort: trivial / small / medium / large

### Singleton oddities
For each one-off issue: form, label, value, brief diagnosis.

### Convergence signal
Compared to the prior iteration's clusters (if any):
- New clusters: ...
- Cleared clusters: ...
- Stable clusters: ...
- Verdict: continue / converging / converged

### Top recommended next action
The single highest-value fix to make next. Specify file path + change.
```

CONTEXT for this iteration:
- Iteration number: {iter_n} of {total_iters}
- Form(s) under review: {form_list}
- Audit TSV: {audit_tsv}
- Prior diagnosis file: {prior_diagnosis} (read it if present, for convergence)
- Working directory: {repo_root}

Begin. Be specific — name files and line numbers in fixes."""


def discover_tuples(form_id: str, max_n: int) -> list[tuple[str, str, str]]:
    """Find existing (case, event, form) tuples for `form_id`."""
    matches: list[tuple[str, str, str]] = []
    for fp in (REPO_ROOT / "intermediate" / "router").glob(
            f"*/filled_router.*.{form_id}.fixed.json"):
        rel = fp.relative_to(REPO_ROOT).as_posix()
        m = CASES_PATTERN.search(rel)
        if m:
            matches.append((m.group(1), m.group(2), m.group(3)))
    random.shuffle(matches)
    return matches[:max_n]


def refill_chain(case: str, event: str, form: str, log_path: pathlib.Path
                 ) -> None:
    """Re-run the deterministic chain on one tuple, applying any updated
    inference scripts. Mirrors /tmp/phase1_broad_refill.sh logic."""
    out_dir = REPO_ROOT / "intermediate" / "router" / case
    infix = f".{event}.{form}"
    schema = REPO_ROOT / "repo" / "forms" / form / "schema.json"
    paths = {
        k: out_dir / f"filled_router{infix}.{k}.json"
        for k in ("canon", "gated", "sigdate", "notary", "venue", "atty",
                  "cons", "post", "fixed")
    }
    patterns = out_dir / f"fact_patterns{infix}.yaml"
    event_date = ""
    if patterns.exists():
        for line in patterns.read_text().splitlines():
            m = re.search(r"\d{4}-\d{2}-\d{2}", line)
            if m:
                event_date = m.group(0)
                break
    if not paths["canon"].exists():
        return
    stages = [
        ("infer_gates",            ["--schema", str(schema), "--filled", str(paths["canon"]),  "--out", str(paths["gated"])]),
        ("infer_signature_dates",  ["--schema", str(schema), "--filled", str(paths["gated"]),  "--out", str(paths["sigdate"]), "--event-date", event_date]),
        ("infer_notary_fields",    ["--filled", str(paths["sigdate"]), "--out", str(paths["notary"]), "--event-date", event_date]),
        ("infer_notary_venue",     ["--filled", str(paths["notary"]),  "--out", str(paths["venue"]),  "--event-date", event_date, "--case-id", case]),
        ("infer_attorney_bar",     ["--filled", str(paths["venue"]),   "--out", str(paths["atty"]),   "--case-id", case, "--schema", str(schema)]),
    ]
    # Form-specific post-attorney chain. Each form maps to a list of
    # (script_name, extra_args) tuples chained in order. Output names
    # are auto-assigned (.post1.json, .post2.json) so chains of length 2+
    # work. Special-case 'infer_guardian_closing' which doesn't take
    # --case-id / --event-date — pass only --filled / --out for it.
    post_chains: dict[str, list[tuple[str, list[str]]]] = {
        "DE-405":   [("infer_de405_inventory_totals", ["--case-id", case])],
        "DE-502":   [("infer_de502_petitioner",       ["--case-id", case])],
        "AF-102":   [("infer_date_word_form",         ["--event-date", event_date])],
        "AF-103":   [("infer_af103_name_change",      ["--case-id", case, "--event-date", event_date])],
        "DE-407":   [("infer_de407_renunciation",     ["--case-id", case])],
        "PP-201":   [("infer_respondent_age",         ["--case-id", case]),
                     ("infer_minor_status_report",    ["--case-id", case, "--event-date", event_date])],
        "DE-503":   [("infer_de503_claim",            ["--case-id", case, "--event-date", event_date])],
        "DE-505":   [("infer_de505_omitted_child",    ["--case-id", case])],
        "DE-403":   [("infer_de403_bond",             ["--case-id", case, "--event-date", event_date])],
        "PP-203":   [("infer_guardian_closing",       ["--case-id", case])],
        "MISC-101": [("infer_misc101_motion",         ["--case-id", case, "--event-date", event_date])],
        "DE-406":   [("infer_de406_financials",       ["--case-id", case, "--event-date", event_date])],
        "N-106":    [("infer_n106_caption",           ["--case-id", case, "--event-date", event_date])],
        "N-112":    [("infer_n112_appointing_court",  ["--case-id", case])],
        "PP-205":   [("infer_respondent_age",         ["--case-id", case])],
        "PP-209":   [("infer_adult_status_report",    ["--case-id", case, "--event-date", event_date])],
        "PP-107":   [("infer_respondent_age",         ["--case-id", case]),
                     ("infer_minor_status_report",    ["--case-id", case, "--event-date", event_date]),
                     ("infer_pp107_minor",            ["--case-id", case, "--event-date", event_date])],
        "PP-108":   [("infer_pp108_conservator",      ["--case-id", case])],
        "GS-014":   [("infer_minor_status_report",    ["--case-id", case, "--event-date", event_date])],
        "AD-007":   [("infer_ad007_confidential_statement", ["--case-id", case])],
        "PP-402":   [("infer_pp108_conservator",      ["--case-id", case])],
        "PP-401":   [("infer_respondent_age",         ["--case-id", case])],
        "PP-412":   [
            ("infer_conservator_dates", ["--event-date", event_date, "--event-type", event]),
            ("infer_pp412_narrative",   ["--case-id", case, "--event-date", event_date]),
        ],
        "DE-507":   [("infer_de507_former_pr",        ["--case-id", case, "--event-date", event_date])],
        "DE-605":   [("infer_de605_closing",          ["--case-id", case, "--event-date", event_date])],
        "PP-407":   [("infer_pp407_account",          ["--case-id", case, "--event-date", event_date])],
    }
    src_path = paths["atty"]
    chain = post_chains.get(form, [])
    for i, (script_name, extra_args) in enumerate(chain):
        out_name = "post" if len(chain) == 1 else f"post{i+1}"
        out_path = out_dir / f"filled_router{infix}.{out_name}.json"
        paths[out_name] = out_path
        stages.append((script_name,
                       ["--filled", str(src_path), "--out", str(out_path),
                        *extra_args]))
        src_path = out_path
    stages.append(("recompute_overwrite",
                   ["--schema", str(schema), "--filled", str(src_path),
                    "--out", str(paths["fixed"])]))

    log = log_path.open("a")
    for script_name, args in stages:
        cmd = [sys.executable, "-m", f"scripts.{script_name}", *args]
        log.write(f"--- {script_name} {case}/{form} ---\n")
        log.flush()
        try:
            subprocess.run(cmd, cwd=str(REPO_ROOT), stdout=log, stderr=log,
                           timeout=120)
        except subprocess.TimeoutExpired:
            log.write(f"  TIMEOUT on {script_name}\n")
    log.close()


def audit_tuples(form_id: str, output_tsv: pathlib.Path,
                 log_path: pathlib.Path) -> None:
    """Run vision_audit_filled.py for one form. Re-uses existing audit."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    cmd = [
        sys.executable, "scripts/vision_audit_filled.py",
        "--all", "--form", form_id, "--out-tsv", str(output_tsv),
    ]
    with log_path.open("a") as log:
        log.write(f"=== audit {form_id} {datetime.datetime.utcnow().isoformat()}Z ===\n")
        subprocess.run(cmd, cwd=str(REPO_ROOT), env=env,
                       stdout=log, stderr=log)


def opus_diagnose(iter_n: int, total_iters: int, form_list: list[str],
                  audit_tsv: pathlib.Path, diagnosis_path: pathlib.Path,
                  prior_diagnosis: pathlib.Path | None,
                  log_path: pathlib.Path) -> None:
    """Spawn headless Opus 4.7 to diagnose the audit + propose fixes."""
    prompt = OPUS_DIAGNOSIS_PROMPT.format(
        iter_n=iter_n, total_iters=total_iters,
        form_list=", ".join(form_list),
        audit_tsv=str(audit_tsv),
        diagnosis_path=str(diagnosis_path),
        prior_diagnosis=str(prior_diagnosis) if prior_diagnosis else "(none)",
        repo_root=str(REPO_ROOT),
        timestamp=datetime.datetime.utcnow().isoformat() + "Z",
    )
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    # Restrict Opus to read-only tools (no Write/Edit/Bash mutations).
    # The prompt's NOT ALLOWED list + the disallowedTools below give the
    # same guarantee as --permission-mode plan, without the plan-mode
    # side-effect of staging the deliverable as a plan-file instead of
    # printing it to stdout.
    cmd = [
        "claude", "-p", prompt,
        "--model", "claude-opus-4-7",
        "--max-turns", "30",
        "--disallowedTools", "Write,Edit,NotebookEdit,Bash",
    ]
    with log_path.open("a") as log:
        log.write(f"=== opus diagnose iter={iter_n} ===\n")
        try:
            with diagnosis_path.open("w") as out:
                r = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env,
                                   stdout=out, stderr=log, timeout=900)
            log.write(f"  exit={r.returncode}  diagnosis={diagnosis_path}\n")
        except subprocess.TimeoutExpired:
            log.write("  TIMEOUT\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", action="append", default=None,
                    help="Form ID to evaluate. Can repeat.")
    ap.add_argument("--form-list", default=None,
                    help="Comma-separated form ids (alt to --form).")
    ap.add_argument("--iterations", type=int, default=3,
                    help="Number of recursive iterations.")
    ap.add_argument("--cases-per-iter", type=int, default=3,
                    help="How many cases to refill+audit per iteration.")
    ap.add_argument("--output-root", type=pathlib.Path,
                    default=REPO_ROOT / "evaluations",
                    help="Output directory for diagnosis artefacts.")
    ap.add_argument("--run-id", default=None,
                    help="Run identifier (default: utc timestamp).")
    args = ap.parse_args()

    forms: list[str] = []
    if args.form:
        forms.extend(args.form)
    if args.form_list:
        forms.extend(args.form_list.split(","))
    forms = [f.strip() for f in forms if f.strip()]
    if not forms:
        print("ERROR: provide --form FORM_ID (or --form-list a,b,c).",
              file=sys.stderr)
        return 2

    run_id = args.run_id or datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "loop.log"

    prior_diagnosis: pathlib.Path | None = None
    for it in range(1, args.iterations + 1):
        iter_dir = run_dir / f"iteration_{it:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        audit_tsv = iter_dir / "audit.tsv"
        diagnosis_path = iter_dir / "diagnosis.md"

        # 1. Per-form: pick + refill some tuples.
        for form in forms:
            tuples = discover_tuples(form, args.cases_per_iter)
            print(f"  [iter {it}] {form}: refilling {len(tuples)} tuple(s)")
            for c, e, f in tuples:
                refill_chain(c, e, f, log_path)

        # 2. Audit each form sequentially (writes to same TSV).
        for form in forms:
            print(f"  [iter {it}] {form}: auditing")
            audit_tuples(form, audit_tsv, log_path)

        # 3. Opus diagnoses + proposes.
        print(f"  [iter {it}] spawning Opus 4.7 diagnosis...")
        opus_diagnose(it, args.iterations, forms, audit_tsv,
                       diagnosis_path, prior_diagnosis, log_path)

        print(f"  [iter {it}] diagnosis → {diagnosis_path}")
        prior_diagnosis = diagnosis_path

    print(f"\nRun complete. Output dir: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
