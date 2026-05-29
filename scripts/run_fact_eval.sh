#!/usr/bin/env bash
# Run the fact-pattern eval pipeline for one form:
#   form_to_markdown → gen_fact_patterns → fill_form × N → render_filled × N → eval_filled × N
# Idempotent: skips steps whose output already exists.
#
# Usage: scripts/run_fact_eval.sh <form_id>
# Example: scripts/run_fact_eval.sh PP-507
set -uo pipefail

form_id="$1"
n_patterns="${2:-5}"

tree="trees/${form_id}.yaml"
if [[ ! -f "$tree" ]]; then
    echo "[$form_id] missing tree: $tree" >&2; exit 2
fi
out_dir="intermediate/fact_eval/${form_id}"
mkdir -p "$out_dir"
status_log="${out_dir}/run.log"

log() { echo "[$(date +%H:%M:%S)] [$form_id] $*" | tee -a "$status_log"; }

# 1. form markdown
form_md="${out_dir}/form.md"
if [[ ! -f "$form_md" ]]; then
    log "form_to_markdown"
    python3 scripts/form_to_markdown.py "$tree" --out "$form_md" 2>&1 | tee -a "$status_log"
else
    log "form_to_markdown: cached"
fi

# 2. fact patterns (Opus)
patterns="${out_dir}/fact_patterns.yaml"
if [[ ! -f "$patterns" ]]; then
    log "gen_fact_patterns (n=$n_patterns)"
    python3 scripts/gen_fact_patterns.py "$form_md" --out "$patterns" --n "$n_patterns" 2>&1 | tee -a "$status_log"
else
    log "gen_fact_patterns: cached"
fi

# 3-5. for each pattern: fill (Qwen, BLOCKING — local GPU is shared) →
#       background (render → Opus eval). Qwen advances to pattern N+1 while
#       the prior pattern's eval runs concurrently. Pipelining cuts per-form
#       wallclock roughly in half (max(qwen-sequential, opus-evals-parallel)
#       rather than their sum).
eval_pids=()
for pid in $(seq 1 "$n_patterns"); do
    filled="${out_dir}/filled_${pid}.json"
    rendered="${out_dir}/rendered_${pid}.md"
    evald="${out_dir}/eval_${pid}.yaml"

    # fill (blocking on Qwen)
    if [[ ! -f "$filled" ]] || ! python3 -c "
import json,sys
d=json.loads(open('$filled').read())
sys.exit(0 if d.get('answers') else 1)
" 2>/dev/null; then
        log "fill pattern $pid"
        python3 scripts/fill_form.py "$form_md" "$patterns" \
            --pattern-id "$pid" --form-id "$form_id" \
            --out "$filled" --chunk-size 20 2>&1 | tee -a "$status_log"
    else
        log "fill pattern $pid: cached"
    fi

    # Verify fill actually produced answers (Qwen sometimes returns empty)
    if ! python3 -c "
import json,sys
d=json.loads(open('$filled').read())
sys.exit(0 if d.get('answers') else 1)
" 2>/dev/null; then
        log "fill pattern $pid FAILED (no answers); skipping render/eval"
        continue
    fi

    # render+eval in BACKGROUND — Qwen advances to next pattern immediately
    if [[ ! -f "$evald" ]]; then
        log "spawning render+eval for pattern $pid in background"
        (
            python3 scripts/render_filled.py "$tree" "$filled" \
                --out "$rendered" 2>&1 | tee -a "$status_log"
            python3 scripts/eval_filled.py "$patterns" "$rendered" \
                --pattern-id "$pid" --form-id "$form_id" \
                --out "$evald" \
                --save-raw "${out_dir}/eval_${pid}.raw.txt" 2>&1 \
                | tee -a "$status_log"
        ) &
        eval_pids+=($!)
    else
        log "eval pattern $pid: cached"
    fi
done

# Wait for all background evals to finish before declaring DONE
if [[ ${#eval_pids[@]} -gt 0 ]]; then
    log "waiting on ${#eval_pids[@]} background eval(s)"
    wait "${eval_pids[@]}" 2>/dev/null
fi

log "DONE → ${out_dir}"
