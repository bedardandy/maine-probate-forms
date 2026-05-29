#!/usr/bin/env bash
# Drive run_fact_eval_opus.sh across every form that has a tree YAML.
# Sequential per form (so the API isn't dogpiled), but each form
# parallelizes its 5 patterns up to FILL_PARALLEL concurrent Opus calls.
#
# Idempotent: skips forms whose eval_5.opus.yaml already exists.
# Writes intermediate/fact_eval/SUMMARY_opus.tsv.
#
# Usage: scripts/run_opus_all.sh [N_PATTERNS [FILL_PARALLEL]]
# Designed for detached use:
#   setsid nohup scripts/run_opus_all.sh \
#     < /dev/null > intermediate/fact_eval/opus_all.log 2>&1 &
set -uo pipefail

n_patterns="${1:-5}"
fill_parallel="${2:-3}"

summary=intermediate/fact_eval/SUMMARY_opus.tsv
mkdir -p intermediate/fact_eval
[[ -f "$summary" ]] || echo -e "form_id\tstatus\tn_filled\tn_evald\tduration_s" > "$summary"

trees=$(ls trees/*.yaml | grep -vE "\.(opus_review|scenarios)\.yaml$")
total=$(echo "$trees" | wc -l)
echo "[$(date +%H:%M:%S)] starting opus_all: $total forms, n=$n_patterns, fill_parallel=$fill_parallel"

idx=0
for tree in $trees; do
    idx=$((idx + 1))
    form_id=$(basename "$tree" .yaml)
    out_dir="intermediate/fact_eval/$form_id"
    mkdir -p "$out_dir"
    last_eval="${out_dir}/eval_${n_patterns}.opus.yaml"
    if [[ -f "$last_eval" ]]; then
        echo "[$(date +%H:%M:%S)] [$idx/$total] $form_id: cached"
        continue
    fi
    echo "[$(date +%H:%M:%S)] [$idx/$total] $form_id: starting"
    start=$(date +%s)
    rc=0
    bash scripts/run_fact_eval_opus.sh "$form_id" "$n_patterns" "$fill_parallel" \
        > "${out_dir}/run_orch.opus.log" 2>&1 || rc=$?
    duration=$(($(date +%s) - start))
    shopt -s nullglob
    ofilled=( "${out_dir}"/filled_[0-9].opus.json "${out_dir}"/filled_[0-9][0-9].opus.json )
    oevald=( "${out_dir}"/eval_[0-9].opus.yaml "${out_dir}"/eval_[0-9][0-9].opus.yaml )
    shopt -u nullglob
    n_filled=${#ofilled[@]}
    n_evald=${#oevald[@]}
    status=$([[ $rc -eq 0 && $n_evald -eq $n_patterns ]] && echo ok || echo partial)
    echo -e "${form_id}\t${status}\t${n_filled}\t${n_evald}\t${duration}" >> "$summary"
    echo "[$(date +%H:%M:%S)] [$idx/$total] $form_id: $status (filled=$n_filled evald=$n_evald in ${duration}s)"
done

echo "[$(date +%H:%M:%S)] opus_all complete"
