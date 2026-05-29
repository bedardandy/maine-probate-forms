#!/usr/bin/env bash
# Full per-form pipeline:
#   apply_tree → restyle → gen_validation_js → add_validate_button
#   → snap_checkboxes → snap_text_fields → pin_rect_overrides
# Usage: scripts/run_tree_pipeline.sh <form_id> <category> "<filename_without_extension>"
# Example: scripts/run_tree_pipeline.sh AD-008 adoption "AD-008 Report of Disbursements"
set -euo pipefail

form_id="$1"
category="$2"
basename="$3"

src="output_fused/${category}/${basename}_fused.pdf"
dst_dir="output_tree/${category}"
dst="${dst_dir}/${basename}_tree.pdf"
tree="trees/${form_id}.yaml"
js="trees/${form_id}.validate.js"

if [[ ! -f "$src" ]]; then
    echo "missing fused PDF: $src" >&2; exit 2
fi
if [[ ! -f "$tree" ]]; then
    echo "missing tree YAML: $tree" >&2; exit 2
fi

mkdir -p "$dst_dir"

echo "── apply_tree ──"
python3 scripts/apply_tree.py "$src" "$tree" --out "$dst" 2>&1 | tail -3

echo "── restyle ──"
python3 scripts/restyle_check_appearance.py "$dst" --out "$dst" 2>&1 | tail -1

echo "── gen_validation_js ──"
python3 scripts/gen_validation_js.py "$tree" --out "$js" 2>&1 | tail -1

echo "── add_validate_button ──"
python3 scripts/add_validate_button.py "$dst" "$js" --out "$dst" 2>&1 | tail -1

echo "── snap_checkboxes ──"
python3 scripts/snap_checkboxes.py "$dst" --snap-mode inner 2>&1 | tail -1

echo "── snap_text_fields ──"
python3 scripts/snap_text_fields.py "$dst" --canonical-height 12 2>&1 | tail -1

echo "── shrink_overlapping_tall ──"
python3 scripts/shrink_overlapping_tall.py "$dst" 2>&1 | tail -1

echo "── fix_column_overlaps ──"
python3 scripts/fix_column_overlaps.py "$dst" 2>&1 | tail -1

# pin_rect_overrides runs LAST so its explicit overrides aren't undone
# by fix_column_overlaps (which shrinks widgets that horizontally
# overlap a sibling — e.g. PP-410 W006 petitioner_interest, when
# widened by an override, overlaps with petitioner_name_address_email's
# 2nd-line wrap rect and gets shrunk back).
echo "── pin_rect_overrides ──"
python3 scripts/pin_rect_overrides.py "$dst" "$tree" 2>&1 | tail -1

echo "✓ ${dst}"
