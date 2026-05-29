#!/usr/bin/env bash
# Drive run_fact_eval.sh across every form that has a tree YAML. Each form
# is sequential (Qwen GPU is the bottleneck), but within a form, Opus evals
# pipeline behind subsequent Qwen fills.
#
# Idempotent: skips forms whose eval_5.yaml already exists (or whatever
# n_patterns'th eval is).
#
# Writes intermediate/fact_eval/SUMMARY.tsv: form_id, status, n_filled,
# n_evald, duration_s.
#
# Usage: scripts/run_all_fact_evals.sh [N_PATTERNS]
# Designed for detached/nohup use:
#   setsid nohup scripts/run_all_fact_evals.sh \
#     < /dev/null > intermediate/fact_eval/run_all.log 2>&1 &
set -uo pipefail

n_patterns="${1:-5}"
summary=intermediate/fact_eval/SUMMARY.tsv
mkdir -p intermediate/fact_eval
[[ -f "$summary" ]] || echo -e "form_id\tstatus\tn_filled\tn_evald\tduration_s" > "$summary"

trees=$(ls trees/*.yaml | grep -vE "\.(opus_review|scenarios)\.yaml$")
total=$(echo "$trees" | wc -l)
echo "[$(date +%H:%M:%S)] starting run_all_fact_evals: $total forms, n=$n_patterns"

idx=0
for tree in $trees; do
    idx=$((idx + 1))
    form_id=$(basename "$tree" .yaml)
    out_dir="intermediate/fact_eval/$form_id"
    last_eval="${out_dir}/eval_${n_patterns}.yaml"

    if [[ -f "$last_eval" ]]; then
        echo "[$(date +%H:%M:%S)] [$idx/$total] $form_id: cached (eval_${n_patterns}.yaml exists)"
        continue
    fi

    echo "[$(date +%H:%M:%S)] [$idx/$total] $form_id: starting"
    start=$(date +%s)
    rc=0
    bash scripts/run_fact_eval.sh "$form_id" "$n_patterns" \
        > "${out_dir}/run_orch.log" 2>&1 || rc=$?
    duration=$(($(date +%s) - start))

    # Count actual qwen-v2 artifacts on disk (exclude opus and v1 backups)
    shopt -s nullglob
    qfilled=( "${out_dir}"/filled_[0-9].json "${out_dir}"/filled_[0-9][0-9].json )
    qevald=( "${out_dir}"/eval_[0-9].yaml "${out_dir}"/eval_[0-9][0-9].yaml )
    shopt -u nullglob
    n_filled=${#qfilled[@]}
    n_evald=${#qevald[@]}
    status=$([[ $rc -eq 0 && $n_evald -eq $n_patterns ]] && echo ok || echo partial)
    echo -e "${form_id}\t${status}\t${n_filled}\t${n_evald}\t${duration}" >> "$summary"
    echo "[$(date +%H:%M:%S)] [$idx/$total] $form_id: $status (filled=$n_filled evald=$n_evald in ${duration}s)"
done

echo "[$(date +%H:%M:%S)] run_all_fact_evals complete"
