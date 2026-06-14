# Geometry review — tiered local voting audit of fill placement

Reviews every text-field and checkbox rect in every form's
`fill_geometry.json` for the three placement failure classes: typed text
**overlapping printed text**, **off its line vertically**, and **off the
blank horizontally**. Deterministic layers do the exhaustive sweep; local
vision models vote only on what those layers flag; a cloud adjudicator
breaks ties. No paid model touches the unfiltered corpus.

```
tier 0    sweep.py            analytic audit of all rects against the source
                              PDF's own text layer + vector drawings, then a
                              sentinel fill (unique ZQnnn token per widget,
                              X per checkbox) rendered with poppler, plus
                              red-boxed crops for everything flagged
tier 0.5  ocr_check.py        PaddleOCR (PP-OCRv6) reads the renders and
                              verifies each token's actual position vs its
                              rect — an independent modality (catches
                              appearance-pipeline bugs the rect math can't)
tier 1    vl_vote.py          N local vision LLMs vote a flat micro-schema
                              per flagged unit (+ clean controls to measure
                              voter false-positive rate); majority consensus
tier 2    adjudicate_codex.py Codex CLI (image attached) settles disputes
tier 3    apply_fixes.py      confirmed findings + codex-major disputes become
          fix_worklist.py     deterministic rect nudges in fill_geometry.json
                              (label x0-shift, county x1-trim, embedded trim),
                              gated by verify_fill_geometry.py + re-render
tier 4    build_poll.py       what stays ambiguous after the automated fixes
          serve_poll.py       (widget-on-heading, full-width detail fields)
          apply_decisions.py  goes to a browser poll: each unit's problem area
                              rendered current-vs-candidate, the reviewer picks
                              A/B/C/Other, picks apply back to fill_geometry
          rebuild_worklist.py honestly re-derives the open list from the
                              post-fix re-sweep (filters benign residuals)
```

## Human-review poll (tier 4)

When the automated fixes leave genuine widget-placement judgment calls,
render them as a poll and let a reviewer decide in a browser:

```bash
OUT=~/geom-review-out
python3 scripts/geometry_review/rebuild_worklist.py --out $OUT \
    --verify-dir <post-fix re-sweep dir> --write catalog/geometry_review_worklist.tsv
python3 scripts/geometry_review/build_poll.py  --out $OUT     # candidates + crops
python3 scripts/geometry_review/serve_poll.py  --out $OUT --port 8770
#   browse http://<host>:8770/  (Tailscale/LAN), or from a laptop:
#   ssh -L 8770:localhost:8770 <host>  then http://localhost:8770/
python3 scripts/geometry_review/apply_decisions.py --out $OUT --apply
```

Each unit shows the current placement (red box) beside candidate fixes
(blue box) — shift past the label, trim before printed text, drop to the
next line — each rendered with a realistic sample value through the real
fill pipeline. Picks (and free-text "Other" notes) are recorded to
`human_decisions.jsonl`; the poll is resumable. `apply_decisions.py` writes
the chosen rects back; "leave as-is"/"skip"/"other" make no geometry change.

Run (endpoints stay in the environment — never commit them):

```bash
OUT=~/geom-review-out
python3 scripts/geometry_review/sweep.py --out $OUT
FLAGS_use_mkldnn=0 python3 scripts/geometry_review/ocr_check.py --out $OUT
GEOM_VOTERS="a=http://host1:8088/v1|Qwen3.6-27B-FP8;b=http://host2:8080/v1|qwen3.6-35b" \
    python3 scripts/geometry_review/vl_vote.py --out $OUT --controls
python3 scripts/geometry_review/adjudicate_codex.py --out $OUT
```

Every stage is resumable (done-markers / seen-keys); artifacts live outside
the repo under `--out`.

Calibration notes:

- analytic thresholds were tuned so the vision-audited informal estate forms
  produce only their known cosmetic flags (~5-10/form, not ~50);
- mixed `"$_____"` / `"___COUNTY"` tokens split into line + label;
  Wingdings/private-use glyphs count as printed checkbox squares;
- `enable_mkldnn=False` is required — PaddlePaddle's oneDNN PIR path crashes
  (`ConvertPirAttribute2RuntimeAttribute`) on CPU as of paddle 3.x;
- centered/right-aligned fields skip the left-edge horizontal checks.
