#!/usr/bin/env bash
# Eval-only resume for forms whose Qwen v2 batch wrote fills/renders but
# whose eval_*.yaml files were lost to the Opus 5h rate-limit cascade.
#
# Strategy: for each form lacking eval_N.yaml (Qwen flavor, excluding
# .opus.yaml), call eval_filled.py against the existing rendered_N.md +
# fact_patterns.yaml. Skips already-eval'd patterns.
#
# Usage: scripts/resume_qwen_evals.sh [N_PATTERNS [PARALLEL]]
set -uo pipefail
n_patterns="${1:-5}"
parallel="${2:-3}"

log_root=intermediate/fact_eval/qwen_v2_eval_resume.log
echo "[$(date +%H:%M:%S)] starting eval-resume (n=$n_patterns, parallel=$parallel)" > "$log_root"

trees=$(ls trees/*.yaml | grep -vE "\.(opus_review|scenarios)\.yaml$")
total=0
done_n=0
for tree in $trees; do
    form_id=$(basename "$tree" .yaml)
    out_dir="intermediate/fact_eval/$form_id"
    [[ -d "$out_dir" ]] || continue
    # any missing pattern?
    missing=()
    for pid in $(seq 1 "$n_patterns"); do
        [[ -f "$out_dir/eval_${pid}.yaml" ]] && continue
        [[ -f "$out_dir/rendered_${pid}.md" ]] || continue
        [[ -f "$out_dir/fact_patterns.yaml" ]] || continue
        missing+=("$pid")
    done
    if [[ ${#missing[@]} -eq 0 ]]; then continue; fi
    total=$((total + 1))
    echo "[$(date +%H:%M:%S)] $form_id: re-evaluating patterns ${missing[*]}" | tee -a "$log_root"

    pids=()
    for pid in "${missing[@]}"; do
        (
            python3 scripts/eval_filled.py \
                "$out_dir/fact_patterns.yaml" \
                "$out_dir/rendered_${pid}.md" \
                --pattern-id "$pid" --form-id "$form_id" \
                --out "$out_dir/eval_${pid}.yaml" \
                --save-raw "$out_dir/eval_${pid}.raw.txt" \
                >> "$log_root" 2>&1
        ) &
        pids+=($!)
        # bound concurrency
        if (( ${#pids[@]} >= parallel )); then
            wait -n 2>/dev/null || true
            # repack pids — drop completed
            new_pids=()
            for p in "${pids[@]}"; do
                if kill -0 "$p" 2>/dev/null; then new_pids+=("$p"); fi
            done
            pids=("${new_pids[@]}")
        fi
    done
    wait "${pids[@]}" 2>/dev/null
    done_n=$((done_n + 1))
    echo "[$(date +%H:%M:%S)] $form_id: done [$done_n/$total]" | tee -a "$log_root"
done

echo "[$(date +%H:%M:%S)] eval-resume complete: $done_n forms processed" | tee -a "$log_root"
