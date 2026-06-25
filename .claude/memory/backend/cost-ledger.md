---
type: reference/architecture
---
# Fact: cost ledger and spending model

Related: [[rbac]], [[locking]], [[retrieval-pipeline]]

## Ledger mechanics

Every model call appends one JSON row to `logs/cost_ledger.jsonl` (shared file, rows
tagged by vault name). The row schema:

```
{ts, date, vault, action, role, provider, model,
 input_tokens, output_tokens, cost_usd, estimated_cost_usd, source}
```

Writers:
- **claude_agent.sh** (agent SDK credit-pool calls): wraps `claude -p --output-format json`,
  parses the JSON result, records a row with `source=agent-sdk-credit` and `cost_usd=0`.
  Re-emits only `.result` to stdout. Preserves claude's exit code. If parsing fails,
  records a zero-cost row with `role=parse_error` (no call is silently dropped).
  `--agent <id>` tags per-agent spend (strips the flag before forwarding to claude).
  Passthrough mode: if the caller already set `--output-format`, exec claude raw (no
  double-wrap, no cost capture).
- **LiteLLM proxy** (`services/litellm/cost_callback.py`): records metered (paid) calls
  with actual `cost_usd` from the provider response and `source=pay-as-you-go`.

The Python API is `agents.cost_tracker.record(action, role, provider, input_tokens,
output_tokens, cost_usd, source, model)`. The ledger file is in the code repo (`logs/`),
not the vault, so it is available even when the vault is absent.

## estimated_cost_usd

Every row carries `estimated_cost_usd` computed from a static rate table
(Haiku/Sonnet/Opus/Flash/Gemini/Ollama) even when `cost_usd=0`. This is informational
only -- it approximates the imputed value of credit-pool and free-route calls. Budget cap
enforcement always uses `cost_usd` (real paid dollars).

## Cost routes

- **$0 / local**: BM25 index, pdf extract, health/stats, ollama embeddings, vault ops.
  Source tag: `agent-sdk-credit` or `local`.
- **$0 / credit pool**: all `claude_agent.sh` headless calls go through the Agent SDK
  credit pool; `cost_usd=0` in the ledger.
- **Metered / paid**: LiteLLM proxy calls to Anthropic/Gemini models with actual billing.
  Source tag: `pay-as-you-go`. Budget cap: `config/vaults/<vault>/budget.yaml:daily_usd_cap`
  (default 2.0 USD/day). Check with `python agents/cost_tracker.py check` (exits 1 if over
  cap).

## Per-agent attribution

`claude_agent.sh --agent <id> ...` tags the ledger row `action=<id>`. This enables
per-agent spend breakdown in `python agents/cost_tracker.py report`, which writes
`meta/cost_report.md` in the active vault.

## Per-vault scoping

Each row carries a `vault` field set to the active vault name. `spent_today()`,
`check_budget()`, and `report()` filter to the active vault automatically. Legacy rows
without a vault tag are attributed to the active vault.
