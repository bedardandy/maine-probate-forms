#!/usr/bin/env bash
# Apply infer_gates to every <stage>.json file in intermediate/fact_eval.
# canonicalize_enums already runs auto via fill_form.py, so this is just
# the second pass (gate inference).
#
# Usage:
#   bash scripts/apply_post_processing.sh v4   # process filled_1.v4.json files
#   bash scripts/apply_post_processing.sh v3.canon
#
# Output: <stage>.gated.json sibling files + a summary line.
set -uo pipefail
cd /path/to/maine-probate-forms-oss

STAGE="${1:-v4}"
SRC_SUFFIX=".${STAGE}.json"
OUT_SUFFIX=".${STAGE}.gated.json"

count=0
inferred=0
forms_touched=0
for src in intermediate/fact_eval/*/filled_1${SRC_SUFFIX}; do
    [[ -f "$src" ]] || continue
    fid=$(basename "$(dirname "$src")")
    schema="repo/forms/$fid/schema.json"
    [[ -f "$schema" ]] || continue
    out="${src%${SRC_SUFFIX}}${OUT_SUFFIX}"
    msg=$(python3 scripts/infer_gates.py --schema "$schema" \
        --filled "$src" --out "$out" 2>&1 >/dev/null)
    count=$((count + 1))
    if [[ "$msg" == *"gate(s) inferred"* ]]; then
        n=$(echo "$msg" | grep -oP '\(\K\d+(?= gate)' | head -1)
        if [[ "${n:-0}" -gt 0 ]]; then
            inferred=$((inferred + n))
            forms_touched=$((forms_touched + 1))
            echo "  $fid: +$n gate(s)"
        fi
    fi
done
echo
echo "Processed: $count form(s)"
echo "Total gates inferred: $inferred across $forms_touched form(s)"
echo "Output suffix: $OUT_SUFFIX"
