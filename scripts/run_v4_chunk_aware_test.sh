#!/usr/bin/env bash
# Refill pattern 1 across all 79 forms using fill_form.py with the
# 2026-05-13 chunk-aware-prompt fix: each chunk's prompt now includes
# a recap of slot-pattern fields already filled in earlier chunks so
# the LLM stops re-emitting items 1..K into slots K+1..N. Built on
# top of the v3 enumerated-value canonicalization rule and the auto-
# canonicalization post-step (canonicalize_enums.py) that now runs
# inside fill_form.py on every fill.
#
# Writes to intermediate/fact_eval/<form>/filled_1.v4.json so v2 and
# v3 baselines are preserved for before/after comparison.
#
# Report at intermediate/fact_eval/v4_chunk_aware_report.tsv.
#
# Usage:
#   bash scripts/run_v4_chunk_aware_test.sh           # foreground
#   setsid nohup bash scripts/run_v4_chunk_aware_test.sh \
#       < /dev/null > /tmp/v4_chunk_aware.log 2>&1 &  # overnight
#
# Cached: if filled_1.v4.json already exists and is non-empty, skip the
# Qwen call. Delete the file to force a refill.

set -uo pipefail
cd /path/to/maine-probate-forms-oss

QWEN_URL="${QWEN_URL:-http://localhost:8088}"
QWEN_MODEL="${QWEN_MODEL:-Qwen3.6-27B-FP8}"
REPORT="intermediate/fact_eval/v4_chunk_aware_report.tsv"

ts() { date +%H:%M:%S; }

log() { echo "[$(ts)] $*"; }

# Verify Qwen is up (fill_form.py appends /v1/chat/completions internally)
if ! curl -s -m 5 "${QWEN_URL}/v1/models" | grep -q '"object":"list"'; then
    log "ERROR: Qwen endpoint ${QWEN_URL}/v1/models not responding"
    exit 2
fi
log "Qwen endpoint OK: ${QWEN_URL} model=${QWEN_MODEL}"

# Header row
mkdir -p intermediate/fact_eval
echo -e "form_id\tv2_errors\tv4_errors\tdelta\tv2_vnic\tv4_vnic\tvnic_delta" > "$REPORT"

forms=$(ls trees/*.yaml | grep -vE "\.(opus_review|scenarios|fact_patterns)\.yaml$" | xargs -n1 basename | sed 's/\.yaml$//')
total=$(echo "$forms" | wc -l)
log "starting v4 canonicalization test: $total forms"

idx=0
for fid in $forms; do
    idx=$((idx + 1))
    out_dir="intermediate/fact_eval/$fid"
    mkdir -p "$out_dir"
    form_md="${out_dir}/form.md"
    patterns="${out_dir}/fact_patterns.yaml"
    filled_v2="${out_dir}/filled_1.json"
    filled_v4="${out_dir}/filled_1.v4.json"

    # Skip if no v2 baseline (no fact patterns generated for this form)
    if [[ ! -f "$form_md" || ! -f "$patterns" || ! -f "$filled_v2" ]]; then
        log "[$idx/$total] $fid: SKIP (no v2 baseline)"
        continue
    fi

    if [[ -f "$filled_v4" ]] && python3 -c "
import json,sys
d=json.loads(open('$filled_v4').read())
sys.exit(0 if d.get('answers') else 1)
" 2>/dev/null; then
        log "[$idx/$total] $fid: cached (v4 already present)"
    else
        # Remove any empty/failed v4 from a prior run before refilling.
        rm -f "$filled_v4"
        log "[$idx/$total] $fid: refilling pattern 1 with v4 prompt"
        start=$(date +%s)
        python3 scripts/fill_form.py "$form_md" "$patterns" \
            --pattern-id 1 --form-id "$fid" \
            --out "$filled_v4" --chunk-size 20 \
            --url "$QWEN_URL" --model "$QWEN_MODEL" \
            > "${out_dir}/fill_v4.log" 2>&1 || true
        duration=$(($(date +%s) - start))
        # Validate the v4 file actually has answers — empty / failed
        # fills get skipped and reported.
        if ! python3 -c "
import json,sys
try:
    d=json.loads(open('$filled_v4').read())
    sys.exit(0 if d.get('answers') else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            log "[$idx/$total] $fid: FAIL (no answers; ${duration}s; see fill_v4.log)"
            echo -e "$fid\tFAIL\tFAIL\tFAIL\tFAIL\tFAIL\tFAIL" >> "$REPORT"
            continue
        fi
        log "[$idx/$total] $fid: filled in ${duration}s"
    fi

    # Compare v2 vs v4 with the validator (errors + value_not_in_choices count)
    schema="repo/forms/$fid/schema.json"
    if [[ ! -f "$schema" ]]; then continue; fi

    v2_out=$(python3 scripts/validate_filled.py --schema "$schema" --filled "$filled_v2" 2>/dev/null || true)
    v4_out=$(python3 scripts/validate_filled.py --schema "$schema" --filled "$filled_v4" 2>/dev/null || true)
    v2_err=$(echo "$v2_out" | grep -c '^\s*\[error\]' || true)
    v4_err=$(echo "$v4_out" | grep -c '^\s*\[error\]' || true)
    v2_vnic=$(echo "$v2_out" | grep -c 'value_not_in_choices' || true)
    v4_vnic=$(echo "$v4_out" | grep -c 'value_not_in_choices' || true)
    delta=$((v4_err - v2_err))
    vnic_delta=$((v4_vnic - v2_vnic))
    echo -e "$fid\t$v2_err\t$v4_err\t$delta\t$v2_vnic\t$v4_vnic\t$vnic_delta" >> "$REPORT"
done

log ""
log "DONE — report at $REPORT"
log ""

# Summary
v2_total=$(awk -F'\t' 'NR>1 {sum+=$2} END{print sum}' "$REPORT")
v4_total=$(awk -F'\t' 'NR>1 {sum+=$3} END{print sum}' "$REPORT")
v2_vnic_total=$(awk -F'\t' 'NR>1 {sum+=$5} END{print sum}' "$REPORT")
v4_vnic_total=$(awk -F'\t' 'NR>1 {sum+=$6} END{print sum}' "$REPORT")
n_forms=$(awk -F'\t' 'NR>1' "$REPORT" | wc -l)
log "Forms refilled:           $n_forms"
log "Total errors v2 → v4:     $v2_total → $v4_total"
log "value_not_in_choices v2→v4: $v2_vnic_total → $v4_vnic_total"
