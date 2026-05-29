"""CLI orchestrator for the Maine Probate Forms AcroForm pipeline."""

import logging

import click

import config


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """Maine Probate Forms AcroForm Pipeline.

    Run individual stages or the full pipeline to convert flat PDF forms
    into interactive AcroForm PDFs with consistent field naming.
    """
    _setup_logging(verbose)


# ── Pipeline stages ───────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--category", "-c", multiple=True, help="Only download specific categories."
)
@click.option("--force", is_flag=True, help="Re-download even if files exist.")
def download(category: tuple[str, ...], force: bool) -> None:
    """Stage 1: Download all PDF forms from maineprobate.net."""
    from download import download_forms, FORM_URLS

    categories = list(category) if category else None
    total = sum(
        len(v) for k, v in FORM_URLS.items() if categories is None or k in categories
    )
    click.echo(f"Downloading up to {total} forms...")

    result = download_forms(categories=categories, force=force)

    click.echo(
        f"Done: {result['success']} downloaded, "
        f"{result['skipped']} skipped, {result['failed']} failed"
    )
    if result["errors"]:
        click.echo("Errors:")
        for e in result["errors"]:
            click.echo(f"  {e}")


@cli.command()
@click.option("--form-id", "-f", multiple=True, help="Only analyze specific form IDs.")
def analyze(form_id: tuple[str, ...]) -> None:
    """Stage 2: Extract text and drawings from downloaded PDFs."""
    from download import list_downloaded_forms
    from modules.pdf_analyzer import analyze_all_forms

    forms = list_downloaded_forms()
    if form_id:
        forms = [f for f in forms if f["form_id"] in form_id]

    click.echo(f"Analyzing {len(forms)} forms...")
    paths = analyze_all_forms(forms)
    click.echo(f"Done: {len(paths)} analysis files written")


@cli.command()
@click.option(
    "--form-id", "-f", multiple=True, help="Only detect fields for specific form IDs."
)
@click.option("--force", is_flag=True, help="Overwrite existing detection files.")
def detect(form_id: tuple[str, ...], force: bool) -> None:
    """Stage 3: Detect form fields using heuristics."""
    from modules.field_detector import detect_all_forms

    ids = list(form_id) if form_id else None
    click.echo("Running heuristic field detection...")
    paths = detect_all_forms(form_ids=ids, force=force)
    click.echo(f"Done: {len(paths)} detection files written")


@cli.command()
@click.option("--form-id", "-f", multiple=True, help="Only validate specific form IDs.")
def validate(form_id: tuple[str, ...]) -> None:
    """Stage 4: Validate detections with VLM (requires running vLLM server)."""
    from modules.vlm_validator import validate_all_forms

    ids = list(form_id) if form_id else None
    click.echo(f"Running VLM validation (endpoint: {config.VLM_API_BASE})...")
    paths = validate_all_forms(form_ids=ids)
    click.echo(f"Done: {len(paths)} validation files written")
    click.echo("Review intermediate/validation/*.json before proceeding.")


@cli.command()
@click.option(
    "--form-id", "-f", multiple=True, help="Only name fields for specific form IDs."
)
@click.option("--force", is_flag=True, help="Overwrite existing naming files.")
def name(form_id: tuple[str, ...], force: bool) -> None:
    """Stage 5: Apply consistent field naming taxonomy."""
    from modules.taxonomy import name_all_forms

    ids = list(form_id) if form_id else None
    click.echo("Applying field naming taxonomy...")
    paths = name_all_forms(form_ids=ids, force=force)
    click.echo(f"Done: {len(paths)} naming files written")


@cli.command()
@click.option("--form-id", "-f", multiple=True, help="Only write specific form IDs.")
@click.option("--force", is_flag=True, help="Overwrite existing output PDFs.")
def write(form_id: tuple[str, ...], force: bool) -> None:
    """Stage 6: Write AcroForm fields to output PDFs."""
    from modules.acroform_writer import write_all_forms

    ids = list(form_id) if form_id else None
    click.echo("Writing AcroForm fields to PDFs...")
    paths = write_all_forms(form_ids=ids, force=force)
    click.echo(f"Done: {len(paths)} fillable PDFs created in output/")


@cli.command()
@click.option("--skip-vlm", is_flag=True, help="Skip VLM validation stage.")
@click.option("--force-download", is_flag=True, help="Re-download all forms.")
@click.option("--force", is_flag=True, help="Overwrite all intermediate/output files.")
def run_all(skip_vlm: bool, force_download: bool, force: bool) -> None:
    """Run the full pipeline (all stages in order)."""
    from download import download_forms, list_downloaded_forms
    from modules.pdf_analyzer import analyze_all_forms
    from modules.field_detector import detect_all_forms
    from modules.vlm_validator import validate_all_forms
    from modules.taxonomy import name_all_forms
    from modules.acroform_writer import write_all_forms

    # Stage 1
    click.echo("=" * 60)
    click.echo("STAGE 1: Download")
    click.echo("=" * 60)
    result = download_forms(force=force_download)
    click.echo(
        f"  {result['success']} downloaded, {result['skipped']} skipped, {result['failed']} failed"
    )

    # Stage 2
    click.echo("=" * 60)
    click.echo("STAGE 2: PDF Analysis")
    click.echo("=" * 60)
    forms = list_downloaded_forms()
    paths = analyze_all_forms(forms)
    click.echo(f"  {len(paths)} forms analyzed")

    # Stage 3
    click.echo("=" * 60)
    click.echo("STAGE 3: Heuristic Field Detection")
    click.echo("=" * 60)
    paths = detect_all_forms(force=force)
    click.echo(f"  {len(paths)} forms processed")

    # Stage 4
    if not skip_vlm:
        click.echo("=" * 60)
        click.echo("STAGE 4: VLM Validation")
        click.echo("=" * 60)
        paths = validate_all_forms()
        click.echo(f"  {len(paths)} forms validated")
        click.echo("  Review intermediate/validation/*.json if needed")
    else:
        click.echo("=" * 60)
        click.echo("STAGE 4: VLM Validation [SKIPPED]")
        click.echo("=" * 60)

    # Stage 5
    click.echo("=" * 60)
    click.echo("STAGE 5: Field Naming")
    click.echo("=" * 60)
    paths = name_all_forms(force=force)
    click.echo(f"  {len(paths)} forms named")

    # Stage 6
    click.echo("=" * 60)
    click.echo("STAGE 6: AcroForm Writing")
    click.echo("=" * 60)
    paths = write_all_forms(force=force)
    click.echo(f"  {len(paths)} fillable PDFs created")

    click.echo("=" * 60)
    click.echo("Pipeline complete!")
    click.echo("=" * 60)


