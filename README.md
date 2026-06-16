# Second Brain

AI-maintained Obsidian knowledge base (Karpathy LLM-Wiki pattern). Agents ingest sources,
rewrite cross-referenced wiki pages, lint, reconcile contradictions, and sync — routed across
Claude, Ollama, and Gemini to keep marginal cost near zero.

- **Code:** `/home/jpietrak/second_brain` (WSL)
- **Vault:** `/mnt/c/Obsidian/Inference-Disagg` (shared between Windows Obsidian and WSL agents)
- **Agent spec:** [`CLAUDE.md`](CLAUDE.md) · **Vault rules:** `/mnt/c/Obsidian/Inference-Disagg/_CLAUDE.md`

---

## Architecture

```
Windows:  Obsidian (vault C:\Obsidian) + Local REST API (:27124) + Task Scheduler (nightly)
                              │  /mnt/c/Obsidian/Inference-Disagg  (shared path)
WSL2:     Claude Code (agent) ─ LiteLLM proxy (:4000) ─┬─ Ollama (:11434, local/free)
                                                       ├─ Gemini Flash (free tier)
                                                       └─ Anthropic Haiku (metered)
```

Three billing pools, cheapest-first:

| Tier | Work | Auth | Pool / cost |
|------|------|------|-------------|
| Interactive | your `claude` sessions | subscription login | subscription · $0 |
| Tier-2 automation | `claude -p` (ingest/lint/sync) via `scripts/claude_agent.sh` | `CLAUDE_CODE_OAUTH_TOKEN` | Agent SDK credit · $0 |
| Tier-3 routed | LiteLLM → Ollama / Gemini / Anthropic **Haiku** | `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | pay-as-you-go · ~$1–2/mo |

> **`ANTHROPIC_API_KEY` is used by LiteLLM ONLY.** Never export it into a `claude -p` shell
> (it would bill pay-as-you-go instead of drawing Agent SDK credit). `scripts/claude_agent.sh`
> enforces this by unsetting it and loading the OAuth token.

---

## Prerequisites

Most are already installed on this machine; listed for reproducibility / fresh setup.

**System (WSL2 Ubuntu, systemd enabled):**
```bash
sudo apt update && sudo apt install -y git curl jq xvfb chromium-browser unzip
# Node 20+ (nvm), Python 3.12, uv:
curl -fsSL https://astral.sh/uv/install.sh | sh
npm install -g @anthropic-ai/claude-code      # claude CLI
```

**Ollama** (runs on the Windows host, reachable from WSL at `localhost:11434`):
```bash
ollama pull llama3:8b      # bulk/local route (mistral optional, swap in config/litellm.yaml)
```

**Obsidian (Windows):** install Obsidian; open `C:\Obsidian` as the vault (see Manual setup);
install the **Local REST API** (coddingtonbear) and **Web Clipper** community plugins.

**API keys / tokens** (put in `.env` — see below):

| Var | Purpose | Required |
|-----|---------|----------|
| `CLAUDE_CODE_OAUTH_TOKEN` | Tier-2 automation (Agent SDK credit) — `claude setup-token` | Yes |
| `ANTHROPIC_API_KEY` | LiteLLM Anthropic route (pay-as-you-go) — Console key | Yes |
| `GEMINI_API_KEY` | LiteLLM validation route (free tier) | Recommended |
| `OBSIDIAN_API_KEY` | Local REST API plugin | For REST edits |
| `LITELLM_MASTER_KEY` | LiteLLM proxy auth (auto-generated) | Yes |
| `PERPLEXITY_API_KEY` / `XAI_API_KEY` / `YOUTUBE_API_KEY` | research/youtube backends | Optional |

---

## Install / bootstrap

The repo is already bootstrapped. From scratch:
```bash
cd /home/jpietrak/second_brain
cp .env.example .env            # then fill in keys/tokens
uv sync                         # installs litellm[proxy], pyyaml, requests, websockets
# Start the LiteLLM proxy as a systemd service:
sudo cp services/litellm/litellm.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now litellm
bash tests/test_routing.sh      # smoke-test the 3 backends
```

`.env` is **gitignored** and must never be committed. Only `.env.example` (no secrets) is tracked.

---

## Configuration

The framework is **multi-vault**: each vault is fully encapsulated. Exactly one vault is
active per run, selected by the `VAULT` env var (default in `config/secondbrain.yaml`).

```
config/
  secondbrain.yaml          # GLOBAL: default_vault, vault registry, shared-infra pointers
  litellm.yaml              # GLOBAL: the single LiteLLM proxy (serves every vault)
  vaults/
    <VAULT>/
      vault.yaml            # path, PURPOSE, methodology, REST url, max_turns, …
      topics.yaml           # research topics
      budget.yaml           # per-vault soft spend caps
