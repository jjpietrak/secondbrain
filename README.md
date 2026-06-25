# Second Brain v0.2

An AI-maintained Obsidian knowledge base powered by three Claude Code agents: **wiki**
(ingest + query + synthesis), **research** (objective graph + deep synthesis), and **backend**
(audits + health + version control).

Each vault is a fully-encapsulated, PURPOSE-bound knowledge base. Agents ingest sources,
rewrite cross-referenced wiki pages, build an objective graph, synthesise research, audit
structure, and commit -- routed across Claude, Ollama, and Gemini to keep marginal cost near
zero.

> **Web scraping is NOT included in this release.** A web-harvest layer is under design review
> and will ship as an optional module in a future version.

---

## What it is

- **Wiki agent**: absorbs PDFs, articles, and notes; rewrites entities/concepts/synthesis pages;
  answers vault-aware queries; lints structure.
- **Research agent**: maintains an objective graph (purpose, topics, research questions,
  directions); synthesises findings; proposes new research questions from gaps.
- **Backend agent**: vault health reports, cost tracking, schema reconciliation.
- **Multi-vault**: each vault has its own config, purpose, and folder tree under
  `config/vaults/<name>/`. One vault is active per session.

---

## Prerequisites

**Required:**
- Python 3.12 and [uv](https://github.com/astral-sh/uv) (`curl -fsSL https://astral.sh/uv/install.sh | sh`)
- [Claude Code CLI](https://claude.ai/code), authenticated (`claude login`)
- An Obsidian vault folder (any empty directory works; Obsidian is optional for editing)

**Optional (local retrieval rerank, $0):**
- [Ollama](https://ollama.com) with `nomic-embed-text` model
  (`ollama pull nomic-embed-text`) -- enables cosine rerank in `wiki-retrieve` and
  `wiki-query`. Without it the retrieval falls back to BM25 only.

**Optional (local retrieval rerank):**
- Install [`ollama`](https://ollama.com) and run `ollama pull nomic-embed-text` to enable
  semantic cosine rerank in `wiki-retrieve`. Without it, retrieval falls back to BM25 (still $0).

---

## Install

```bash
# 1. Clone the repo
git clone <repo-url> second_brain
cd second_brain

# 2. Install Python dependencies
uv sync

# 3. Copy environment template and fill in your keys
cp .env.example .env
# Required: CLAUDE_CODE_OAUTH_TOKEN (run: claude setup-token)
# See .env.example for details on each optional key.

# 4. Create and scaffold your vault
scripts/setup.sh --vault-path /path/to/your/vault --name my-vault \
    --purpose "One sentence stating your research purpose."
# Prompts for any missing args.  Idempotent: safe to re-run.

# 5. Restart Claude Code to register skills and agents

# 6. (optional) run the test suite to verify the install
uv run pytest tests/
```

---

## Quick start

```bash
# Start an interactive session against your vault
VAULT=my-vault claude
# (or set default_vault in config/secondbrain.yaml and just run: claude)
```

**Ingest a source and query:**
```
# Drop a PDF into <vault>/raw/papers/ then:
/wiki-ingest
/wiki-query what does this paper say about X?
```

**Build your objective graph:**
```
# Edit <vault>/objective/purpose/PURPOSE.md with your research purpose, then:
/obj-synth
/deep-synthesis
```

**Maintenance:**
```
/wiki-lint         # structural check: orphans, dead links, missing frontmatter
/wiki-health       # full health report -> meta/health_report/
/wiki-stats        # page counts, type breakdown
/vault-push        # commit + push the vault repo (git)
/cost-report       # today's spend vs budget
```

---

## Agents and skills

| Agent | Skills (slash commands) |
|-------|------------------------|
| `wiki` | `wiki-ingest`, `wiki-query`, `wiki-save`, `wiki-lint`, `wiki-health`, `wiki-reconcile`, `wiki-stats`, `wiki-cite`, `wiki-retrieve`, `wiki-gaps`, `wiki-synth`, `wiki-init` |
| `research` | `obj-query`, `obj-synth`, `obj-reconcile`, `deep-synthesis`, `question-promote`, `question-solve` |
| `backend` | `vault-health`, `vault-push`, `wiki-stats`, `think`, `challenge`, `connect` |

Each skill has a `SKILL.md` under `.claude/skills/<skill-name>/` with full usage instructions.

---

## Multi-vault configuration

Active vault resolution precedence:
1. `VAULT=<name>` environment variable
2. `default_vault` in `config/secondbrain.yaml`

```
config/
  secondbrain.yaml              # global: default_vault, vault registry
  vaults/
    <name>/
      vault.yaml                # vault path, PURPOSE, research engine
      topics.yaml               # research topics for nightly sweeps
      budget.yaml               # per-vault daily spend cap
```

Resolve the active vault at any time:
```bash
python -m agents.vault_config path      # vault root path
python -m agents.vault_config name      # active vault name
python -m agents.vault_config purpose    # one-line PURPOSE
python -m agents.vault_config list      # all registered vaults
```

---

## Billing

Three billing pools, cheapest first:

| Route | Auth | Cost |
|-------|------|------|
| Interactive `claude` sessions | subscription login | $0 |
| `claude -p` automation via `scripts/claude_agent.sh` | `CLAUDE_CODE_OAUTH_TOKEN` | $0 (Agent SDK credit) |
| Local `ollama` (bulk summarisation, retrieval embeddings) | none | $0 (local) |
| Optional metered providers (only if a key is set in `.env`) | provider key in `.env` | pay-as-you-go |

The core wiki and research flow runs entirely on the $0 pools. Provider keys in `.env` are
optional and used only by metered routes; never export `ANTHROPIC_API_KEY` into a `claude -p`
shell (`scripts/claude_agent.sh` enforces this automatically).

---

## Repository layout

```
README.md  HOWTO.md  CLAUDE.md     # overview, day-to-day guide, agent spec
scripts/
  setup.sh                          # one-command team setup (run this first)
  claude_agent.sh                   # OAuth wrapper for `claude -p` automation
  wiki_init.py                      # vault scaffold + reconcile
  obj_init.py                       # objective/ graph scaffold
  pdf_extract.py                    # PDF -> Markdown (PyMuPDF)
  templates/                        # file templates used by scaffolders
agents/
  vault_config.py                   # active-vault resolver
  cost_tracker.py                   # spend ledger + budget gate
  vault_health.py                   # structural health audit
  nightly_run.sh                    # nightly automation orchestrator
config/
  secondbrain.yaml                  # global config + vault registry
  vaults/<name>/                    # per-vault config (vault/topics/budget.yaml)
.claude/
  skills/<name>/SKILL.md            # skill dispatch + procedure for each command
  agents/                           # agent definitions
tests/                              # pytest suite (1000+ tests)
```

---

## See also

- `HOWTO.md` -- day-to-day command reference, automation recipes, billing details.
- `CLAUDE.md` -- agent contract and RBAC boundaries.
- `docs/` -- frozen design documents (requirements, vault schema, agent spec, skills).
