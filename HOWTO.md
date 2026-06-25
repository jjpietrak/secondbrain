# HOWTO -- Second Brain day-to-day guide

Day-to-day reference for working with the Claude Code agents.
For architecture, install, and config see `README.md`; for the agent contract see `CLAUDE.md`.

---

## Quick start

```bash
# Launch an interactive session against the active vault
claude
# or, to target a specific vault:
VAULT=my-vault claude
```

At session start the agents resolve the active vault, load its PURPOSE, and read `wiki/hot.md`
for working context. Then type a skill command or talk to the agent directly.

**Everything is filtered by the vault PURPOSE.** Each vault has one research subject defined in
`config/vaults/<name>/vault.yaml` (`purpose:`). Ingest, research, and discovery prioritise
on-purpose material and flag off-purpose content rather than dropping it silently. A direct
instruction overrides the filter for that one action.

---

## Active vault (multi-vault)

One vault is active per session, chosen by the `VAULT` env var (default in
`config/secondbrain.yaml`).

```bash
python -m agents.vault_config name        # active vault name
python -m agents.vault_config path        # active vault root path
python -m agents.vault_config purpose      # the PURPOSE one-liner
python -m agents.vault_config list         # all registered vaults

VAULT=other-vault claude                   # run against a different vault
```

Per-vault config: `config/vaults/<name>/{vault,topics,budget}.yaml`.
Shared across vaults: the LiteLLM proxy config and API keys in `.env`.

---

## Skills (slash commands in a `claude` session)

### Capture and ingest

| Command | What it does |
|---------|--------------|
| `wiki-ingest <file>` | Absorb one source (PDF, Markdown, plain text). The vault rewrites itself: updates entities, rewrites stale claims, synthesises concepts, resolves contradictions. Raw file saved to `raw/` (immutable). |
| `wiki-ingest` (no arg) | Batch ingest: process every raw source not yet ingested. Deduped by stable source-id index -- safe to re-run. |
| `wiki-save` | Save everything worth keeping from the current conversation into the vault. |

### Research and synthesis

| Command | What it does |
|---------|--------------|
| `obj-synth` | Synthesise the objective graph: scan open questions, generate research directions, propose new questions from gaps. |
| `deep-synthesis <question>` | Deep research pass on a specific research question; writes `research/deep/<id>.md`. |
| `obj-query <q>` | Query the objective graph: find relevant questions, directions, decisions. |
| `obj-reconcile` | Find and resolve contradictions or redundancy in the objective graph. |
| `question-promote <QP-id>` | Promote a pending question proposal to a confirmed research question. |
| `question-solve <Q-id>` | Mark a research question as solved, linking to the answer. |
| `wiki-synth` | Synthesise across wiki pages on a topic; produces a synthesis page. |

### Search and query

| Command | What it does |
|---------|--------------|
| `wiki-query <q>` | Smart vault search: BM25 + contextual-prefix + Ollama cosine rerank (falls back to BM25 if Ollama unavailable). |
| `wiki-retrieve <q>` | Lower-level retrieval; returns a ranked list of candidate pages. |
| `wiki-gaps` | Identify knowledge gaps: pages with dead links or under-developed concepts; writes `wiki/gap/` files. |

### Maintenance and health

| Command | What it does |
|---------|--------------|
| `wiki-lint` | Structural lint: orphans, dead links, missing frontmatter, stale pages, stubs. |
| `wiki-health` | Full health report with scoring -> `meta/health_report/`. |
| `wiki-stats` | Page counts, type breakdown, status distribution. |
| `wiki-reconcile` | Find and resolve factual contradictions across the vault. |
| `wiki-init` | Scaffold a fresh vault or reconcile an existing vault to the v0.2 schema. |
| `wiki-cite` | Validate source citations: check AI-first rules compliance, citation quality. |
| `vault-health` | Backend-level audit: RBAC, cross-area link checks, orphan detection. |
| `vault-push` | Commit and push the vault repo (git). Run after substantive changes. |
| `cost-report` | Today's spend per action/provider + remaining budget -> `meta/cost_report.md`. |

### Reasoning skills

| Command | What it does |
|---------|--------------|
| `think <topic>` | Structured thinking pass: break down a complex question, enumerate assumptions. |
| `challenge <claim>` | Steelman + challenge: generate the strongest objection to a claim. |
| `connect <a> <b>` | Find non-obvious connections between two concepts or pages. |

