# Second Brain

AI-maintained Obsidian knowledge base (Karpathy LLM-Wiki pattern). Agents ingest sources,
rewrite cross-referenced wiki pages, lint, and sync — routed across Claude, Ollama, and
Gemini to keep cost near zero.

- **Code:** `/home/jpietrak/second_brain` (WSL) · **Vault:** `/mnt/c/Obsidian` (Windows + WSL)
- Full spec: see the implementation plan. Vault rules: `/mnt/c/Obsidian/_CLAUDE.md`. Agent
  spec: `CLAUDE.md`.

## Layout
```
config/   litellm.yaml (model routing — single switch file), budget.yaml, vault.yaml,
          topics.yaml, cost_callback.py
.claude/  commands/ (14 slash commands), settings.json
agents/   cost_tracker.py, vault_health.py, nightly_run.sh
services/litellm/  start.sh, litellm.service (systemd)
scripts/  claude_agent.sh (OAuth wrapper), obsidian_api.sh, setup_cron.sh
tests/    test_routing.sh
```

## Cost model (three billing pools)
| Tier | Work | Auth | Pool |
|------|------|------|------|
| Interactive | your `claude` sessions | subscription login | subscription ($0) |
| Tier-2 automation | `claude -p` (ingest/lint/sync) via `scripts/claude_agent.sh` | `CLAUDE_CODE_OAUTH_TOKEN` | Agent SDK credit ($0) |
| Tier-3 routed | LiteLLM → Ollama (free), Gemini Flash (free tier), Anthropic **Haiku** | `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | pay-as-you-go (~$1–2/mo) |

`ANTHROPIC_API_KEY` is used by **LiteLLM only** — never export it into a `claude -p` shell.
Daily cap: `config/litellm.yaml` `max_budget` + `config/budget.yaml` `daily_usd_cap`.

## Usage
```bash
cd /home/jpietrak/second_brain && claude          # interactive
/obsidian-ingest <file|url|text>   # absorb a source -> wiki pages
/obsidian-lint                     # health report -> meta/health_report.md
/obsidian-sync                     # commit + push the vault
/cost-report                       # spend vs budget -> meta/cost_report.md
```
Switch a routed model: edit the role's `model:` line in `config/litellm.yaml`, then
`sudo systemctl restart litellm`.

## Remaining manual setup
1. **Make `C:\Obsidian` the vault**: close Obsidian (currently open on "LLM Inference"),
   then *Open folder as vault → `C:\Obsidian`*. Delete the stray `C:\Obsidian\LLM Inference\.obsidian`.
2. **Local REST API plugin** (coddingtonbear): install + enable in Obsidian, put its key in
   `.env` `OBSIDIAN_API_KEY`. Test: `source scripts/obsidian_api.sh && obsidian_ping`.
3. **Anthropic Console**: set a hard **$5 spend limit** on the API key (the real budget backstop).
4. **Agent SDK credit**: claim the credit opt-in; `claude setup-token` (token already in `.env`).
5. **Nightly automation** (optional): create 3 cloud Routines at code.claude.com for research
   (Tier-1), then `bash scripts/setup_cron.sh` to register the local Tier-2 run.
   Test first: `DRY_RUN=1 bash agents/nightly_run.sh`.

## Status
Core built & verified: routing (3 backends), cost ledger/report/budget, OAuth auth isolation,
vault scaffold, commands, nightly dry-run. Deferred: NotebookLM browser automation (Phase 7).