```

- **Active vault / encapsulation:** `VAULT=<name>` picks `config/vaults/<name>/`. Resolve the
  active vault root anywhere with `python -m agents.vault_config path` (`name`/`purpose`/`env`
  also). Adding a vault = create `config/vaults/<name>/` + register it in `secondbrain.yaml`.
  Run `VAULT=all` in the nightly to process every enabled vault. Shared across all vaults: the
  LiteLLM proxy and the API keys in `.env`.
- **Vault PURPOSE:** each vault has one research subject binding all its pages/sources. The
  authoritative statement is the **"## Vault purpose"** heading in the vault's `_CLAUDE.md`;
  a one-line mirror lives in `config/vaults/<name>/vault.yaml` (`purpose:`). Agents load it as
  a high-priority relevance filter for every automated action (ingest/research/discovery).
- **Model routing — single switch file:** `config/litellm.yaml`. Each `model_name` is a stable
  **role** (`synthesis`/`bulk`/`validation`/`fallback`). To switch a provider/model, edit only
  that role's `model:` line, then `sudo systemctl restart litellm`.
- **Main interactive agent model:** `.claude/settings.json` (`"model"`); override per session
  with `claude --model`.
- **Budget:** per-vault soft cap in `config/vaults/<name>/budget.yaml` (`daily_usd_cap`); the
  shared proxy's `config/litellm.yaml` `max_budget` is a global ceiling. The real backstop is a
  hard spend limit on the Console key.
- **Topics:** `config/vaults/<name>/topics.yaml` (mirrored to that vault's `meta/topics.md`).

---

## Usage

```bash
cd /home/jpietrak/second_brain && claude     # interactive
```
| Command | Does |
|---------|------|
| `/obsidian-ingest <file\|url\|text>` | absorb a source → rewrites entities/concepts/synthesis |
| `/obsidian-query <q>` | smart vault search |
| `/obsidian-lint` | health report → `meta/health_report.md` |
| `/obsidian-reconcile` | find & resolve contradictions |
| `/obsidian-research[-deep]` | web/deep research (free key-less sources or Perplexity) |
| `/obsidian-sync` | commit + push the vault |
| `/cost-report` | spend vs budget → `meta/cost_report.md` |

Helpers: `agents/vault_health.py` (structural audit), `agents/cost_tracker.py {record,report,check}`,
`agents/nightly_run.sh` (Tier-2 nightly; `DRY_RUN=1` to rehearse).

---

## Manual setup steps (do these to go live)

1. **Make `C:\Obsidian` the vault.** Close Obsidian (currently open on "LLM Inference"), then
   *Open folder as vault → `C:\Obsidian`*. Delete the leftover `C:\Obsidian\LLM Inference\.obsidian`
   (the real config now lives at the vault root).
2. **Local REST API plugin.** Install + enable in Obsidian; copy its key to `.env` `OBSIDIAN_API_KEY`.
   Test: `source scripts/obsidian_api.sh && obsidian_ping`. (HTTPS/27124, self-signed → `-k`.)
3. **Anthropic Console — set a hard $5 spend limit** on the API key (console.anthropic.com).
   This is the only guaranteed total cap; LiteLLM's `max_budget` resets daily.
4. **Agent SDK credit.** Claim the credit opt-in in account settings; `claude setup-token`
   (token already in `.env`; re-run if it expires).
5. **Nightly automation (optional).**
   - Tier-1 (cloud research, laptop can be off): create 3 Routines at code.claude.com pointing
     at the vault repo, writing digests to `research/daily/<topic>/`.
   - Tier-2 (local): `bash scripts/setup_cron.sh` registers the 2 AM Windows Task Scheduler job.
     Rehearse first: `DRY_RUN=1 bash agents/nightly_run.sh`.
6. **NotebookLM (Phase 7, deferred).** Fragile browser automation — needs a dedicated Google
   account + interactive auth via the unofficial `notebooklm-mcp`. `/obsidian-notebooklm` is
   wired and waiting on that.

---

## Layout

```
config/   secondbrain.yaml (global+registry), litellm.yaml (routing), vaults/<name>/{vault,topics,budget}.yaml
.claude/  commands/ (16 slash commands), settings.json
agents/   vault_config.py (active-vault resolver), cost_tracker.py, vault_health.py, nightly_run.sh, __init__.py
services/litellm/  start.sh, litellm.service, cost_callback.py
scripts/  claude_agent.sh (OAuth wrapper), obsidian_api.sh, setup_cron.sh,
          research/ (vendored research toolkit incl. key-less sources), architect_scan.py
skills/references/  ai-first-rules.md, vault-schema.md, write-rules.md, ...
tests/    test_routing.sh, test_ingest.sh, test_nightly.sh
logs/     cost_ledger.jsonl, nightly-*.log   (gitignored)
```

## Status

Built & verified: 3-backend routing, cost ledger/report/budget gate, OAuth auth isolation,
vault scaffold + templates, 16 commands, `vault_health.py`, nightly dry-run. Deferred:
NotebookLM browser automation (Phase 7).
