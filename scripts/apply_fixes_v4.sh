#!/usr/bin/env bash
# Apply the residual-error fix chain on top of v4 outputs:
#   1. re-canonicalize (picks up STEM_MAP additions + prose-currency)
#   2. infer_gates    (writable_when absorption)
#   3. recompute_overwrite (formula-target overwrite)
#
# Final stage file: intermediate/fact_eval/<form>/filled_1.v4.fixed.json
#
# Usage: bash scripts/apply_fixes_v4.sh
set -uo pipefail
cd /path/to/maine-probate-forms-oss

count=0
for src in intermediate/fact_eval/*/filled_1.v4.json; do
    [[ -f "$src" ]] || continue
    dir=$(dirname "$src")
    fid=$(basename "$dir")
    schema="repo/forms/$fid/schema.json"
    [[ -f "$schema" ]] || continue

    canon="$dir/filled_1.v4.recanon.json"
    gated="$dir/filled_1.v4.recanon.gated.json"
    fixed="$dir/filled_1.v4.fixed.json"

    python3 scripts/canonicalize_enums.py \
        --schema "$schema" --filled "$src" --out "$canon" >/dev/null
    python3 scripts/infer_gates.py \
        --schema "$schema" --filled "$canon" --out "$gated" >/dev/null
    python3 scripts/recompute_overwrite.py \
        --schema "$schema" --filled "$gated" --out "$fixed" >/dev/null

    count=$((count + 1))
done
echo "Processed: $count form(s) → filled_1.v4.fixed.json"
