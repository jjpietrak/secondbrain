# HOWTO — using the Second Brain agent

Day-to-day reference for working with the Claude Code agent in `/home/jpietrak/second_brain`.
For architecture, setup, and config see [`README.md`](README.md); for the agent contract see
[`CLAUDE.md`](CLAUDE.md).

---

## Quick start

```bash
cd /home/jpietrak/second_brain
claude                       # interactive session on the ACTIVE vault
```
At session start the agent resolves the active vault, reads its `_CLAUDE.md` (rules + PURPOSE),
and reads `wiki/hot.md` for working context. Then just type a `/command` or talk to it.

**Everything is filtered by the vault PURPOSE.** Each vault has one research subject (its
`_CLAUDE.md` → "## Vault purpose"). Ingest, research, and discovery prioritise on-purpose
material and flag off-purpose content rather than dropping it. A direct instruction from you
overrides the filter for that one action.

---

## Active vault (multi-vault)

One vault is active per run, chosen by the `VAULT` env var (default in `config/secondbrain.yaml`).

```bash
python -m agents.vault_config name        # active vault name        (e.g. Inference-Disagg)
python -m agents.vault_config path        # active vault root         ($VAULT_ROOT)
python -m agents.vault_config purpose      # the PURPOSE one-liner
python -m agents.vault_config notebook     # the vault's NotebookLM notebook id/alias
python -m agents.vault_config list         # all registered vaults

VAULT=OtherVault claude                    # run the agent against a different vault
```
Per-vault config lives in `config/vaults/<name>/{vault,topics,budget}.yaml`; rules + PURPOSE
live in `<vault>/_CLAUDE.md`. The LiteLLM proxy and API keys (`.env`) are shared across vaults.

---

## Commands (slash commands in an interactive `claude` session)

### Capture & ingest
| Command | What it does |
|---------|--------------|
| `/obsidian-ingest <file\|url\|text>` | Absorb a source — the vault rewrites itself: updates entities, rewrites stale claims, synthesises concepts, resolves contradictions. Raw saved to `raw/` (immutable). |
| `/obsidian-capture <idea>` | Zero-friction idea capture → `wiki/concepts/` + a mention in today's daily note. |
| `/obsidian-save` | Save everything worth keeping from the current conversation into the vault. |
| `/obsidian-daily` | Create/update today's daily note (calendar, overdue tasks, conversation context). |
| `/obsidian-task <task>` | Add a task to the right kanban board with inferred priority + due date. |

### Research
| Command | What it does |
|---------|--------------|
| `/obsidian-research <topic>` | Web research with citations — Perplexity Sonar if keyed, else free key-less sources (Wikipedia, HN, arXiv, Reddit…). Saves a dossier. |
| `/obsidian-research-deep <topic>` | Vault-first deep research: scans the vault, fills gaps, synthesises a delta, then propagates updates across the wiki. |
| `/obsidian-notebooklm <topic>` | Source-grounded synthesis via **Gemini File Search** (ephemeral, no real notebook, ~$0.01–0.05). |
| `/obsidian-youtube <url>` | Pull a video's transcript, metadata, top comments → summarised into the vault. |
| `/obsidian-architect <path>` | Scan a codebase → maintained architecture notes (overview, per-module, key decisions). Re-runnable. |

### NotebookLM (real notebook, via `nlm` CLI — $0)
| Command | What it does |
|---------|--------------|
| `/obsidian-notebooklm-sync [id\|alias]` | Bidirectional sync with the vault's real Google NotebookLM notebook. `push/` → notebook **sources**; notebook **notes** → `notes/`. Notebook resolved from `vault.yaml` if no arg. |

### Search & maintenance
| Command | What it does |
|---------|--------------|
| `/obsidian-query <q>` | Smart vault search — results with context, not just filenames. |
| `/obsidian-lint` | Structural health report (orphans, dead links, contradictions, stale pages) → `meta/health_report.md`. |
| `/obsidian-reconcile` | Find & resolve contradictions across the vault. |
| `/obsidian-sync` | `git add/commit/push` the vault repo. Run after substantive changes. |
| `/cost-report` | Today's spend per action/provider + remaining budget → `meta/cost_report.md`. |

---

## Automations (shell / Python, run from `/home/jpietrak/second_brain` in WSL)

