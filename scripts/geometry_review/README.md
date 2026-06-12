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
tier 3    (apply fixes)       confirmed findings become rect nudges in
                              fill_geometry.json, gated by
                              scripts/verify_fill_geometry.py + re-render
```

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