# ── Utility commands ──────────────────────────────────────────────────────


@cli.command()
def status() -> None:
    """Show pipeline status: counts of files at each stage."""
    from download import list_downloaded_forms

    forms = list_downloaded_forms()
    analyses = (
        list(config.ANALYSIS_DIR.glob("*.json")) if config.ANALYSIS_DIR.exists() else []
    )
    detections = (
        list(config.DETECTION_DIR.glob("*.json"))
        if config.DETECTION_DIR.exists()
        else []
    )
    validations = (
        list(config.VALIDATION_DIR.glob("*.json"))
        if config.VALIDATION_DIR.exists()
        else []
    )
    namings = (
        list(config.NAMING_DIR.glob("*.json")) if config.NAMING_DIR.exists() else []
    )

    output_pdfs = []
    preview_pdfs = []
    if config.OUTPUT_DIR.exists():
        for d in config.OUTPUT_DIR.iterdir():
            if not d.is_dir():
                continue
            if d.name == "previews":
                preview_pdfs.extend(d.glob("*.pdf"))
            else:
                output_pdfs.extend(d.glob("*.pdf"))

    click.echo("Pipeline Status")
    click.echo("-" * 40)
    click.echo(f"  Downloaded PDFs:       {len(forms)}")
    click.echo(f"  Analyzed (Stage 2):    {len(analyses)}")
    click.echo(f"  Detected (Stage 3):    {len(detections)}")
    click.echo(f"  Validated (Stage 4):   {len(validations)}")
    click.echo(f"  Named (Stage 5):       {len(namings)}")
    click.echo(f"  Output PDFs (Stage 6): {len(output_pdfs)}")
    click.echo(f"  Preview PDFs:          {len(preview_pdfs)}")


@cli.command()
def report() -> None:
    """Show field detection quality report."""
    from modules.catalog import quality_report

    click.echo(quality_report())


@cli.command()
@click.option("--output", "-o", default=None, help="Output CSV file path.")
def catalog(output: str | None) -> None:
    """Export field catalog as CSV."""
    from modules.catalog import export_csv

    path = export_csv(output)
    if path:
        click.echo(f"Catalog exported to {path}")


@cli.command()
@click.argument("pdf_path")
def fields(pdf_path: str) -> None:
    """List all fillable fields in a PDF."""
    from modules.form_filler import list_form_fields

    field_list = list_form_fields(pdf_path)
    if not field_list:
        click.echo("No fields found.")
        return

    click.echo(f"{'Field Name':40s} {'Type':12s} {'Page':5s} {'Value'}")
    click.echo("-" * 70)
    for f in field_list:
        click.echo(
            f"{f['field_name']:40s} {f['field_type']:12s} {f['page']:<5d} {f['current_value']}"
        )
    click.echo(f"\nTotal: {len(field_list)} fields")


@cli.command()
@click.argument("pdf_path")
@click.argument("json_data")
@click.option("--output", "-o", default=None, help="Output PDF path.")
def fill(pdf_path: str, json_data: str, output: str | None) -> None:
    """Fill a PDF form with data from a JSON file.

    PDF_PATH: Path to the fillable PDF.
    JSON_DATA: Path to JSON file with field_name→value mapping.
    """
    from modules.form_filler import fill_form_from_json

    result = fill_form_from_json(pdf_path, json_data, output)
    click.echo(f"Filled form saved to {result}")


@cli.command()
@click.argument("pdf_path")
@click.option("--output", "-o", default=None, help="Output template JSON path.")
def template(pdf_path: str, output: str | None) -> None:
    """Generate a JSON fill-data template from a fillable PDF."""
    from modules.form_filler import generate_template

    result = generate_template(pdf_path, output)
    click.echo(f"Template written to {result}")


@cli.command()
@click.option("--form-id", "-f", multiple=True, help="Only preview specific form IDs.")
@click.option("--force", is_flag=True, help="Overwrite existing preview PDFs.")
def preview(form_id: tuple[str, ...], force: bool) -> None:
    """Generate visual debug PDFs with field overlays."""
    from modules.preview import render_all_previews

    ids = list(form_id) if form_id else None
    click.echo("Rendering field preview PDFs...")
    paths = render_all_previews(form_ids=ids, force=force)
    click.echo(f"Done: {len(paths)} preview PDFs in output/previews/")


if __name__ == "__main__":
    cli()
