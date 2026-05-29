"""Field catalog: cross-form field inventory, CSV export, and quality metrics."""

import csv
import logging
from collections import Counter, defaultdict
from pathlib import Path

import config
from modules.schema import FormNaming

logger = logging.getLogger(__name__)


def _load_all_namings() -> list[FormNaming]:
    """Load all naming JSONs."""
    namings = []
    for path in sorted(config.NAMING_DIR.glob("*.json")):
        try:
            namings.append(FormNaming.model_validate_json(path.read_text()))
        except Exception as e:
            logger.warning("Failed to load %s: %s", path.name, e)
    return namings


def build_catalog() -> list[dict]:
    """Build a catalog of all fields across all forms.

    Returns list of dicts, each representing one field occurrence.
    """
    namings = _load_all_namings()
    catalog = []

    for naming in namings:
        for field in naming.fields:
            catalog.append(
                {
                    "form_id": naming.form_id,
                    "category": naming.category,
                    "field_name": field.field_name,
                    "field_type": field.field_type.value,
                    "page": field.page,
                    "nearby_label": field.nearby_label,
                    "confidence": field.confidence,
                    "x0": round(field.rect.x0, 1),
                    "y0": round(field.rect.y0, 1),
                    "x1": round(field.rect.x1, 1),
                    "y1": round(field.rect.y1, 1),
                }
            )

    return catalog


def export_csv(output_path: str | Path | None = None) -> str:
    """Export the full field catalog as CSV."""
    catalog = build_catalog()
    if not catalog:
        logger.warning("No naming data found")
        return ""

    if output_path is None:
        output_path = config.BASE_DIR / "field_catalog.csv"
    output_path = Path(output_path)

    fieldnames = list(catalog[0].keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(catalog)

    logger.info("Exported %d field records to %s", len(catalog), output_path)
    return str(output_path)


def field_name_index() -> dict[str, list[str]]:
    """Build an index: field_name → list of form_ids that contain it.

    Useful for finding which forms share the same field names.
    """
    catalog = build_catalog()
    index: dict[str, list[str]] = defaultdict(list)

    for entry in catalog:
        # Strip trailing _N disambiguation suffixes for grouping
        base_name = entry["field_name"]
        # Remove trailing _2, _3, etc. to get the base field concept
        import re

        base = re.sub(r"_\d+$", "", base_name)
        index[base].append(entry["form_id"])

    # Deduplicate form lists
    return {k: sorted(set(v)) for k, v in sorted(index.items())}


def quality_report() -> str:
    """Generate a text report of field detection quality metrics."""
    catalog = build_catalog()
    if not catalog:
        return "No data available."

    lines = []
    lines.append("=" * 60)
    lines.append("FIELD DETECTION QUALITY REPORT")
    lines.append("=" * 60)

    # Overall stats
    total = len(catalog)
    empty_label = sum(1 for c in catalog if not c["nearby_label"].strip())
    lines.append(f"\nTotal fields: {total}")
    lines.append(
        f"Fields with labels: {total - empty_label} ({100 * (total - empty_label) / total:.1f}%)"
    )
    lines.append(
        f"Fields without labels: {empty_label} ({100 * empty_label / total:.1f}%)"
    )

    # By field type
    lines.append("\n--- By Field Type ---")
    type_counts: Counter[str] = Counter()
    type_labeled: Counter[str] = Counter()
    for c in catalog:
        ft = c["field_type"]
        type_counts[ft] += 1
        if c["nearby_label"].strip():
            type_labeled[ft] += 1

    for ft in sorted(type_counts.keys()):
        count = type_counts[ft]
        labeled = type_labeled.get(ft, 0)
        pct = 100 * labeled / count if count else 0
        lines.append(f"  {ft:12s}: {count:5d} total, {labeled:5d} labeled ({pct:.1f}%)")

    # By category
    lines.append("\n--- By Category ---")
    cat_counts: Counter[str] = Counter()
    cat_labeled: Counter[str] = Counter()
    for c in catalog:
        cat = c["category"]
        cat_counts[cat] += 1
        if c["nearby_label"].strip():
            cat_labeled[cat] += 1

    for cat in sorted(cat_counts.keys()):
        count = cat_counts[cat]
        labeled = cat_labeled.get(cat, 0)
        pct = 100 * labeled / count if count else 0
        lines.append(
            f"  {cat:20s}: {count:5d} total, {labeled:5d} labeled ({pct:.1f}%)"
        )

    # By confidence
    lines.append("\n--- By Confidence ---")
    conf_buckets = {"high (>=0.9)": 0, "medium (0.7-0.89)": 0, "low (<0.7)": 0}
    for c in catalog:
        conf = c["confidence"]
        if conf >= 0.9:
            conf_buckets["high (>=0.9)"] += 1
        elif conf >= 0.7:
            conf_buckets["medium (0.7-0.89)"] += 1
        else:
            conf_buckets["low (<0.7)"] += 1

    for bucket, count in conf_buckets.items():
        lines.append(f"  {bucket:20s}: {count:5d} ({100 * count / total:.1f}%)")

    # Most common field names (cross-form reuse)
    lines.append("\n--- Most Reused Field Names (appear in 3+ forms) ---")
    name_index = field_name_index()
    reused = [(name, forms) for name, forms in name_index.items() if len(forms) >= 3]
    reused.sort(key=lambda x: -len(x[1]))
    for name, forms in reused[:20]:
        lines.append(f"  {name:40s}: {len(forms)} forms")

    # Forms with most fields
    lines.append("\n--- Forms by Field Count ---")
    form_counts: Counter[str] = Counter()
    for c in catalog:
        form_counts[c["form_id"]] += 1
    for form_id, count in form_counts.most_common(10):
        lines.append(f"  {form_id:20s}: {count} fields")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def cross_form_field_map() -> dict[str, dict[str, list[str]]]:
    """Build a map showing which canonical fields appear in which forms.

    Returns: {base_field_name: {form_id: [actual_field_names]}}
    Useful for understanding field reuse patterns.
    """
    import re

    catalog = build_catalog()
    result: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for c in catalog:
        base = re.sub(r"_\d+$", "", c["field_name"])
        result[base][c["form_id"]].append(c["field_name"])

    return {k: dict(v) for k, v in sorted(result.items())}
