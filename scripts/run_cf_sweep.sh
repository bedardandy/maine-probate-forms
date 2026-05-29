#!/bin/bash
# Phase 1: commonforms parameter sweep on 5 panel forms.
set -e
VENV=.venv-commonforms/bin
ROOT=output_commonforms/sweep
mkdir -p "$ROOT"

declare -a FORMS=(
  "estates|DE-101(I) Application for Informal - Intestate (Rev. 09-12-19).pdf"
  "estates|DE-104 PR Acceptance (Rev. 07-01-19).pdf"
  "gc_adults|PP-205 Joined Petition for Guardian and Conservator (Rev. 07-01-19).pdf"
  "name_change|NC-001 Petition for Name Change of Minor.pdf"
  "estates|DE-405 Inventory (Rev. 5-6-21).pdf"
)

declare -a VARIANTS=(
  "highres|--image-size 2400 --confidence 0.3"
  "precision|--image-size 1600 --confidence 0.45"
  "recall|--image-size 1600 --confidence 0.20"
  "sig_multi|--image-size 1600 --confidence 0.3 --use-signature-fields --multiline"
  "all_in|--image-size 2400 --confidence 0.3 --use-signature-fields --multiline"
)

for ventry in "${VARIANTS[@]}"; do
  vname="${ventry%|*}"
  vargs="${ventry#*|}"
  echo "##### variant=$vname args=$vargs #####"
  vdir="$ROOT/$vname"
  for entry in "${FORMS[@]}"; do
    cat="${entry%|*}"
    name="${entry#*|}"
    src="forms/$cat/$name"
    dst="$vdir/$cat/${name%.pdf}_commonforms.pdf"
    mkdir -p "$vdir/$cat"
    echo "  -- $name"
    start=$(date +%s)
    "$VENV/commonforms" "$src" "$dst" --device cuda $vargs 2>&1 | tail -2
    echo "     elapsed: $(($(date +%s) - start))s"
  done
done
echo "SWEEP_DONE"
