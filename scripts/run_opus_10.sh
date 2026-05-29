#!/usr/bin/env bash
# Drive run_fact_eval_opus.sh across 10 forms.
#   - 4 head-to-head with existing Qwen evals: PP-507, N-115, AD-008, AD-009
#   - 6 fresh small forms: DE-104, AF-103, AF-102, APP-1, AF-101, AD-028
#
# Forms run SEQUENTIALLY (one at a time) but each form parallelizes its 5
# patterns. This caps Opus API concurrency at ~3/form rather than letting
# 10×5=50 calls dogpile the API. Saves a SUMMARY_opus.tsv.
#
# Usage: scripts/run_opus_10.sh
# Designed for detached use:
#   setsid nohup scripts/run_opus_10.sh \
#     < /dev/null > intermediate/fact_eval/opus_10.log 2>&1 &
set -uo pipefail

forms=(
    DE-104    # 7 fields — sanity check
    AF-103    # 11
    AF-102    # 12
    APP-1     # 15
    N-115     # 17 (head-to-head)
    AD-009    # 19 (head-to-head)
    AF-101    # 21
    AD-028    # 24
    AD-008    # 26 (head-to-head)
    PP-507    # ~99 (head-to-head, the big one)
)

n_patterns=5
fill_parallel=3
summary=intermediate/fact_eval/SUMMARY_opus.tsv
[[ -f "$summary" ]] || echo -e "form_id\tstatus\tn_filled\tn_evald\tduration_s" > "$summary"

echo "[$(date +%H:%M:%S)] starting Opus pass: ${#forms[@]} forms, n=$n_patterns, fill_parallel=$fill_parallel"

for form_id in "${forms[@]}"; do
    out_dir="intermediate/fact_eval/$form_id"
    last_eval="${out_dir}/eval_${n_patterns}.opus.yaml"
    if [[ -f "$last_eval" ]]; then
        echo "[$(date +%H:%M:%S)] $form_id: cached (eval_${n_patterns}.opus.yaml exists)"
        continue
    fi
    echo "[$(date +%H:%M:%S)] $form_id: starting"
    start=$(date +%s)
    rc=0
    bash scripts/run_fact_eval_opus.sh "$form_id" "$n_patterns" "$fill_parallel" \
        > "${out_dir}/run_orch.opus.log" 2>&1 || rc=$?
    duration=$(($(date +%s) - start))
    n_filled=$(ls "${out_dir}"/filled_*.opus.json 2>/dev/null | wc -l)
    n_evald=$(ls "${out_dir}"/eval_*.opus.yaml 2>/dev/null | wc -l)
    status=$([[ $rc -eq 0 && $n_evald -eq $n_patterns ]] && echo ok || echo partial)
    echo -e "${form_id}\t${status}\t${n_filled}\t${n_evald}\t${duration}" >> "$summary"
    echo "[$(date +%H:%M:%S)] $form_id: $status (filled=$n_filled evald=$n_evald in ${duration}s)"
done

echo "[$(date +%H:%M:%S)] Opus pass complete"
