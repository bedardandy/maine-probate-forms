#!/usr/bin/env bash
# Process all fused PDFs through the full tree pipeline.
# Designed to be run detached: `nohup scripts/process_all_forms.sh &> /tmp/all.log &`
# Idempotent — re-running skips already-completed forms.
#
# Final report goes to intermediate/pipeline_status/SUMMARY.tsv with one
# row per form: form_id  category  result  duration_s  output_path
set -u

SUMMARY="intermediate/pipeline_status/SUMMARY.tsv"
mkdir -p "$(dirname "$SUMMARY")"
[[ -f "$SUMMARY" ]] || echo -e "form_id\tcategory\tresult\tduration_s\toutput" > "$SUMMARY"

total=0
ok=0
failed=0
skipped=0

while IFS= read -r pdf; do
    total=$((total + 1))
    # Strip "output_fused/" prefix and "_fused.pdf" suffix
    rel="${pdf#output_fused/}"
    category="${rel%%/*}"
    file="${rel#*/}"
    basename="${file%_fused.pdf}"
    form_id=$(echo "$basename" | grep -oE "^[A-Za-z]+-?[0-9]+" | head -1)

    # Skip if final PDF already exists AND has reasonable size
    dst="output_tree/${category}/${basename}_tree.pdf"
    if [[ -f "$dst" ]] && [[ $(stat -c%s "$dst") -gt 5000 ]]; then
        # Check if it's in the SUMMARY as ok
        if grep -qE "^${form_id}\s.*\sok\s" "$SUMMARY" 2>/dev/null; then
            skipped=$((skipped + 1))
            echo "[$total/79] SKIP  $form_id (already done)"
            continue
        fi
    fi

    echo ""
    echo "═══════════════════════════════════════════════════════════════════════"
    echo "[$total/79] PROCESSING  $form_id  ($category)"
    echo "═══════════════════════════════════════════════════════════════════════"
    start=$(date +%s)
    if bash scripts/process_one_form.sh "$category" "$basename"; then
        rc=0
        result="ok"
        ok=$((ok + 1))
    else
        rc=$?
        result="fail_rc${rc}"
        failed=$((failed + 1))
    fi
    duration=$(($(date +%s) - start))
    echo -e "${form_id}\t${category}\t${result}\t${duration}\t${dst}" >> "$SUMMARY"
    echo "[$total/79] $result  $form_id  (${duration}s)"
done < <(find output_fused -name "*_fused.pdf" | sort)

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "BATCH COMPLETE: total=$total ok=$ok failed=$failed skipped=$skipped"
echo "Summary: $SUMMARY"
echo "═══════════════════════════════════════════════════════════════════════"
