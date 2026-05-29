#!/usr/bin/env bash
# Walk all base tree YAMLs, ensure each has form.md + fact_patterns.yaml in
# intermediate/fact_eval/<form_id>/. Idempotent — skips forms whose
# fact_patterns.yaml already exists.
#
# Runs PARALLELism Opus calls concurrently. Each Opus call is independent
# (different form, different prompt) so the API can fan out freely.
#
# Usage: scripts/gen_all_fact_patterns.sh [PARALLEL [N_PATTERNS]]
# Default: 4 concurrent jobs, 5 patterns per form.
set -uo pipefail
parallel="${1:-4}"
n_patterns="${2:-5}"

mkdir -p intermediate/fact_eval

trees=$(ls trees/*.yaml | grep -vE "\.(opus_review|scenarios)\.yaml$")
total=$(echo "$trees" | wc -l)
echo "[$(date +%H:%M:%S)] walking $total tree YAMLs (parallel=$parallel n=$n_patterns)"

started=0
skipped=0
for tree in $trees; do
    form_id=$(basename "$tree" .yaml)
    out_dir="intermediate/fact_eval/$form_id"
    form_md="$out_dir/form.md"
    patterns="$out_dir/fact_patterns.yaml"

    if [[ -f "$patterns" ]]; then
        skipped=$((skipped + 1))
        continue
    fi
    mkdir -p "$out_dir"

    if [[ ! -f "$form_md" ]]; then
        python3 scripts/form_to_markdown.py "$tree" --out "$form_md" \
            >/dev/null 2>&1 || { echo "[!] $form_id form.md failed"; continue; }
    fi

    # Throttle to parallel concurrent jobs.
    while [[ $(jobs -rp | wc -l) -ge $parallel ]]; do
        wait -n
    done

    (
        echo "[$(date +%H:%M:%S)] $form_id: gen_fact_patterns"
        python3 scripts/gen_fact_patterns.py "$form_md" \
            --out "$patterns" --n "$n_patterns" \
            >> "$out_dir/run.log" 2>&1
        if [[ -f "$patterns" ]]; then
            n=$(python3 -c "
import yaml; d=yaml.safe_load(open('$patterns').read());
print(len((d or {}).get('patterns', [])))" 2>/dev/null)
            echo "[$(date +%H:%M:%S)] $form_id: done ($n patterns)"
        else
            echo "[$(date +%H:%M:%S)] $form_id: FAILED"
        fi
    ) &
    started=$((started + 1))
done

wait
echo "[$(date +%H:%M:%S)] complete: started=$started skipped=$skipped"
