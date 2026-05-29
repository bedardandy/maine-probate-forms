# Form router — design and evaluation

`tools/route_form.py` selects the right form from a plain-language fact pattern.
This documents why it is built the way it is.

## Why not embeddings / a vector DB

There are only 79 forms. The entire catalog fits in ~1.5k tokens, so the whole
menu is put in one prompt and a single LLM call picks the form. A vector store
adds infrastructure and a similarity hop for a set small enough to read directly.
Token efficiency comes from (1) one call instead of multi-turn repo exploration,
(2) a static catalog that caches as a prompt prefix, (3) constrained output.

## Variants evaluated

- **R0 lexical** — keyword overlap over title+category (the old `find_forms.py`).
- **R1 title** — LLM over `cat_title` (`id | category | title`, ~1.1k tok).
- **R2 enriched** — LLM over a blanket-enriched catalog (every form annotated,
  ~3.2k tok).
- **Surgical** (shipped) — `cat_title` plus a curated one-line disambiguation hint
  on ONLY the confusable clusters (~1.5k tok). See `tools/build_router_catalog.py`.

## Results

Two labelled sets were used: a 24-item core set and a 48-item harder set (terse,
lay phrasing, distractor-heavy, compound→primary, dense confusables, and an
out-of-scope NONE class), plus a paraphrase-consistency check. Models: a local
Qwen3.6-27B (vLLM FP8), and Gemma-4-31B / GPT-OSS-120B / GPT-OSS-20B via
OpenRouter.

Top-1 accuracy on the harder set (top models):

| Variant            | tokens | Gemma-4-31B | GPT-OSS-120B | GPT-OSS-20B | Qwen3.6-27B |
|--------------------|--------|-------------|--------------|-------------|-------------|
| R0 lexical         | 0      | ~54% (core) | —            | —           | —           |
| R1 title           | ~1.1k  | 98%         | 94%          | 71%         | ~85%\*      |
| R2 enriched        | ~3.2k  | 96%         | 92%          | 85%         | ~85%\*      |
| **Surgical**       | ~1.5k  | **98%**     | —            | **92%**     | **98%**     |

\* Qwen3.6's raw score was dragged by intermittent empty responses; with
retry-on-empty + enum validation its routing is ~95%+.

## Findings that drove the design

1. **Lexical is not enough** (~54%); a single LLM call is a decisive upgrade.
2. **Blanket enrichment is not worth it** — ~2.3x the tokens for no net gain on
   capable models, and it can add noise that perturbs easy items.
3. **Surgical disambiguation wins** — title-only plus hints on just the confusable
   clusters beats both R1 and blanket R2, at ~half blanket's tokens. It lifts the
   weakest model (GPT-OSS-20B 71%→92%) and Qwen3.6 (→98%).
4. **Validate the pick.** Smaller / thinking models occasionally emit an empty or
   out-of-catalog response; the router enum-validates and retries.
5. **NONE works.** Giving the model a NONE option lets it decline out-of-scope
   requests (divorce, trusts, general questions) without hurting recall on real
   ones. The one consistently hard case is a will contest, which has no shipped
   form and gets pulled toward a probate petition.

## The confusable clusters (where the hints live)

estate-vs-conservatorship parallels (bond / inventory / account / claim /
disputed-claim), petition-vs-acceptance (adult GC), adult-vs-minor, name-change
petition (CN-1) vs affidavit (AF-103), the notice family (N-105/N-115/N-117), and
special-administrator (DE-301) vs emergency GC (PP-507). Regenerate the catalog
with `python3 tools/build_router_catalog.py` after adding or removing forms.