### Nightly run — `agents/nightly_run.sh`
Tier-2 local maintenance (pull → ingest new `raw/` → lint → health → NotebookLM sync → commit/push),
all on Agent SDK credit ($0). Per-vault budget gate + 20h catch-up guard.
```bash
DRY_RUN=1 bash agents/nightly_run.sh          # rehearse the active vault (no writes)
bash agents/nightly_run.sh                     # run the active vault for real
VAULT=all bash agents/nightly_run.sh           # process every enabled vault
bash scripts/setup_cron.sh                     # register the 2 AM Windows Task Scheduler job
```

### NotebookLM sync — `scripts/research/notebooklm_sync.py`
Same engine as `/obsidian-notebooklm-sync`. Requires `nlm login` (cookies, ~20 min sessions).
```bash
uv run -m scripts.research.notebooklm_sync --notebook "$(python -m agents.vault_config notebook)"
#   --dry-run     preview actions, change nothing
#   --pull-only   only pull notebook notes down
#   --push-only   only push push/*.md up as sources
#   --prune       delete remote sources whose local push/ file was removed
#   --probe       dump raw nlm JSON shapes (debugging)
```
Folder: `<vault>/research/notebooklm/<notebook-slug>/` — `push/` (you → notebook),
`notes/` (notebook → you), `.nlm-sync.json` (manifest). Conflicts back up to `.conflicts/`.

### Cost & health — `agents/cost_tracker.py`, `agents/vault_health.py`
```bash
python agents/cost_tracker.py check            # exit 1 if today's paid spend ≥ daily cap
python agents/cost_tracker.py report           # write <vault>/meta/cost_report.md
python agents/vault_health.py                  # structural audit → <vault>/meta/health_report.md
```

### Research backends — `scripts/research/*.py`
Invoked by the research commands, but runnable directly:
```bash
uv run -m scripts.research.research "<topic>"          # web dossier (free or Perplexity)
uv run -m scripts.research.research_deep "<topic>"     # vault-first deep research
uv run -m scripts.research.notebooklm --topic "<t>"    # Gemini File Search grounded synthesis
```

### Unattended Claude — `scripts/claude_agent.sh`
Wrapper for `claude -p` automation: loads `CLAUDE_CODE_OAUTH_TOKEN` (Agent SDK credit, $0) and
unsets `ANTHROPIC_API_KEY` so calls never hit the metered API. Used by the nightly run.

### Obsidian REST API — `scripts/obsidian_api.sh`
Helpers for live edits while Obsidian is running (HTTPS :27124):
```bash
source scripts/obsidian_api.sh && obsidian_ping      # also: obsidian_list / _get_file / _put_file / _search
```

---

## Recipes

**Ingest a paper and grow the wiki**
```
/obsidian-ingest /mnt/c/Obsidian/Inference-Disagg/raw/papers/some-paper.pdf
/obsidian-lint            # check structural health afterward
/obsidian-sync            # commit + push
```

**Research a topic, vault-aware**
```
/obsidian-research-deep disaggregated prefill/decode KV-cache transfer
# scans the vault, fills gaps, synthesises a delta, propagates into the wiki
```

**Two-way NotebookLM**
```
# 1. drop on-purpose notes into <vault>/research/notebooklm/<slug>/push/
# 2. sync (pulls the notebook's notes down, pushes your push/ files up as sources):
/obsidian-notebooklm-sync
```

**Daily upkeep (or let the nightly do it)**
```
/obsidian-daily
/obsidian-lint
/cost-report
/obsidian-sync
```

**Add a new vault**
```
1. Create the vault folder in Obsidian with its own _CLAUDE.md ("## Vault purpose").
2. mkdir config/vaults/<Name>/ ; add vault.yaml (path, purpose) [+ topics.yaml, budget.yaml].
3. Register it under `vaults:` in config/secondbrain.yaml.
4. VAULT=<Name> claude         # work on it;  VAULT=all in the nightly includes it.
```

---

## Billing — route to the cheapest pool (details in [`README.md`](README.md))

- **Interactive subscription** ($0): your normal `claude` sessions.
- **Agent SDK credit** ($0): `claude -p` automation via `scripts/claude_agent.sh`.
- **Pay-as-you-go**: LiteLLM → Ollama (free) / Gemini (free tier) / Anthropic Haiku (metered).
  Guarded by the per-vault `daily_usd_cap` and a hard limit on the Console key.
- **NotebookLM (`nlm`)**: $0 — uses your Google account cookies, no API pool.

Never export `ANTHROPIC_API_KEY` into a `claude -p` shell (it would bill pay-as-you-go instead
of drawing Agent SDK credit). `scripts/claude_agent.sh` enforces this.