---

## Automations (shell / Python)

### Nightly run -- `agents/nightly_run.sh`

Tier-2 local maintenance (pull -> ingest new `raw/` -> research due topics -> lint -> health ->
commit/push), all on Agent SDK credit ($0). Per-vault budget gate + 20h catch-up guard.

```bash
DRY_RUN=1 bash agents/nightly_run.sh          # rehearse (no writes)
bash agents/nightly_run.sh                     # run the active vault
VAULT=all bash agents/nightly_run.sh           # process every enabled vault
```

Topics due = `daily` every night + `weekly` on Mondays. Configure in
`config/vaults/<name>/topics.yaml`.

### PDF to Markdown -- `scripts/pdf_extract.py`

Used automatically by `wiki-ingest` for PDFs (PyMuPDF). Text PDFs only (no OCR).

```bash
uv run -m scripts.pdf_extract <file.pdf>                          # Markdown to stdout
uv run -m scripts.pdf_extract <file.pdf> --pages 0-15 --out out.md
```

### Ingest index -- `agents/ingest_index.py`

Per-vault registry of which sources have been ingested. Canonical store:
`<vault>/meta/ingest_index.json`; human-readable: `<vault>/meta/ingest_index.md`.

```bash
python -m agents.ingest_index scan              # register new + mark deleted
python -m agents.ingest_index status            # total / ingested / pending / deleted
python -m agents.ingest_index pending           # raw files not yet ingested
```

### Cost and health

```bash
python agents/cost_tracker.py check            # exit 1 if today's paid spend >= daily cap
python agents/cost_tracker.py report           # write <vault>/meta/cost_report.md
python agents/vault_health.py                  # structural audit -> meta/health_report/
```

### Unattended Claude -- `scripts/claude_agent.sh`

Wrapper for `claude -p` automation: loads `CLAUDE_CODE_OAUTH_TOKEN` (Agent SDK credit, $0) and
unsets `ANTHROPIC_API_KEY` so calls never hit the metered API. Used by the nightly run.

### Obsidian REST API -- `scripts/obsidian_api.sh`

Helpers for live edits while Obsidian is open (HTTPS, port 27124; requires the Local REST API
plugin):

```bash
source scripts/obsidian_api.sh && obsidian_ping
# also: obsidian_list, obsidian_get_file, obsidian_put_file, obsidian_search
```

### NotebookLM sync -- `scripts/research/notebooklm_sync.py`

Bidirectional sync with a real Google NotebookLM notebook via the `nlm` CLI ($0, uses Google
cookies). Set the notebook in `config/vaults/<name>/vault.yaml` (`notebooklm_notebook:`).

```bash
# Authenticate once:
nlm login
# Then sync:
uv run -m scripts.research.notebooklm_sync \
    --notebook "$(python -m agents.vault_config notebook)"
#   --dry-run      preview actions, no writes
#   --pull-only    only pull notebook notes down
#   --push-only    only push vault files up as sources
```

---

## Recipes

**Ingest a paper and grow the wiki:**
```
# Drop the PDF into <vault>/raw/papers/, then in Claude Code:
wiki-ingest
wiki-lint
vault-push
```

**Research a question, vault-aware:**
```
# First make sure your vault has an objective graph (run setup.sh + edit PURPOSE.md).
obj-synth
deep-synthesis Q-0001
```

**Batch process a folder of PDFs:**
```
wiki-ingest --all
wiki-stats
```

**Add a new vault:**
```bash
scripts/setup.sh --vault-path /path/to/new-vault --name new-vault \
    --purpose "One sentence purpose."
# Then restart Claude Code and: VAULT=new-vault claude
```

**Daily upkeep (or let the nightly do it):**
```
wiki-lint
cost-report
vault-push
```

---

## Billing

- **Interactive subscription ($0):** normal `claude` sessions.
- **Agent SDK credit ($0):** `claude -p` automation via `scripts/claude_agent.sh`.
- **Pay-as-you-go:** LiteLLM -> Ollama (free) / Gemini (free tier) / Anthropic Haiku
  (metered). Guarded by `daily_usd_cap` in `config/vaults/<name>/budget.yaml`.

Never export `ANTHROPIC_API_KEY` into a `claude -p` shell.
`scripts/claude_agent.sh` enforces this automatically.
