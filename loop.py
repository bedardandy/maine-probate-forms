"""Per-form production loop driver.

Picks PENDING forms at random, runs the full pipeline (analyze → detect →
validate → write), and records status in `intermediate/loop_state.db`.
Failures are retried up to MAX_ATTEMPTS, after which the form goes to
DEAD (the dead-letter state).

Subcommands:
  python -m loop init        — populate forms.db from downloaded PDFs
  python -m loop status      — print counts per status
  python -m loop next        — process one random PENDING form
  python -m loop drive [-n]  — keep processing until queue is empty (or n forms)
  python -m loop retry       — move FAILED rows back to PENDING
  python -m loop reset <id>  — force a single form back to PENDING
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

import click

import config
from modules import loop_state

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _run_one(form_id: str, category: str, pdf_path: str) -> int:
    """Run the per-form pipeline. Returns the field count written.

    Raises any exception encountered. Caller is responsible for catching
    and routing to the dead-letter state.
    """
    from modules.pdf_analyzer import analyze_form, load_analysis
    from modules.field_detector import detect_fields, load_detection
    from modules.vlm_validator import validate_form, load_validation
    from modules.acroform_writer import write_form
    from modules.taxonomy import name_fields, load_naming

    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"Source PDF missing: {pdf}")

    # Stage: analysis
    analysis = load_analysis(form_id)
    if analysis is None:
        logger.info("  analyzing %s", form_id)
        analysis = analyze_form(pdf, form_id, category)
        config.ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        (config.ANALYSIS_DIR / f"{form_id}.json").write_text(
            analysis.model_dump_json(indent=2)
        )

    # Stage: detection
    detection = load_detection(form_id)
    if detection is None:
        logger.info("  detecting %s", form_id)
        detection = detect_fields(analysis)
        config.DETECTION_DIR.mkdir(parents=True, exist_ok=True)
        (config.DETECTION_DIR / f"{form_id}.json").write_text(
            detection.model_dump_json(indent=2)
        )

    # Stage: VLM naming (default mode = naming-only, see config.VLM_MODE)
    validation = load_validation(form_id)
    if validation is None:
        logger.info("  naming %s via VLM", form_id)
        validation = validate_form(detection, str(pdf))
        config.VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
        (config.VALIDATION_DIR / f"{form_id}.json").write_text(
            validation.model_dump_json(indent=2)
        )

    # Stage: field-name finalization (taxonomy)
    naming = load_naming(form_id)
    if naming is None:
        logger.info("  finalizing names for %s", form_id)
        naming = name_fields(validation)
        config.NAMING_DIR.mkdir(parents=True, exist_ok=True)
        (config.NAMING_DIR / f"{form_id}.json").write_text(
            naming.model_dump_json(indent=2)
        )

    # Stage: AcroForm write
    logger.info("  writing AcroForm %s", form_id)
    out = write_form(naming, str(pdf))
    return out.field_count


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Per-form production loop with SQLite state + dead-letter."""
    _setup_logging(verbose)


@cli.command()
def init() -> None:
    """Populate the forms table from downloaded PDFs (idempotent)."""
    res = loop_state.init_from_downloads()
    click.echo(
        f"forms.db: +{res['added']} added, {res['skipped_existing']} already present, "
        f"{res['total']} total downloaded"
    )


@cli.command()
def status() -> None:
    """Show counts per status."""
    counts = loop_state.status_counts()
    if not counts:
        click.echo("forms.db is empty — run `loop init` first.")
        return
    total = sum(counts.values())
    click.echo(f"  total: {total}")
    for status_name in ("PENDING", "IN_PROGRESS", "DONE", "FAILED", "DEAD"):
        n = counts.get(status_name, 0)
        click.echo(f"  {status_name:<12s} {n:>4d}")


@cli.command(name="next")
def next_one() -> None:
    """Process one random PENDING form."""
    if not _process_one():
        click.echo("No PENDING forms.")


def _process_one() -> bool:
    row = loop_state.pick_pending()
    if row is None:
        return False
    form_id = row["form_id"]
    run_id = loop_state.begin_run(form_id)
    click.echo(f"→ {form_id} (attempt {row['attempts'] + 1})")
    try:
        n = _run_one(form_id, row["category"], row["pdf_path"])
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        new_status = loop_state.finish_err(form_id, run_id, traceback.format_exc())
        click.echo(f"  ✗ {new_status}: {err}", err=True)
    else:
        loop_state.finish_ok(form_id, run_id, n)
        click.echo(f"  ✓ DONE — {n} fields written")
    return True


@cli.command()
@click.option("-n", "--limit", type=int, default=None, help="Stop after N forms.")
def drive(limit: int | None) -> None:
    """Process PENDING forms until the queue empties (or --limit reached)."""
    processed = 0
    while True:
        if limit is not None and processed >= limit:
            click.echo(f"Hit limit={limit}, stopping.")
            break
        if not _process_one():
            click.echo("Queue empty.")
            break
        processed += 1


@cli.command()
def retry() -> None:
    """Move FAILED rows back to PENDING for another sweep."""
    n = loop_state.reset_failed_to_pending()
    click.echo(f"Moved {n} FAILED → PENDING.")


@cli.command()
@click.argument("form_id")
def reset(form_id: str) -> None:
    """Force a single form back to PENDING (clears attempts + last_error)."""
    if loop_state.reset_form(form_id):
        click.echo(f"Reset {form_id} → PENDING.")
    else:
        click.echo(f"Form {form_id} not found.")
        sys.exit(1)


@cli.command()
def dead() -> None:
    """List forms in the dead-letter (DEAD) state with their last error."""
    rows = loop_state.list_dead()
    if not rows:
        click.echo("No DEAD forms.")
        return
    for r in rows:
        click.echo(f"  {r['form_id']:30s} attempts={r['attempts']:>2d}  {r['last_error']}")


if __name__ == "__main__":
    cli()
