# Backend agent memory

Backend agent: maintains the Second Brain code (`agents/`, `scripts/`, `.claude/`,
`config/`, `skills/`) and owns cost/health/log audits (`meta/health_report/`,
`meta/cost_report/`). Architecture facts are below; build your own engineering memory
as you work by adding one-fact-per-file and linking it here.

## Architecture

- [retrieval-pipeline.md](retrieval-pipeline.md) -- hybrid retrieval design:
  contextual-prefix + BM25 + ollama cosine rerank. Default path is $0/local.
  Drives wiki-retrieve. Provision via `scripts/setup-retrieve.sh`.
- [locking.md](locking.md) -- two-layer write-safety model: Layer-2 per-file
  age-based lockfiles (`scripts/wiki-lock.sh`) for intra-host multi-writer
  safety; Layer-1 cross-host git lease (`scripts/vault_lease.sh`) for the
  nightly orchestrator. Humans never block.
- [rbac.md](rbac.md) -- per-agent RBAC: each agent has a write allowlist of
  vault path prefixes; `scripts/rbac_guard.py` PreToolUse backstop on
  Write/Edit-class tools; R4 sanction signal for research -> objective/research_question/.
- [cost-ledger.md](cost-ledger.md) -- cost model: `claude_agent.sh` captures usage
  on every headless call; `agents/cost_tracker.py` appends a JSONL row per call;
  per-agent attribution via `--agent <id>`; $0 credit-pool vs metered routing.

## Build log

_(append one-line entries here as features ship; link to a fact file for detail)_

## Decisions

_(append architecture decisions as they are made)_

## Audits

_(append health/cost audit findings here)_
