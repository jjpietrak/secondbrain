# v0.2 Deployment Plan — clean `v02-main` for team shipping

Ship an installable v0.2 to the team: **wiki + research + backend** agents, **NO web scraping**, no
hardcoded paths, no secrets, no dev-agent memory. The user keeps developing on
`claude/v2-prototype`; anything built during the deployment phase is dual-merged into BOTH branches.
This plan is the release checklist; execution happens on the user's GO (backend fans out the surgery).

## 1. Branch strategy
- Cut **`v02-main`** from `claude/v2-prototype` (current frozen HEAD), then clean it.
- `v02-main` = shipped release; `claude/v2-prototype` = ongoing dev.
- **Dual-merge rule:** a change meant to ship is applied to BOTH branches (cherry-pick the dev SHA
  onto `v02-main`, keeping `v02-main` free of dev memory/paths). A `RELEASE.md` checklist keeps them
  in sync.
- (Optional, user's call) promote `v02-main` to the repo default / tag `v0.2.0` for the team.

## 2. What SHIPS in v02-main
- **Agents:** `backend`, `wiki`, `research`. (Remove `web.md`.)
- **Skills:** `wiki-init/ingest/save/defuddle/cite/retrieve/query/reconcile/synth/lint`,
  `wiki-health`, `wiki-stats`, `think/challenge/connect`, `obj-query/synth/reconcile`,
  `deep-synthesis`, `question-promote/solve`, `wiki-gaps`, `vault-health`, `vault-push`,
  `cost-report`. (Remove: `web-scrape`, `web-rank`, `wiki-approve`, `agent-learn`.)
- **Core scripts:** `ingest_index`, `vault_config`, `cost_tracker`, `wiki_index`, `wiki_init`,
  `wiki_cite_check`, `wiki_gaps_{gather,fill,split}`, `objectives`, `obj_*`, `research_synthesis`,
  `pdf_extract`, retrieval pipeline (`bm25-index/retrieve/rerank/contextual-prefix/setup-retrieve`),
  `claude_agent.sh`, `wiki-lock.sh`, `vault_lease.sh`, `prompts/pipeline_prompts.py`. (Remove
  `web_*.py`, `agent_learn.py`, `report_approve.py` + their tests.)
- **skills/references/** (ai-first-rules, write-rules, vault-schema) — the conventions skills obey.
- **docs/** (path-cleaned): `requirements`, `agents`, `vault-schema`, `skills-description` (web rows
  pruned). Remove `web-agent-workflow.md`.
- **config/**: `secondbrain.yaml` (templatized) + a **template vault config** `config/vaults/_example/`.
  (Remove `config/vaults/Inference-Disagg/`.)
- **Tests:** the hermetic suite **minus web** (decision §6 Q4).
- **README** (new quick-install) + setup flow + clean **.env.example**.

## 3. What's STRIPPED
- **Web crawl pipeline:** `scripts/web_{crawl,decision,harvest,query,rank}.py`, `agent_learn.py`,
  `report_approve.py`, `.claude/web/`, `agents/web.md`, skills `{web-scrape,web-rank,wiki-approve,
  agent-learn}`, tests `test_web_*`, `test_agent_learn`, `test_web_query`, `test_report_approve`.
  `ingest_index` STAYS (wiki ingest lifecycle); its `waiting_approval/enqueue/reject` verbs go
  dormant (no web producer) — harmless, kept for the source lifecycle.
- **All web-fetching tools** (pending Q decision): OSB commands `obsidian-research/-deep`,
  `scripts/research/research*.py` + `scripts/research/lib/sources/*`, media tools (youtube/x/nlm).
- **Agent memory facts** (backend especially) → ship empty memory dirs + a skeleton `MEMORY.md` per
  shipped agent (the memory protocol still works; zero dev history).
- **plans/** (dev artifacts, incl. this file — it lives on v2-prototype, not in the release).
- **config/vaults/Inference-Disagg/** (the user's vault config) — replaced by the template.
- **ursa/ + .ursa_reference/** (vendored langchain project, not the product; breaks `pytest`).
- **services/litellm/** (the paid-proxy infra) — pending Q decision; the $0 wiki/research flow
  doesn't need it.

## 4. Cleanup tasks
1. **Paths (the big one).** `/home/jpietrak/second_brain` → resolved repo root (scripts already use
   `Path(__file__)`; skills/docs use a `$SECOND_BRAIN_HOME` env or rely on CWD + relative
   `python -m ...`). `/mnt/c/Obsidian/Inference-Disagg` → `$VAULT_ROOT` via `vault_config` (already
   the resolver). Audit every file from the survey (60 files); target = 0 machine-specific paths.
2. **Secrets.** Confirm `.env` untracked (gitignored ✅); scrub `.env.example` to placeholders only;
   confirm no real keys anywhere (the survey's key-hits were env-var NAMES + ursa, both gone).
3. **Memory.** Strip all facts; write a clean skeleton `MEMORY.md` for backend/wiki/research.
4. **Web cut + untangle** ingest_index/docs so the kept components don't import removed modules
   (verify deep-synthesis/obj-* don't import `web_*`/`lib/sources`; if research/lib is cut, confirm
   nothing kept imports it).
5. **Drop ursa/, .ursa_reference/, plans/, Inference-Disagg vault config.**

## 5. Install / setup flow (README)
**Prereqs:** Python 3.12 + `uv`, Claude Code CLI (authenticated), optional `ollama` + `nomic-embed-text`
for local retrieval rerank.
**Install:**
1. `git clone <repo> && cd second_brain`
2. `uv sync` (deps from `pyproject.toml`/`uv.lock`)
3. `cp .env.example .env` (optional keys; all core wiki/research is $0/local + Claude subscription)
4. **`setup`** (new — a `scripts/setup.sh` and/or `/setup` skill): prompt for a vault path + name →
   scaffold the vault skeleton via `wiki-init` (folders + `_template.md` per type + index/hot) →
   write `config/vaults/<name>/vault.yaml` (PURPOSE + path) → set the active vault.
5. Restart Claude Code (registers `.claude/skills` + agents).
6. **Quick-start:** drop a PDF in `raw/papers/` → `/wiki-ingest` → `/wiki-query "..."`; seed
   `objective/` (purpose/topics/questions) → `/obj-synth`, `/deep-synthesis`.

## 6. Verify the clean branch
- `pytest tests/` green on `v02-main` (minus web).
- **Fresh-clone smoke test:** clone `v02-main` to a tmp dir on a path with NO "jpietrak", run
  `uv sync` + `setup` against a tmp vault, `wiki-init` + a sample `wiki-ingest` → works with zero
  reference to the dev machine.
- **Path audit:** `grep -rE 'jpietrak|Inference-Disagg|/mnt/c/Obsidian'` over `v02-main` → 0 (or only
  inside an intentional example).

## 7. Open decisions (grill — §below)
A) Backend agent memory on ship. B) The "no-web" boundary (crawl-only vs all web-fetching).
C) Vault for the team (scaffold-empty vs ship example). D) Ship the hermetic tests / LiteLLM service.
