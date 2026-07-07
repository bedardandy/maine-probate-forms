"""End-to-end orchestrator: Case + Event → routed form → filled JSON.

Pipeline:
  1. Resolve Case + Event (from seed_cases.yaml or stdin)
  2. router.Router().route() → top form_id
  3. case_to_narrative.render_patterns_yaml() → fact_patterns.yaml
  4. scripts/fill_form.py → filled_router.json
  5. scripts/canonicalize_enums.py → filled_router.canon.json
  6. scripts/infer_gates.py → filled_router.gated.json
  7. scripts/recompute_overwrite.py → filled_router.fixed.json
  8. scripts/validate_filled.py → final error count

Outputs land under intermediate/router/<case_id>/ so router runs don't
collide with the eval-pattern pipeline in intermediate/fact_eval/.

Usage:
  python3 -m router.run_case --case-id case_intestate_death_spouse_minors
  python3 -m router.run_case --case-id case_intestate_death_spouse_minors \
      --dry-run         # route + narrative only, skip Qwen call

The Qwen endpoint defaults match scripts/fill_form.py; pass --url /
--model to override.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import yaml

from router.case_to_narrative import render_patterns_yaml
from router.router import Router
from router.schemas import from_dict_case, from_dict_event


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _ok_status(err_count: int) -> str:
    """Status for a fill that completed its chain. A nonzero validator
    error count is NOT a success: downstream (chain summary, exit codes,
    any consumer keying on status) must be able to tell a clean fill from
    one that certified with errors. Returns "ok" only when err_count == 0,
    else "errors". Kept additive: the `errors` count is still emitted."""
    return "ok" if err_count == 0 else "errors"


def _validate(schema: pathlib.Path, filled: pathlib.Path) -> tuple[int, str]:
    out = subprocess.run(
        ["python3", "scripts/validate_filled.py",
         "--schema", str(schema), "--filled", str(filled)],
        capture_output=True, text=True, cwd=REPO_ROOT)
    err_count = sum(
        1 for line in out.stdout.splitlines() if "[error]" in line)
    return err_count, out.stdout


def _fill_and_validate(case, event, candidate, case_dict: dict,
                       out_dir: pathlib.Path, infix: str,
                       qwen_url: str, qwen_model: str,
                       fill_timeout_sec: int) -> dict:
    """Run the fill → post-process → validate chain for one candidate.

    Caller-supplied infix lets multi-form mode keep per-form output
    files (e.g. infix=".e1_death.DE-101" alongside ".e1_death.DE-301").
    Returns a result dict in the same shape as run_case's "ok" branch.
    """
    case_id = case.case_id
    form_id = candidate.form_id

    form_md = REPO_ROOT / "intermediate" / "fact_eval" / form_id / "form.md"
    if not form_md.exists():
        return {"status": "form_md_missing", "form_id": form_id,
                "case_id": case_id, "missing_path": str(form_md)}
    schema = REPO_ROOT / "repo" / "forms" / form_id / "schema.json"

    # Narrative is form-independent; render once per (case, event).
    patterns_doc = render_patterns_yaml(case, event)
    patterns_path = out_dir / f"fact_patterns{infix}.yaml"
    patterns_path.write_text(yaml.safe_dump(
        patterns_doc, sort_keys=False, allow_unicode=True))

    filled = out_dir / f"filled_router{infix}.json"
    cached_fixed = out_dir / f"filled_router{infix}.fixed.json"
    if cached_fixed.exists() and cached_fixed.stat().st_size > 0:
        err_count, val_out = _validate(schema, cached_fixed)
        print(f"[cache] {form_id} {infix} errors={err_count}")
        return {
            "status": _ok_status(err_count), "case_id": case_id,
            "form_id": form_id,
            "confidence": candidate.confidence, "reasons": candidate.reasons,
            "filled": str(cached_fixed), "errors": err_count,
            "validator_output": val_out, "cached": True,
        }

    print(f"[fill] {form_id} ← {patterns_path.name}")
    try:
        proc = subprocess.run(
            ["python3", "scripts/fill_form.py", str(form_md), str(patterns_path),
             "--pattern-id", "1", "--form-id", form_id,
             "--out", str(filled),
             "--url", qwen_url, "--model", qwen_model,
             "--schema", str(schema)],
            cwd=REPO_ROOT, timeout=fill_timeout_sec)
    except subprocess.TimeoutExpired:
        print(f"[fill] {form_id} TIMEOUT after {fill_timeout_sec}s",
              file=sys.stderr)
        return {"status": "fill_timeout", "form_id": form_id,
                "case_id": case_id, "timeout_sec": fill_timeout_sec}
    if proc.returncode != 0:
        return {"status": "fill_failed", "form_id": form_id,
                "case_id": case_id, "returncode": proc.returncode}

    canon = out_dir / f"filled_router{infix}.canon.json"
    gated = out_dir / f"filled_router{infix}.gated.json"
    sigdate = out_dir / f"filled_router{infix}.sigdate.json"
    fixed = out_dir / f"filled_router{infix}.fixed.json"
    chain_steps: list[tuple[str, str, pathlib.Path, pathlib.Path, list[str]]] = [
        ("canon",  "scripts/canonicalize_enums.py",   filled, canon,   []),
        ("gate",   "scripts/infer_gates.py",          canon,  gated,   []),
        ("sigdate","scripts/infer_signature_dates.py", gated,  sigdate,
         ["--event-date", str(event.date)]),
        ("recomp", "scripts/recompute_overwrite.py",  sigdate, fixed,  []),
    ]
    for label, script, src, dst, extra in chain_steps:
        proc = subprocess.run(
            ["python3", script, "--schema", str(schema),
             "--filled", str(src), "--out", str(dst)] + extra,
            cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"[{label}] failed: {proc.stderr}", file=sys.stderr)
            return {"status": f"{label}_failed", "form_id": form_id,
                    "case_id": case_id}

    err_count, val_out = _validate(schema, fixed)
    print(f"[validate] {form_id} errors={err_count}")
    return {
        "status": _ok_status(err_count), "case_id": case_id, "form_id": form_id,
        "confidence": candidate.confidence, "reasons": candidate.reasons,
        "filled": str(fixed), "errors": err_count,
        "validator_output": val_out,
    }


def run_case_multi(case_dict: dict, threshold: float = 0.7,
                   max_forms: int = 5,
                   qwen_url: str = "http://localhost:8088",
                   qwen_model: str = "Qwen3.6-27B-FP8",
                   tag: str = "",
                   fill_timeout_sec: int = 900) -> list[dict]:
    """Multi-form variant: fill every candidate with conf >= threshold.

    Returns a list of result dicts, one per filled form. Output files
    are tagged with form_id so they don't collide:
      filled_router.<tag>.<form_id>.fixed.json

    `max_forms` caps the number of forms filled per event (safety net
    against routing explosions).
    """
    case = from_dict_case(case_dict["case"])
    event = from_dict_event(case_dict["event"])
    case_id = case.case_id

    out_dir = REPO_ROOT / "intermediate" / "router" / case_id
    out_dir.mkdir(parents=True, exist_ok=True)

    r = Router()
    candidates = r.route(case, event, top_k=10)
    above = [c for c in candidates if c.confidence >= threshold][:max_forms]
    if not above:
        # Fall back to top-1 so we always produce one row even when no
        # candidate clears the bar.
        above = candidates[:1]
    print(f"[router-multi] case={case_id} event={event.type}: "
          f"{len(above)} form(s) ≥ {threshold} — "
          + ", ".join(f"{c.form_id}({c.confidence})" for c in above))

    # Persist multi-candidate routing decision once for the event.
    base_infix = f".{tag}" if tag else ""
    (out_dir / f"routing{base_infix}.json").write_text(json.dumps({
        "case_id": case_id, "event": event.to_dict(),
        "threshold": threshold,
        "selected": [{"form_id": c.form_id, "confidence": c.confidence,
                      "reasons": c.reasons} for c in above],
        "all_candidates": [{"form_id": c.form_id, "confidence": c.confidence}
                           for c in candidates[:10]],
    }, indent=2))

    results: list[dict] = []
    for cand in above:
        per_form_infix = f"{base_infix}.{cand.form_id}"
        res = _fill_and_validate(case, event, cand, case_dict, out_dir,
                                 per_form_infix, qwen_url, qwen_model,
                                 fill_timeout_sec)
        results.append(res)
    return results


def run_case(case_dict: dict, dry_run: bool = False, top_k: int = 5,
             qwen_url: str = "http://localhost:8088",
             qwen_model: str = "Qwen3.6-27B-FP8",
             tag: str = "",
             fill_timeout_sec: int = 900) -> dict:
    case = from_dict_case(case_dict["case"])
    event = from_dict_event(case_dict["event"])
    case_id = case.case_id

    out_dir = REPO_ROOT / "intermediate" / "router" / case_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # File-name infix so a chain that fires the same case across many
    # events doesn't clobber prior steps' outputs.
    infix = f".{tag}" if tag else ""

    # ── 1. Route ───────────────────────────────────────────────────────
    r = Router()
    candidates = r.route(case, event, top_k=top_k)
    if not candidates:
        return {"status": "no_candidates", "case_id": case_id}
    chosen = candidates[0]
    print(f"[router] case={case_id} event={event.type} → "
          f"chose {chosen.form_id} "
          f"(conf={chosen.confidence}, reasons={chosen.reasons})")
    if top_k > 1 and len(candidates) > 1:
        print(f"[router]   runner-up: {candidates[1].form_id} "
              f"(conf={candidates[1].confidence})")

    # Persist routing decision
    (out_dir / f"routing{infix}.json").write_text(json.dumps({
        "case_id": case_id,
        "event": event.to_dict(),
        "candidates": [
            {"form_id": c.form_id, "confidence": c.confidence,
             "reasons": c.reasons, "filer_role": c.filer_role}
            for c in candidates],
    }, indent=2))

    # ── 2. Render narrative ────────────────────────────────────────────
    patterns_doc = render_patterns_yaml(case, event)
    patterns_path = out_dir / f"fact_patterns{infix}.yaml"
    patterns_path.write_text(yaml.safe_dump(
        patterns_doc, sort_keys=False, allow_unicode=True))

    # form.md lives under the per-form eval directory because that's
    # where the existing pipeline already maintains schema-derived
    # markdown. We pull it from there.
    form_md = REPO_ROOT / "intermediate" / "fact_eval" / chosen.form_id / "form.md"
    if not form_md.exists():
        return {"status": "form_md_missing", "form_id": chosen.form_id,
                "missing_path": str(form_md)}

    schema = REPO_ROOT / "repo" / "forms" / chosen.form_id / "schema.json"

    if dry_run:
        print(f"[dry-run] narrative + routing written; would fill "
              f"{chosen.form_id} using {patterns_path}")
        return {"status": "dry_run", "case_id": case_id,
                "form_id": chosen.form_id,
                "narrative_path": str(patterns_path)}

    # ── 3. Fill (Qwen) ─────────────────────────────────────────────────
    # subprocess timeout below kills hung fills so a single bad event
    # can't block the rest of a batch indefinitely.
    filled = out_dir / f"filled_router{infix}.json"

    # Resume: if the post-processed file already exists, skip the fill +
    # validate the cached output. Lets killed chain batches resume
    # without burning ~5 min per already-done event.
    cached_fixed = out_dir / f"filled_router{infix}.fixed.json"
    if cached_fixed.exists() and cached_fixed.stat().st_size > 0:
        err_count, val_out = _validate(schema, cached_fixed)
        print(f"[cache] {chosen.form_id} {infix} errors={err_count}")
        return {
            "status": _ok_status(err_count), "case_id": case_id,
            "form_id": chosen.form_id,
            "confidence": chosen.confidence, "reasons": chosen.reasons,
            "filled": str(cached_fixed), "errors": err_count,
            "validator_output": val_out, "cached": True,
        }
    print(f"[fill] {chosen.form_id} ← {patterns_path.name}")
    try:
        proc = subprocess.run(
            ["python3", "scripts/fill_form.py", str(form_md), str(patterns_path),
             "--pattern-id", "1", "--form-id", chosen.form_id,
             "--out", str(filled),
             "--url", qwen_url, "--model", qwen_model,
             "--schema", str(schema)],
            cwd=REPO_ROOT, timeout=fill_timeout_sec)
    except subprocess.TimeoutExpired:
        print(f"[fill] {chosen.form_id} TIMEOUT after {fill_timeout_sec}s",
              file=sys.stderr)
        return {"status": "fill_timeout", "form_id": chosen.form_id,
                "timeout_sec": fill_timeout_sec}
    if proc.returncode != 0:
        return {"status": "fill_failed", "form_id": chosen.form_id,
                "returncode": proc.returncode}

    # ── 4. Post-process chain (canon → gate → sigdate → recompute) ────
    canon = out_dir / f"filled_router{infix}.canon.json"
    gated = out_dir / f"filled_router{infix}.gated.json"
    sigdate = out_dir / f"filled_router{infix}.sigdate.json"
    fixed = out_dir / f"filled_router{infix}.fixed.json"
    for label, script, src, dst, extra in [
        ("canon",  "scripts/canonicalize_enums.py",   filled, canon,   []),
        ("gate",   "scripts/infer_gates.py",          canon,  gated,   []),
        ("sigdate","scripts/infer_signature_dates.py", gated, sigdate,
         ["--event-date", str(event.date)]),
        ("recomp", "scripts/recompute_overwrite.py",  sigdate, fixed,  []),
    ]:
        proc = subprocess.run(
            ["python3", script, "--schema", str(schema),
             "--filled", str(src), "--out", str(dst)] + extra,
            cwd=REPO_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"[{label}] failed: {proc.stderr}", file=sys.stderr)
            return {"status": f"{label}_failed", "form_id": chosen.form_id}

    # ── 5. Validate ────────────────────────────────────────────────────
    err_count, val_out = _validate(schema, fixed)
    print(f"[validate] {chosen.form_id} errors={err_count}")

    return {
        "status": _ok_status(err_count),
        "case_id": case_id,
        "form_id": chosen.form_id,
        "confidence": chosen.confidence,
        "reasons": chosen.reasons,
        "filled": str(fixed),
        "errors": err_count,
        "validator_output": val_out,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=pathlib.Path,
                    default=pathlib.Path(__file__).parent / "seed_cases.yaml")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="Route + render narrative only; skip Qwen call.")
    ap.add_argument("--url", default="http://localhost:8088")
    ap.add_argument("--model", default="Qwen3.6-27B-FP8")
    args = ap.parse_args()

    seed = yaml.safe_load(args.seed.read_text())
    matches = [c for c in seed.get("cases", []) if c.get("id") == args.case_id]
    if not matches:
        print(f"no seed case '{args.case_id}'", file=sys.stderr)
        return 2

    result = run_case(matches[0], dry_run=args.dry_run,
                      qwen_url=args.url, qwen_model=args.model)
    print()
    print("=" * 60)
    print(json.dumps({k: v for k, v in result.items()
                      if k != "validator_output"}, indent=2))
    return 0 if result.get("status") in ("ok", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
