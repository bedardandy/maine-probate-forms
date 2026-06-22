# Citation inspector: catching legal hallucinations in narrative fields

The deterministic fill path (`fill_plan` → `fill_pdf` → `verify_filled`) resolves
hard facts and checks that values *land* on the page. But the `llm_over_narrative`
fields are composed by an LLM, and `verify_filled` explicitly leaves their *legal
content* out of scope ("Narrative/blank/unresolved buckets are out of scope").
The citation inspector fills that gap: it checks whether the statutes and cases a
narrative field cites actually say what the draft claims they say.

> **Not legal advice. Experimental.** The inspector is an LLM over an AI-annotated
> statute layer; it points to issues to weigh, not conclusions. Verify everything
> against the current statute and the actual opinions.

## The idea: force closed-vocabulary placeholders, then substitute and inspect

LLMs are good at *argument* and bad at *verbatim recall of law*. So we never let
the model write statutory text from memory. Instead:

1. **Draft with placeholders.** The drafting model cites only by emitting
   `[[REF: cite]]` placeholders, copied from an enumerated **closed vocabulary** —
   the citations the form's `statutes.json` actually uses.
2. **Substitute the real authority.** Each placeholder is deterministically
   replaced with the verbatim authority text: statutes fetched **live** from
   legislature.maine.gov (cached + SHA-pinned), cases from the summarized holdings
   in `caselaw.json`.
3. **Inspect.** A separate, zero-temperature "cold-eyes" inspector LLM compares
   each conclusion against the literal authority text and returns a structured
   per-citation verdict (`pass` / `fail` / `unclear`) with the exact `quote` it
   relied on.

### Two hard-fail gates make invented citations impossible to pass silently

This is the key difference from a naive "ask the model to cite" approach, where a
model can invent a plausible citation key. Here, two deterministic gates run
*before and around* the LLM:

- **Gate A — invented.** A `[[REF: KEY]]` whose `KEY` is not in the form's closed
  vocabulary is recorded as `invented` with no model in the loop. Even if the
  drafting model ignores the prompt and makes up a key, it cannot resolve to
  authority text, so it can never be scored `pass`.
- **Gate B — unresolved.** An in-vocabulary statute whose live text cannot be
  fetched is `unresolved` — visibly distinct from "resolved but unsupported".

The inspector additionally **grounds every quote**: a verdict whose `quote` is not
actually present in the authority text is flagged and, if it claimed `pass`, is
downgraded to `unclear` — a fabricated supporting quote is exactly the failure
mode we are inspecting for.

### A deterministic safety net for citations written outside the protocol

Placeholders only guarantee correctness for cites the model chose to wrap. A model
can still slip a bare `see 18-C §3-203` or `In re Estate of Kruzynski` into prose.
So `inspect_field` also runs `tools/citation_scan.py` — a deterministic,
**offline, no-LLM, no-network** scanner (regex families over the closed Maine
index; no trained model, because the surface forms are regular and the vocabulary
is finite). It buckets every citation-shaped span it finds:

- `leaked` — citation-shaped, but **outside** any `[[REF:]]` placeholder (only
  meaningful when the text uses the protocol);
- `unresolvable` — does not resolve to the trusted index (a fabricated or
  mistyped cite);
- `out_of_vocab` — a real cite, but not one of *this form's* citations.

Because the scanner needs no LLM, these findings are reported even when the
inspector LLM is unconfigured or down (the inspect call **fails soft** — it keeps
all deterministic findings and sets `ok=False`).

It is also hardened against the obvious ways a cite could dodge the regexes:
input is normalized first, so a **homoglyph hyphen** (`18‑C §9‑999`) or a
**zero-width space** inside a section number can't hide it; the **spelled-out
reverse** order (`Section 9-999 of Title 18-C`) and **plural `§§` lists**
(`§§ 5-301 and 9-999` — every section, not just the first) are recognized; and the
**guard** (`tools/guard.py`) blocks an `out_of_vocab` cite even when it is wrapped
in a placeholder, matching the inspector's Gate A.

```bash
echo "Under [[REF: 18-C §3-401]] the court acts; see also 18-C §3-203 and 18-C §9-999." \
  | python3 tools/citation_scan.py --form DE-101
#   [statute] '18-C §3-203' -> 18-C §3-203  <- LEAKED
#   [statute] '18-C §9-999' -> 18-C §9-999  <- UNRESOLVABLE, LEAKED
```

### Dead-link detection

A fabricated or stale citation often points to a URL that 404s, so "is the
authority link live?" is a verification signal on top of "does the cite resolve?"
The principle is **DEAD ≠ BLOCKED**: legislature.maine.gov returns **403** to
non-browser User-Agents and many servers reject `HEAD` with **405** — neither
means the page is gone. `tools/check_links.py` classifies `404/410/NXDOMAIN` as
**dead**, and `403/405/429/timeout` as **blocked/inconclusive**; only *dead* fails
a build. It runs on three surfaces:

