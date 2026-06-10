# router/

**Evaluation harness only — the runtime router is `tools/route_form.py`** (or,
for agents, reading `catalog/router_catalog.json` directly; see
`docs/agent-workflow.md` step 1).

This directory holds the synthetic-case benchmark used to measure routing
quality while the catalog and disambiguation notes were tuned: case generators
(`generate_case.py`, `case_chain.py`, `case_to_narrative.py`), seed/synthetic
case sets (`seed_cases.yaml`, `synthetic_cases.jsonl`), batch runners
(`run_synthetic_batch.py`, `run_chain_batch.py`), and their reports
(`*_report.tsv`). See `docs/router-eval.md` for the methodology and results.

Nothing here runs at fill time, and nothing in `tools/` imports from it. The
batch runners expect an OpenAI-compatible LLM endpoint (`ROUTER_*` env vars).
