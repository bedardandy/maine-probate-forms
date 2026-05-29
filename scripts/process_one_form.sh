#!/usr/bin/env bash
# Cradle-to-grave processing for one form:
#   fused PDF → digest → LLM tree (with retries) → Opus review → apply_tree
#   → restyle → JS → button → snap_checkboxes → snap_text_fields → pin_rect_overrides
#
# Usage: scripts/process_one_form.sh <category> "<basename>"
# Example: scripts/process_one_form.sh adoption "AD-008 Report of Disbursements"
#
# Idempotent: skips steps whose output already exists.
# Exit codes:
#   0   success
#   2   missing input
#   3   LLM tree generation failed
#   4   Opus review failed
#   5   apply_tree / downstream pipeline failed
set -uo pipefail

category="$1"
basename="$2"

src="output_fused/${category}/${basename}_fused.pdf"
form_id=$(echo "$basename" | grep -oE "^[A-Za-z]+-?[0-9]+" | head -1)

if [[ ! -f "$src" ]]; then
    echo "[$form_id] missing fused PDF: $src" >&2
    exit 2
fi

digest="intermediate/digest/${form_id}.txt"
tree="trees/${form_id}.yaml"
status_log="intermediate/pipeline_status/${form_id}.log"
mkdir -p "$(dirname "$digest")" "$(dirname "$status_log")"

log() {
    echo "[$(date +%H:%M:%S)] [$form_id] $*" | tee -a "$status_log"
}

# ── Step 1: digest ────────────────────────────────────────────────
if [[ ! -f "$digest" ]]; then
    log "digest: extracting from $src"
    python3 scripts/build_form_digest.py "$src" --out "$digest" 2>&1 | tee -a "$status_log" || {
        log "digest: FAILED"
        exit 2
    }
else
    log "digest: cached"
fi

# ── Step 2: LLM tree with retries ────────────────────────────────
if [[ ! -f "$tree" ]]; then
    log "build_form_tree: generating with --retries 5"
    python3 scripts/build_form_tree.py "$digest" --retries 5 \
        --out "$tree" --save-raw "intermediate/llm_raw/${form_id}" \
        2>&1 | tee -a "$status_log"
    if [[ ! -f "$tree" ]]; then
        log "build_form_tree: FAILED — no tree produced"
        exit 3
    fi
else
    log "build_form_tree: cached ($tree exists)"
fi

# ── Step 3: Opus multimodal review ───────────────────────────────
opus_tree="${tree%.yaml}.opus_review.yaml"
if [[ ! -f "$opus_tree" ]]; then
    log "opus_review: running"
    python3 scripts/opus_review_tree.py "$digest" "$tree" "$src" \
        --save-raw "intermediate/opus_raw/${form_id}.txt" \
        2>&1 | tee -a "$status_log" || {
        log "opus_review: FAILED — keeping LLM tree"
        # Fall through to use the original tree if Opus review fails
    }
else
    log "opus_review: cached"
fi

# Pick which tree to use: prefer Opus-reviewed, fall back to raw LLM
if [[ -f "$opus_tree" ]]; then
    final_tree="$opus_tree"
    log "using Opus-reviewed tree"
else
    final_tree="$tree"
    log "using raw LLM tree (no Opus correction)"
fi

# ── Step 4: full pipeline (apply → snap → pin) ───────────────────
dst="output_tree/${category}/${basename}_tree.pdf"
mkdir -p "$(dirname "$dst")"
# Temporarily symlink the chosen tree into the conventional location
# expected by run_tree_pipeline.sh, then run it.
if [[ "$final_tree" != "$tree" ]]; then
    cp "$tree" "${tree}.preopus.bak"
    cp "$final_tree" "$tree"
fi
log "pipeline: apply_tree → restyle → JS → button → snap → pin"
bash scripts/run_tree_pipeline.sh "$form_id" "$category" "$basename" \
    2>&1 | tee -a "$status_log"
pipeline_rc=$?
if [[ $pipeline_rc -ne 0 ]]; then
    log "pipeline: FAILED (rc=$pipeline_rc)"
    [[ -f "${tree}.preopus.bak" ]] && mv "${tree}.preopus.bak" "$tree"
    exit 5
fi

if [[ -f "${tree}.preopus.bak" ]]; then
    rm "${tree}.preopus.bak"
fi
log "SUCCESS → $dst"
