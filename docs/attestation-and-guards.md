# Injecting the citation guard into a harness, and proving it ran

The citation inspector is only useful if (a) you can wire it into whatever harness
runs the model, and (b) you can *prove* it actually ran on a given output. This
page covers both. Not legal advice.

## Injecting into a harness

There is no universal switch — each harness exposes a different extension point —
but the verification is available in the forms that collectively cover them:

| Harness | Seam | Where |
|---|---|---|
| Claude Code, Cursor, Cline, Continue, Claude Desktop | **MCP tool** `inspect_citations` | `tools/agent_server.py` |
| Claude Code (automatic, blocking) | **Stop hook** | `hooks/citation_guard.py` |
| Codex, CI, any shell agent | **CLI + exit code** | `tools/inspect_citations.py`, `tools/citation_scan.py` |
| Codex, Hermes (vLLM/Ollama), LiteLLM — anything OpenAI-compatible | **guard proxy** | `tools/inspect_proxy.py` |

All of them share one core, `tools/guard.py:evaluate()`, which runs the
deterministic scanner (offline — no LLM, no network) and blocks on a leaked cite,
an unresolvable cite, or a fabricated URL (with `llm=True` it also folds in the
inspector's `fail`/`invented`/`dead_link`).

### Claude Code (blocking hook)

`.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {"hooks": [{"type": "command", "command": "python3 hooks/citation_guard.py"}]}
    ]
  }
}
```

The hook reads the turn's last assistant message, scans it, and emits
`{"decision":"block","reason":…}` to make Claude Code feed the problem back
instead of finishing. It **fails open** (any internal error allows the turn) and
attests every check to `$ATTEST_LOG`. Scope to a form with `$CITATION_GUARD_FORM`;
add `$CITATION_GUARD_LLM=1` to also run the inspector. Test without a transcript:

```bash
echo "see 18-C §9-999 and https://example.com/x" | python3 hooks/citation_guard.py --text -
```

### OpenAI-compatible proxy (Codex, Hermes, LiteLLM, …)

Point the harness's `base_url` at the proxy; it forwards upstream, inspects the
completion, annotates it under `x_citation_inspection`, and — with
`$PROXY_FAIL_CLOSED=1` — replaces a failing message with a refusal:

```bash
UPSTREAM_BASE_URL=https://api.example/v1 PROXY_FAIL_CLOSED=1 \
    python3 tools/inspect_proxy.py --port 8099
# harness base_url -> http://127.0.0.1:8099/v1   (header X-Citation-Form: DE-101 to scope)
```

Reference implementation: non-streaming (`stream:true` is forwarded unverified).

## Proving it was on

A guard you can't attest to is only as trustworthy as the operator's word.
`tools/attest.py` turns each inspection into independently-verifiable evidence,
decomposing "prove it was on" into four checkable claims:

1. **It ran** → a **signed receipt** (`{schema, tool, git_commit, config_digest,
   model, input_sha256, summary, findings, verdict_digest, needs_review,
   timestamp, nonce}`), HMAC-SHA256-signed with `$ATTEST_HMAC_KEY` — a key the
   **operator holds, not the agent**, so a model can't forge "it passed".
2. **On *this* output** → the receipt pins `input_sha256`; `verify --input` checks
   the binding, so a receipt can't be replayed onto a different draft.
3. **Nothing suppressed** → receipts **hash-chain** into an append-only
   `inspection_log.jsonl` (each entry pins the prior line's hash); deleting or
   reordering breaks the chain.
4. **Anyone can re-check** → the deterministic findings (invented / unresolved /
   dead_link / fabricated_url / leaked) are reproducible by re-running the
   scanner; the LLM verdict is covered by the signed `verdict_digest`.

```bash
export ATTEST_HMAC_KEY=operator-only-secret
python3 tools/inspect_citations.py --form DE-101 --draft draft.txt \
    --attest --log inspection_log.jsonl --json > out.json     # emits attestation
python3 tools/attest.py verify receipt.json --input draft.txt # binds to the input
python3 tools/attest.py verify-log inspection_log.jsonl       # chain + signatures
```

A tampered receipt (e.g. flipping `needs_review` to hide a finding) fails the
signature check; a forged or reordered log entry fails `verify-log`.

**The limit, stated plainly:** a receipt proves the guard *ran and what it found*
— it does **not** prove the agent *heeded* a failure. Only fail-closed enforcement
does that: the hook blocks the turn, or the proxy withholds the message. So the
audit story is *config (the gate is enforced) + artifact (a valid, chained
receipt for the exact output)*. Together they show the guard was on and acted.
