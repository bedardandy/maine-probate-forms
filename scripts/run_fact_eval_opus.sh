#!/usr/bin/env bash
# Opus-fill variant of run_fact_eval.sh.
# Uses claude -p --model opus instead of Qwen for the FILL step. Eval step
# is unchanged (already Opus).
#
# Artifacts use a .opus. infix to preserve Qwen artifacts side-by-side:
#   filled_<N>.opus.json
#   rendered_<N>.opus.md
#   eval_<N>.opus.yaml
#   eval_<N>.opus.raw.txt
#
# Opus has no GPU bottleneck, so we parallelize fills across patterns
# within a form. Eval step still runs in background after its fill.
#
# Usage: scripts/run_fact_eval_opus.sh <form_id> [N_PATTERNS] [FILL_PARALLEL]
set -uo pipefail

form_id="$1"
n_patterns="${2:-5}"
fill_parallel="${3:-3}"

tree="trees/${form_id}.yaml"
if [[ ! -f "$tree" ]]; then
    echo "[$form_id] missing tree: $tree" >&2; exit 2
fi
out_dir="intermediate/fact_eval/${form_id}"
mkdir -p "$out_dir"
status_log="${out_dir}/run.opus.log"

log() { echo "[$(date +%H:%M:%S)] [$form_id] $*" | tee -a "$status_log"; }

form_md="${out_dir}/form.md"
patterns="${out_dir}/fact_patterns.yaml"

# Prerequisites — gen_all_fact_patterns.sh should already have produced these
if [[ ! -f "$form_md" ]]; then
    log "form_to_markdown"
    python3 scripts/form_to_markdown.py "$tree" --out "$form_md" 2>&1 | tee -a "$status_log"
fi
if [[ ! -f "$patterns" ]]; then
    log "gen_fact_patterns (n=$n_patterns)"
    python3 scripts/gen_fact_patterns.py "$form_md" --out "$patterns" --n "$n_patterns" 2>&1 | tee -a "$status_log"
fi

# Phase 1: Opus fills in parallel (bounded). No GPU contention.
fill_pids=()
for pid in $(seq 1 "$n_patterns"); do
    filled="${out_dir}/filled_${pid}.opus.json"
    if [[ -f "$filled" ]] && python3 -c "
import json,sys
d=json.loads(open('$filled').read())
sys.exit(0 if d.get('answers') else 1)
" 2>/dev/null; then
        log "fill pattern $pid: cached"
        continue
    fi
    # Throttle
    while [[ $(jobs -rp | wc -l) -ge $fill_parallel ]]; do
        wait -n
    done
    log "spawning fill pattern $pid (parallel=$fill_parallel)"
    (
        python3 scripts/fill_form_opus.py "$form_md" "$patterns" \
            --pattern-id "$pid" --form-id "$form_id" \
            --out "$filled" --chunk-size 25 2>&1 \
            | sed "s|^|[fill p${pid}] |" | tee -a "$status_log"
    ) &
    fill_pids+=($!)
done
wait "${fill_pids[@]}" 2>/dev/null || true
log "all fills complete"

# Phase 2: render + Opus eval (also parallel)
eval_pids=()
for pid in $(seq 1 "$n_patterns"); do
    filled="${out_dir}/filled_${pid}.opus.json"
    rendered="${out_dir}/rendered_${pid}.opus.md"
    evald="${out_dir}/eval_${pid}.opus.yaml"

    if ! python3 -c "
import json,sys
d=json.loads(open('$filled').read())
sys.exit(0 if d.get('answers') else 1)
" 2>/dev/null; then
        log "fill pattern $pid had no answers; skipping render/eval"
        continue
    fi
    if [[ -f "$evald" ]]; then
        log "eval pattern $pid: cached"
        continue
    fi
    while [[ $(jobs -rp | wc -l) -ge $fill_parallel ]]; do
        wait -n
    done
    log "spawning render+eval for pattern $pid"
    (
        python3 scripts/render_filled.py "$tree" "$filled" \
            --out "$rendered" 2>&1 \
            | sed "s|^|[render p${pid}] |" | tee -a "$status_log"
        python3 scripts/eval_filled.py "$patterns" "$rendered" \
            --pattern-id "$pid" --form-id "$form_id" \
            --out "$evald" \
            --save-raw "${out_dir}/eval_${pid}.opus.raw.txt" 2>&1 \
            | sed "s|^|[eval p${pid}] |" | tee -a "$status_log"
    ) &
    eval_pids+=($!)
done

if [[ ${#eval_pids[@]} -gt 0 ]]; then
    log "waiting on ${#eval_pids[@]} background eval(s)"
    wait "${eval_pids[@]}" 2>/dev/null
fi

log "DONE → ${out_dir} (.opus.* artifacts)"