1. **Citation-DB audit** — `make links` probes the authority URLs in
   `docs/statute-reference/_index/` (scope `used` = the cites the forms actually
   reference; `all` = the full index) and writes `catalog/link_health.json`.
   `make links-check` exits non-zero only on a *dead* link, so it is CI-safe
   behind a bot filter.
2. **Inspection-time signal** — the resolver reuses the live statute fetch it
   already does; a `404/410/NXDOMAIN` becomes a distinct `dead_links` bucket in
   the inspector result (a `[[DEAD LINK: cite]]` marker in the substituted text),
   while a `403`/timeout stays `unresolved` rather than being falsely called dead.
3. **Fabricated-URL scan** — `citation_scan` finds URL strings in free text and,
   fully offline, flags `placeholder` hosts (example.com…) and `fabricated`
   statute URLs (a `legislature.maine.gov` link whose section is not in the
   index — certain, no network). `--check-links` additionally probes `unknown`
   URLs for liveness.

```bash
echo "see https://legislature.maine.gov/statutes/18-C/title18-Csec99-999.html and https://example.com/x" \
  | python3 tools/citation_scan.py --json   # both -> fabricated_urls (offline)
python3 tools/check_links.py --scope used --check    # exit nonzero only on a DEAD link
```

## Usage

```bash
# 1. Get the drafting prompt + the form's allowed citations (deterministic).
python3 tools/inspect_citations.py --emit-prompt --form DE-101

# 2. Inspect a composed draft containing [[REF: cite]] placeholders.
python3 tools/inspect_citations.py --form DE-101 --draft draft.txt --json
echo "...[[REF: 18-C §3-401]]..." | python3 tools/inspect_citations.py --form DE-101

# Offline: skip the live fetch and inspect against section titles + relevance notes.
python3 tools/inspect_citations.py --form DE-101 --draft draft.txt --no-fetch-text
```

Exit status is non-zero whenever something needs a human's eyes: a `fail`,
`invented`, or `unresolved` citation, or an inspector LLM that could not complete.

Over MCP (`tools/agent_server.py`), the opt-in `inspect_citations(form_id,
field_texts)` tool does the same over a single string or a `{field_id: text}`
object.

## Configuration

The inspector LLM uses the same pluggable, OpenAI-compatible pattern as
`tools/route_form.py`:

| env var | default |
|---|---|
| `INSPECTOR_BASE_URL` | falls back to `ROUTER_BASE_URL`, then `http://127.0.0.1:8088/v1` |
| `INSPECTOR_MODEL` | falls back to `ROUTER_MODEL`, then `Qwen3.6-27B-FP8` |
| `INSPECTOR_API_KEY` | falls back to `ROUTER_API_KEY`, then `x` |
| `MPF_STATUTE_CACHE` | `/tmp/probate_statute_cache` |

This is **opt-in and non-deterministic** — it is never called from `fill_plan`,
`fill_pdf`, or `build_plan`, and the fill output is byte-identical whether or not
an inspector is configured.

## How the pieces fit

| file | role |
|---|---|
| `tools/legal_inspector.py` | generic, corpus-agnostic engine: placeholders, the two gates, the inspector LLM call, quote-grounding |
| `tools/maine_citation_db.py` | Maine adapter: builds the closed vocabulary from `docs/statute-reference/_index/` + a form's `statutes.json`, and resolves each cite to authority text |
| `tools/citation_scan.py` | deterministic safety-net scanner (no LLM, no network): flags bare cites written outside the `[[REF:]]` protocol, unresolvable cites, out-of-vocab cites, and fabricated URLs |
| `tools/check_links.py` | dead-link checker (stdlib only): classifies URLs live/dead/blocked, audits the citation DB (`make links`), and powers the inspector's `dead_links` and the scanner's fabricated-URL check |
| `tools/attest.py` | signed, hash-chained attestation receipts — proof the inspector ran on a given output (see `docs/attestation-and-guards.md`) |
| `tools/guard.py` + `hooks/citation_guard.py` + `tools/inspect_proxy.py` | harness injection: shared guard core, a Claude Code blocking hook, and an OpenAI-compatible guard proxy |
| `tools/fetch_statute_text.py` | live statute-text fetch + cache + SHA manifest (pins the **normalized extracted text**, not raw HTML) |
| `tools/build_statute_text_manifest.py` | maintainer tool to pin the cites the forms use into `catalog/statute_text_manifest.json` |
| `tools/inspect_citations.py` | the CLI |

### Notes on the statute-text fetch

- legislature.maine.gov returns **HTTP 403** to non-browser User-Agents; the
  fetcher sends a browser-like UA.
- Statute HTML is **not byte-stable** (nav/footer/analytics churn independently of
  the law), so the manifest pins the SHA-256 of the *normalized extracted text*,
  not the raw bytes. The fragile extraction lives in one function
  (`_extract_statute_text`) with a frozen-fixture test
  (`tests/fixtures/sec3-108.html`). Bump `EXTRACTOR_VERSION` after changing it and
  re-run the manifest builder.
- `text_verified` in a fetch result is `True` (matches the pin), `False` (the
  section was re-issued), or `None` (the cite is not pinned yet).
