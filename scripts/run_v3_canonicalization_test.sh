#!/usr/bin/env bash
# Refill pattern 1 across all 79 forms using the updated fill_form.py
# prompt (enumerated-value canonicalization rule added 2026-05-12).
#
# Writes to intermediate/fact_eval/<form>/filled_1.v3.json so the v2
# baseline (filled_1.json) is preserved for before/after comparison.
#
# After all fills complete, runs validate_filled.py on each v3 fill and
# writes a delta report to intermediate/fact_eval/v3_canonicalization_report.tsv.
#
# Usage:
#   bash scripts/run_v3_canonicalization_test.sh           # foreground
#   setsid nohup bash scripts/run_v3_canonicalization_test.sh \
#       < /dev/null > /tmp/v3_canonicalization.log 2>&1 &  # overnight
#
# Cached: if filled_1.v3.json already exists and is non-empty, skip the
# Qwen call. Delete the file to force a refill.

set -uo pipefail
cd /path/to/maine-probate-forms-oss

QWEN_URL="${QWEN_URL:-http://localhost:8088}"
QWEN_MODEL="${QWEN_MODEL:-Qwen3.6-27B-FP8}"
REPORT="intermediate/fact_eval/v3_canonicalization_report.tsv"

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
echo -e "form_id\tv2_errors\tv3_errors\tdelta\tv2_vnic\tv3_vnic\tvnic_delta" > "$REPORT"

forms=$(ls trees/*.yaml | grep -vE "\.(opus_review|scenarios|fact_patterns)\.yaml$" | xargs -n1 basename | sed 's/\.yaml$//')
total=$(echo "$forms" | wc -l)
log "starting v3 canonicalization test: $total forms"

idx=0
for fid in $forms; do
    idx=$((idx + 1))
    out_dir="intermediate/fact_eval/$fid"
    mkdir -p "$out_dir"
    form_md="${out_dir}/form.md"
    patterns="${out_dir}/fact_patterns.yaml"
    filled_v2="${out_dir}/filled_1.json"
    filled_v3="${out_dir}/filled_1.v3.json"

    # Skip if no v2 baseline (no fact patterns generated for this form)
    if [[ ! -f "$form_md" || ! -f "$patterns" || ! -f "$filled_v2" ]]; then
        log "[$idx/$total] $fid: SKIP (no v2 baseline)"
        continue
    fi

    if [[ -f "$filled_v3" ]] && python3 -c "
import json,sys
d=json.loads(open('$filled_v3').read())
sys.exit(0 if d.get('answers') else 1)
" 2>/dev/null; then
        log "[$idx/$total] $fid: cached (v3 already present)"
    else
        # Remove any empty/failed v3 from a prior run before refilling.
        rm -f "$filled_v3"
        log "[$idx/$total] $fid: refilling pattern 1 with v3 prompt"
        start=$(date +%s)
        python3 scripts/fill_form.py "$form_md" "$patterns" \
            --pattern-id 1 --form-id "$fid" \
            --out "$filled_v3" --chunk-size 20 \
            --url "$QWEN_URL" --model "$QWEN_MODEL" \
            > "${out_dir}/fill_v3.log" 2>&1 || true
        duration=$(($(date +%s) - start))
        # Validate the v3 file actually has answers — empty / failed
        # fills get skipped and reported.
        if ! python3 -c "
import json,sys
try:
    d=json.loads(open('$filled_v3').read())
    sys.exit(0 if d.get('answers') else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            log "[$idx/$total] $fid: FAIL (no answers; ${duration}s; see fill_v3.log)"
            echo -e "$fid\tFAIL\tFAIL\tFAIL\tFAIL\tFAIL\tFAIL" >> "$REPORT"
            continue
        fi
        log "[$idx/$total] $fid: filled in ${duration}s"
    fi

    # Compare v2 vs v3 with the validator (errors + value_not_in_choices count)
    schema="repo/forms/$fid/schema.json"
    if [[ ! -f "$schema" ]]; then continue; fi

    v2_out=$(python3 scripts/validate_filled.py --schema "$schema" --filled "$filled_v2" 2>/dev/null || true)
    v3_out=$(python3 scripts/validate_filled.py --schema "$schema" --filled "$filled_v3" 2>/dev/null || true)
    v2_err=$(echo "$v2_out" | grep -c '^\s*\[error\]' || true)
    v3_err=$(echo "$v3_out" | grep -c '^\s*\[error\]' || true)
    v2_vnic=$(echo "$v2_out" | grep -c 'value_not_in_choices' || true)
    v3_vnic=$(echo "$v3_out" | grep -c 'value_not_in_choices' || true)
    delta=$((v3_err - v2_err))
    vnic_delta=$((v3_vnic - v2_vnic))
    echo -e "$fid\t$v2_err\t$v3_err\t$delta\t$v2_vnic\t$v3_vnic\t$vnic_delta" >> "$REPORT"
done

log ""
log "DONE — report at $REPORT"
log ""

# Summary
v2_total=$(awk -F'\t' 'NR>1 {sum+=$2} END{print sum}' "$REPORT")
v3_total=$(awk -F'\t' 'NR>1 {sum+=$3} END{print sum}' "$REPORT")
v2_vnic_total=$(awk -F'\t' 'NR>1 {sum+=$5} END{print sum}' "$REPORT")
v3_vnic_total=$(awk -F'\t' 'NR>1 {sum+=$6} END{print sum}' "$REPORT")
n_forms=$(awk -F'\t' 'NR>1' "$REPORT" | wc -l)
log "Forms refilled:           $n_forms"
log "Total errors v2 → v3:     $v2_total → $v3_total"
log "value_not_in_choices v2→v3: $v2_vnic_total → $v3_vnic_total"
