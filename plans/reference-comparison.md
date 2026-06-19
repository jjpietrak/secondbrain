# Reference Repo Comparison: claude-obsidian vs obsidian-second-brain vs our v0.2 plan

Read-only research report by the `backend` agent. Studies the two local reference repos and
maps them against the frozen `docs/` and the v0.2 plan. ASCII only.

- claude-obsidian (CO): `/home/jpietrak/second_brain/.co_reference` (AgriciDaniel/claude-obsidian, v1.9.2)
- obsidian-second-brain (OSB): `/home/jpietrak/second_brain/.osb_reference` (eugeniughelbur/obsidian-second-brain)

Bottom line up front: CO is the better-defined engineering substrate and is the chosen backend
implementation model. We copy CO's directory layout, SKILL.md format, retrieval pipeline,
hot-cache, hooks, and CLAUDE.md structure; we lift a narrow set of OSB commands/scripts
(reconcile, synthesize, connect, challenge, research/research_deep, vault_health, vault_stats,
export, youtube/x-read/notebooklm); we build new the objective graph, RBAC multi-agent isolation,
token-window budgeting, web-agent feedback loop, newsletter/export/eval; we drop CO's marketing /
methodology-mode / canvas / DragonScale / multi-host sprawl.

---

## 1. Overview per repo

### claude-obsidian (CO)
A Claude Code plugin + Obsidian vault implementing Karpathy's "LLM Wiki" pattern. 15 skills as
`skills/<name>/SKILL.md` (kepano `name`+`description` frontmatter convention), 3 agent definitions
(`agents/*.md`), slash-command entry points (`commands/*.md`), 4 lifecycle hooks (`hooks/hooks.json`),
12 helper scripts, 9 hermetic test suites (~1240 assertions, `make test`), and 5 `bin/setup-*.sh`
provisioners. It is engineering-grade: atomic writes, fcntl locks, graceful degradation, exit-code
contracts, defensive guards with audit references in the code comments (e.g. "v1.7.2 closes audit M2").
The retrieval pipeline (BM25 + contextual-prefix + cosine rerank) is the crown jewel and is exactly
what our `wiki-retrieve` needs. Weaknesses for us: heavy PKM/personal-assistant + marketing surface
(canvas, methodology modes, DragonScale, multi-host, release-blog), single-vault, no agent RBAC, no
objective graph, no cost/token accounting.

### obsidian-second-brain (OSB)
A cross-CLI *skill* (not a plugin): one `commands/` source compiles to Claude Code / Codex / Gemini /
OpenCode via a build-time adapter pattern. 45 slash commands grouped by `category:` frontmatter
(vault 17, thinking 13, research 8, meta 6). Its strength is the **research toolkit** under
`scripts/research/` (`research.py`, `research_deep.py`, `notebooklm.py`, `youtube_extract.py`,
`x_read.py`, plus a key-less free-source `lib/` over ~10 public APIs: arXiv, HN, Reddit, Lobsters,
dev.to, OpenAlex, Semantic Scholar, CrossRef, DuckDuckGo, Wikipedia) and the thinking/reconcile
command set. It is the canonical home of the `ai-first-rules.md` / `write-rules.md` / `vault-schema.md`
references (which our `skills/references/` already mirror). Weaknesses: enormous command sprawl
(calendar, meetings, agenda, podcasts, ADRs, kanban, goals, daily/mood/energy journaling) that is
personal-productivity, not focused research; less hardened code; no retrieval pipeline; no locking;
no multi-agent model.

---

## 2. Architecture deep-dive: claude-obsidian (priority - we copy a lot)

### 2.1 Directory layout
```
.claude-plugin/{plugin,marketplace}.json   # plugin manifest + distribution (drop for us)
skills/<name>/SKILL.md                      # 15 skills; references/ + templates/ subfolders
agents/{verifier,wiki-ingest,wiki-lint}.md  # subagent definitions (name+description+tools frontmatter)
commands/*.md                               # slash-command entry points routing to skills
hooks/hooks.json                            # SessionStart / PostCompact / PostToolUse / Stop
scripts/                                    # 12 helpers (retrieval, locking, transport, mode, address)
bin/setup-*.sh                              # 5 provisioners (vault, retrieve, mode, multi-agent, dragonscale)
_templates/{concept,entity,source,question,comparison}.md
wiki/                                       # the vault itself: concepts/ entities/ sources/ synthesis(comparisons)/ questions/ meta/ + hot.md index.md log.md overview.md
.vault-meta/                                # runtime state: chunks/ bm25/ embed-cache.json transport.json mode.json locks/ address-counter.txt hook.log
tests/                                      # hermetic make test
```

### 2.2 Skill / command model
- Capabilities are `skills/<name>/SKILL.md` with kepano-style frontmatter: `name`, `description`
  (the `description` carries the trigger phrases inline, e.g. "Triggers on: ingest, ingest this url, ...").
  Older skills also carry optional `allowed-tools:` (space-separated) for Claude Code.
- A skill may ship `references/*.md` (deep specs) and `templates/`.
- `commands/*.md` are thin slash-command entry points that route into skills. `AGENTS.md` lists the
  skill->trigger-phrase table.
- This is exactly our v0.2 skills model (`skills/<name>/SKILL.md` owned by an agent). **Adopt verbatim.**

### 2.3 Agent / orchestration model
- Subagents are `agents/<name>.md` with `name` / `description` (+ `<example>` blocks) / `model:` /
  `maxTurns:` / `tools:` frontmatter, then a body of role + "When invoked" + procedure.
  `verifier.md` (read-only Read/Grep/Glob/Bash, sonnet, 25 turns) is dispatched pre-commit; this is a
  clean template for our agent definitions and a direct precedent for our `backend` audit role.
- **No cron / nightly orchestrator.** Orchestration is hook-driven + manual slash commands. There is
  no multi-agent RBAC, no shared budget, no nightly loop. We must build all of that (Phase 4).
- Hooks (`hooks/hooks.json`) - the load-bearing automation, copy the shapes:
  - **SessionStart** (`startup|resume`): `cat wiki/hot.md` to inject recent context; clear stale
    locks (`wiki-lock.sh clear-stale --max-age 3600`); a prompt telling Claude to silently read
    `hot.md`. This is the "hot cache" warm-start.
  - **PostCompact**: re-read `hot.md` because hook-injected context does not survive compaction.
  - **PostToolUse** (`Write|Edit`): auto-commit `wiki/ .raw/ .vault-meta/`, but **defers if any
    wiki-lock is held** (avoids torn commits mid-ingest). Honors `.vault-meta/auto-commit.disabled`.
  - **Stop**: if `wiki/` changed this session, emit a `WIKI_CHANGED:` message instructing Claude to
    refresh `hot.md` (Last Updated / Key Recent Facts / Recent Changes / Active Threads, <500 words,
    overwrite completely - "it is a cache, not a journal").
- For us, hooks are also the **RBAC enforcement point**: the plan calls for `PreToolUse` path/tool
  guards derived from the `docs/vault-schema.md` matrix. CO has no `PreToolUse` guard - we add it.

### 2.4 Retrieval (the crown jewel - copy whole)
Three-tier pipeline from Anthropic's Sept-2024 contextual-retrieval research, opt-in via
`bin/setup-retrieve.sh`, feature-gated (callers fall back to legacy `hot -> index -> drill` if absent).

- **Ingest side** (`scripts/contextual-prefix.py`, 505 lines): chunk each wiki page on paragraph
  boundaries (~500-token target, 200-char overlap); generate a 1-2 sentence contextual prefix per
  chunk; write `.vault-meta/chunks/<page-address>/chunk-NNN.json` with `raw_text`,
  `contextualized_text`, `body_hash`, `page_address`, `page_path`, `chunk_index`.
  Prefix tiers: (1) Anthropic API Haiku with prompt caching - **GATED behind `--allow-egress`**;
  (2) `claude` CLI subprocess; (3) synthetic (title + first paragraph, fully local, default).
- **Index** (`scripts/bm25-index.py`, 293 lines): pure-stdlib Okapi BM25 (k1=1.5, b=0.75) inverted
  index over `contextualized_text`; Unicode tokenizer; fcntl-locked atomic write to
  `.vault-meta/bm25/index.json`; subcommands `build` / `query --top N` / `stats`.
- **Rerank** (`scripts/rerank.py`, 312 lines): cosine over `nomic-embed-text` embeddings via local
  ollama (`127.0.0.1:11434`); per-chunk embedding cache keyed by `body_hash`; **localhost-only guard,
  off-localhost requires `--allow-remote-ollama`**; degrades to no-op rerank (BM25 order) if ollama or
  model absent. Documented future paths: cross-encoder BGE, Cohere/Voyage rerank APIs.
- **Orchestrator** (`scripts/retrieve.py`, 195 lines): `bm25 query top-20 -> rerank to top-5 ->
  dedupe by page-address -> return candidates with `absolute_path`. JSON to stdout. Exit 10 =
  "not provisioned" so callers fall back gracefully. Imports siblings as modules (no subprocess
  overhead), with try/except friendly diagnostics.
- Benchmark claim: +32pp top-1 accuracy, +41% error reduction vs v1.6 page-level baseline.
- **Privacy posture matches our budget reality:** default path is fully local ($0); the only
  off-machine egress (contextual-prefix API tier, remote ollama) is double-consent-gated. This aligns
  perfectly with our "$0 marginal, prefer free/local routes" discipline.

### 2.5 Memory / hot-cache model
- `wiki/hot.md` = recent-context cache (~500 words), read at session start, rewritten at session end
  via the Stop hook. `wiki/index.md` = master catalog. `wiki/log.md` = append-only op log (newest on
  top). Read order: hot -> index -> pages. This is the two-layer token-discipline pattern our
  `wiki/hot.md` + `wiki/index.md` + per-area `hot`/`index` (objective/) directly mirror.
- No per-agent memory (single implicit agent). Our per-agent `memory/<id>/` isolation is new.

### 2.6 Cost tracking
- **None.** CO documents a "cost ceiling" estimate (~$12/1000 docs for contextual prefix) but has no
  ledger, no token accounting, no budget gate. Our `agents/cost_tracker.py` + window budgeting is new.

### 2.7 Vault schema / templates
- `wiki/{concepts,entities,sources}` + `comparisons/` (= our `synthesis/`) + `questions/` (= a
  research-output analogue) + `meta/`. `_templates/{concept,entity,source,question,comparison}.md`.
- Frontmatter is light (no bi-temporal `timeline:`; that richer schema is OSB's). CO does carry stable
  page addresses (`c-NNNNNN`) via `allocate-address.sh` (DragonScale, opt-in) - relevant precedent for
  our `ingest_index` stable IDs but not required.

### 2.8 Concurrency / locking
- `scripts/wiki-lock.sh`: per-file advisory locks keyed on `sha1(vault-relative-path)`, 60s staleness
  auto-reap, cross-process release by design. Every wiki write is guarded by acquire/release; the
  PostToolUse hook defers commits while locks are held. Useful precedent for our nightly multi-agent
  writes (parallel research/web/wiki) though our RBAC path-partitioning reduces contention.

## 2b. Architecture deep-dive: obsidian-second-brain (the narrow lift)
- **Adapter pattern:** `commands/` is the single source; `scripts/build.sh` + `adapters/<cli>/adapter.sh`
  compile to `dist/<platform>/`. Irrelevant to us (Claude Code only) - **drop the adapter layer.**
- **Research toolkit** (`scripts/research/`): the real prize. `research.py` (dossier: summary / key
  facts with recency markers / timeline / players / contrarian / further reading / open questions),
  `research_deep.py` (vault-scan -> gap analysis -> 3-5 targeted queries web/X -> delta synthesis with
  "What's New / Confirmed / Contradictions / Synthesis / Recommended Updates / Open Questions" ->
  propagation payload). Both have a **paid mode (Perplexity/Grok) and a free key-less mode** that emits
  JSON for the calling Claude to synthesize - the free mode is exactly our $0 route. `lib/sources/*`
  gives us ready-made free-source clients.
- **Health/stats:** `vault_health.py` (orphans, dead links, dup notes, missing frontmatter,
  code-fence-wrapped frontmatter, stale tasks, unfilled templates; `--json` for Claude) and
  `vault_stats.py` (aggregates by `type`/`status`, rewrites a marked block in `index.md`, no PyYAML
  dep). These are the cited sources for our backend-owned `wiki-health` / `wiki-stats`.
- **Hooks:** `validate-ai-first.sh` (PostToolUse: warns if a vault write lacks AI-first frontmatter /
  `## For future Claude` / has substitution-Unicode); `load_vault_context.py` (SessionStart context
  inject); `obsidian-bg-agent.sh` (PostCompact, opt-in, additive-only headless propagation). The
  validate-ai-first check-5 (em-dash / curly-quote / Unicode-math gate) is worth porting as a write
  guard for our `write-rules`.
- **Thinking/reconcile commands:** `obsidian-reconcile` (resolve contradictions), `obsidian-synthesize`
  (auto-find unnamed patterns), `obsidian-connect` (bridge two domains), `obsidian-challenge`
  (red-team an idea against vault history) - the cited sources for our `wiki-reconcile` / `wiki-synth`
  / `connect` / `challenge`. `obsidian-export` (flat JSON / md index / OKF snapshot) -> our `export`.
- **AI-first rule:** every write carries `## For future Claude` + rich frontmatter + wikilinks +
  recency markers + confidence levels; "search before create"; "propagate everything". This is the
  philosophy our `skills/references/{ai-first-rules,write-rules}.md` already encode.

---

## 3. Feature -> skill -> phase mapping (verified + enriched)

Verified against the actual repo files. The `docs/skills-description.md` source citations are
**all accurate** (every cited CO SKILL.md path and OSB command/script path exists and matches).

| Our skill | Owner | Source repo | Concrete source artifact (verified) | Phase | Notes / corrections |
|---|---|---|---|---|---|
| wiki-init | wiki | CO | `skills/wiki/SKILL.md` (+ `wiki-cli`, `references/`) | 1 | catalog cites `wiki-cli/SKILL.md`; the scaffold logic is in `skills/wiki/`. Both apply. |
| wiki-ingest | wiki | CO | `skills/wiki-ingest/SKILL.md` + `agents/wiki-ingest.md` | 1 | strong source: manifest delta-tracking, contradiction callouts, batch pass. Drop DragonScale address section. |
| wiki-save | wiki | CO | `skills/save/SKILL.md` (`commands/save.md`) | 1 | verified. |
| wiki-cite | wiki | NEW | (none - write new) | 1 | markdown-link citations; built on `write-rules.md`. Correct: no upstream. |
| wiki-retrieve | wiki | CO | `scripts/{retrieve,bm25-index,rerank,contextual-prefix}.py`, `skills/wiki-retrieve/SKILL.md` | 1 | crown jewel; copy whole. |
| wiki-query | wiki | CO | `skills/wiki-query/SKILL.md` + `retrieve.py` | 1 | reads hot+index+retrieve -> synthesize+cite. |
| wiki-reconcile | wiki | OSB | `commands/obsidian-reconcile.md` | 1 | verified. |
| wiki-synth | wiki | OSB | `commands/obsidian-synthesize.md` | 1 | verified. |
| wiki-lint | wiki | CO | `skills/wiki-lint/SKILL.md` + `agents/wiki-lint.md` | 1 | CO lint is skill-side; OSB `vault_health.py` is the script-side complement. |
| wiki-defuddle | wiki | CO | `skills/defuddle/SKILL.md` (`defuddle-cli`) | 1 | verified. |
| wiki-health | backend | OSB | `scripts/vault_health.py` (`commands/obsidian-health.md`) | 1 | verified; backend-owned per RBAC. |
| wiki-stats | backend | NEW wrapper over OSB | `scripts/vault_stats.py` | 1 | port script; new SKILL wrapper. |
| challenge | wiki | OSB | `commands/obsidian-challenge.md` | 1 | catalog said "both repos"; **correction: challenge is OSB-only** (CO has no challenge). |
| connect | wiki | OSB | `commands/obsidian-connect.md` | 1 | verified OSB. |
| think | wiki | CO | `skills/think/SKILL.md` (10-principle loop) | 1 | verified CO. So "challenge/connect/think from both repos" resolves to: think=CO, challenge+connect=OSB. |
| obj-query / obj-synth / obj-reconcile | research | NEW (pattern from CO/OSB) | model on `wiki-query`/`obsidian-synthesize`/`obsidian-reconcile` | 2 | new; operate on `objective/`, higher-effort model. |
| research [engine] [topic] | research | OSB | `scripts/research/research.py` (+ `lib/`) | 2 | verified; free mode is $0. |
| research-deep | research | OSB | `scripts/research/research_deep.py` | 2 | verified; vault-first gap-fill + delta synthesis + propagation payload. |
| autoresearch | research+code | CO | `skills/autoresearch/SKILL.md` + `references/program.md` | 2 | verified; Karpathy loop (3 rounds, web egress hygiene). |
| web-scrape | web | NEW | (none) | 3 | new; evaluate Apify/crawl4AI/Perplexity-SP. |
| web-rank | web | CO | `scripts/rerank.py` | 3 | verified; reuse the same rerank.py as wiki-retrieve. |
| agent-learn | all | NEW | (none) | 4 | new; OSB `obsidian-learn` is a loose analogue (prune vault learnings) but not agent-memory. |
| newsletter | research | NEW | (none) | 5 | new. |
| export [doc] | wiki | OSB | `commands/obsidian-export.md` (`scripts/export_okf.py`) | 5 | verified; OKF/json/md snapshot. |
| youtube / x-read / nlm | research | OSB / NEW | `scripts/research/{youtube_extract,x_read,notebooklm}.py` | 5 | verified. |

---

## 4. Over-reach to avoid (where each repo "does too much")

### From claude-obsidian - DROP / do not copy
- **Marketing + distribution surface:** `.claude-plugin/`, `marketplace.json`, README marketing,
  AI Marketing Hub Pro tiers, `/release-blog`, social previews, star-history, FUNDING, CITATION,
  blog/SEO machinery. None of it serves a focused research vault.
- **Methodology Modes (LYT/PARA/Zettelkasten/Generic)** + `wiki-mode.py` + `bin/setup-mode.sh`: our
  vault schema is fixed by `docs/vault-schema.md`. Modes add a routing indirection we do not want.
- **DragonScale Memory** (log folds `wiki-fold`, deterministic addresses `allocate-address.sh`,
  semantic tiling lint `tiling-check.py`, boundary-first autoresearch `boundary-score.py`): clever but
  scope creep. Stable IDs we already get from `ingest_index`; folds/tiling are unneeded.
- **Canvas / claude-canvas** (`skills/canvas/`, `commands/canvas.md`): visual layer, irrelevant.
- **Multi-host adapters / Codex/Cursor/Windsurf/Gemini support**, `obsidian-bases`, `obsidian-markdown`
  reference skills, MCP transport zoo (`detect-transport.sh`, REST API, mcpvault): we are Claude-Code
  + filesystem only. Keep filesystem transport; drop the rest.
- **Multi-writer locking (`wiki-lock.sh`)**: defer. Our RBAC path-partitioning means agents write
  disjoint folders; revisit only if nightly parallel writes to the same file appear.

### From obsidian-second-brain - DROP / do not copy
- **The entire personal-productivity command sprawl:** daily notes with mood/energy, `/obsidian-task`,
  kanban `/obsidian-board`, `/obsidian-project(s)`, `/obsidian-person`, `/obsidian-meeting`,
  `/obsidian-agenda`, `/obsidian-calendar`, `/obsidian-schedule`, `/obsidian-recurring`,
  `/obsidian-recap`, `/obsidian-review`, `/obsidian-world`, `/obsidian-log`, `/obsidian-capture`,
  `/obsidian-daily`, goals, ADRs, dev logs. This is a life-OS, not a research vault. These are the
  source of the schema "reference folders absent from v0.2 matrix" noise (open note #3 in vault-schema.md).
- **Calendar / Google-MCP commands** (Claude-Code-only, depend on Google Calendar MCP): drop.
- **Podcast** (`/podcast`, `podcast_extract.py`), `/x-pulse`, `/idea-discovery`, `/obsidian-emerge`,
  `/obsidian-graduate`, `/obsidian-decide`, `/obsidian-panel`, `/vault-deep-synthesis`,
  `/obsidian-visualize`, `/create-command`: defer or drop; not in our catalog.
- **The adapter/build/dist machinery** (`adapters/`, `scripts/build.sh`, `dist/`): single-platform; drop.

---

## 5. Adoption plan

### COPY from claude-obsidian (the backbone)
1. **Directory layout** -> our repo: `skills/<name>/SKILL.md` (+ `references/`, `templates/`),
   `.claude/agents/<id>.md`, `commands/*` (thin entry points, optional), `hooks/`, `scripts/`,
   `_templates/` (we already have `config/`, `agents/`, `logs/`). Keep `.vault-meta/`-style runtime
   state under our `meta/` + `.vault-meta/` for retrieval artifacts (chunks/bm25/embed-cache).
2. **SKILL.md format** verbatim: `name` + `description` (trigger phrases inline) + optional
   `allowed-tools`; `references/` deep specs. Maps to every skill in our catalog.
3. **Retrieval pipeline** copy whole into `scripts/`: `bm25-index.py`, `rerank.py`,
   `contextual-prefix.py`, `retrieve.py` + `bin/setup-retrieve.sh` -> drives `wiki-retrieve` (wiki, P1)
   and `web-rank` (web, P3, reuses `rerank.py`). Keep the `--allow-egress` / `--allow-remote-ollama`
   consent gates - they match our $0/free-route discipline. Default path is fully local.
4. **Hot-cache + hooks** (`hooks/hooks.json` shapes): SessionStart hot.md inject; PostCompact re-read;
   Stop -> refresh hot.md; PostToolUse auto-commit. Apply per-area (`wiki/hot.md`, `objective/hot`).
   **Extend with `PreToolUse` RBAC path/tool guards** derived from `docs/vault-schema.md` (new).
5. **Agent definition template** (`agents/verifier.md` shape: frontmatter `name/description/model/
   maxTurns/tools` + "When invoked" + procedure) for our `backend/wiki/research/web` agents. The
   read-only verifier maps onto the `backend` pre-commit/audit role.
6. **CLAUDE.md structure:** short, sectioned (What this is / Vault structure / How to use / Skills
   table / Concurrency / Hooks / cross-project access). Our root `CLAUDE.md` should follow this.
7. **wiki-ingest skill** (manifest delta-tracking by hash, contradiction callouts, batch cross-ref
   pass, context-window discipline) - port minus the DragonScale address section. Our `ingest_index`
   (schema v2->v3) replaces `.raw/.manifest.json`.
8. **autoresearch** loop + `references/program.md` config + web-egress-hygiene policy -> P2 `autoresearch`.
9. **Test discipline** (`tests/` hermetic, `make test`, exit-code contracts) - adopt the pattern for
   our scripts.

### TAKE from obsidian-second-brain (the narrow lift)
1. `scripts/research/research.py` + `research_deep.py` + the free key-less `lib/sources/*` aggregator
   -> `research` / `research-deep` skills (P2). The free mode (JSON -> Claude synthesizes) is our $0 route.
   Fold open `research_question` + `direction` reasoning into the query payload (objective-driven).
2. `scripts/vault_health.py` -> backend `wiki-health` (P1). `scripts/vault_stats.py` -> backend
   `wiki-stats` (P1).
3. Thinking/reconcile commands: `obsidian-reconcile` -> `wiki-reconcile`; `obsidian-synthesize` ->
   `wiki-synth`; `obsidian-connect` -> `connect`; `obsidian-challenge` -> `challenge` (P1).
4. `commands/obsidian-export.md` + `scripts/export_okf.py` -> `export` (P5).
5. `scripts/research/{youtube_extract,x_read,notebooklm}.py` -> `youtube` / `x-read` / `nlm` (P5).
6. `hooks/validate-ai-first.sh` check-5 (substitution-Unicode gate) -> our write-time guard for
   `write-rules.md` (ASCII enforcement).

### BUILD NEW (neither repo provides)
1. **Objective graph** (`objective/` typed nodes, User-vs-Agent write split, `agents/objectives.py`
   open-frontier query payload, `decision` -> agent-behavior compilation) - P2. No precedent in either repo.
2. **RBAC multi-agent isolation** (`backend/wiki/research/web` with per-agent `tools:` allowlist and
   `PreToolUse` path guards derived from the `docs/vault-schema.md` matrix; per-agent `memory/<id>/`).
   CO has subagents but no path RBAC; OSB has no agent roster. - P0-P4.
3. **Cost + token-window budgeting** (`agents/cost_tracker.py` window_tokens/window_used/
   window_pct_used over trailing 5h; `claude_agent.sh` capturing `--output-format json` usage;
   `config/secondbrain.yaml` + per-vault `budget.yaml`). Neither repo has any cost accounting. - P0/P4.
4. **Multi-agent nightly orchestrator** (`agents/nightly_run.sh` window-bounded loop:
   research+web enqueue waiting_approval -> wiki ingest/reconcile/answer -> backend audit -> nlm sync
   -> commit). CO is hook-only; OSB explicitly ships no cron. - P4.
5. **Web-agent feedback loop** (read accept/reject outcomes from `ingest_index` + `nightly_report`,
   propagate into improved search prompts / engine selection, persist heuristics in `memory/web/` via
   `agent-learn`). Closed retrieval-quality loop - novel. - P3/P4.
6. **Manual approval gate** (`waiting_approval` / `rejected` statuses in `ingest_index`, sticky reject,
   `nightly_report` approval surface). Neither repo gates web-discovered sources. - P1/P3.
7. **newsletter / eval harness** (grounding_eval, ab_eval with/without objective reasoning, $0 routes). - P5/P6.
8. **`wiki-cite`** (markdown-link NotebookLM-style citations coexisting with `[[wikilinks]]` graph
   edges - the hybrid link decision). - P1.

### DROP (over-reach for our purpose)
- CO: plugin/marketplace/marketing, methodology modes, DragonScale, canvas, multi-host adapters,
  obsidian-bases/markdown reference skills, MCP transport zoo, wiki-lock (defer).
- OSB: all personal-productivity commands (daily/task/board/project/person/meeting/calendar/agenda/
  schedule/recurring/recap/review/world/log/capture/goals/ADR/devlog), podcast, x-pulse,
  idea-discovery/emerge/graduate/decide/panel/visualize/create-command, adapter+build+dist machinery.

---

## 6. Conflicts and open questions vs our plan

1. **Vault schema folder names diverge from both references.** Neither repo has `objective/`,
   `research/`, our `meta/` split, or our `raw/<type>/` subfolders. Both use `wiki/sources` (matches),
   CO uses `wiki/comparisons` (= our `synthesis/`) and `wiki/questions` (no direct analogue). This is
   fine - our schema is authoritative - but the porting of CO/OSB skills must remap their hard-coded
   paths (`wiki/concepts`, `wiki/questions`, `Research/Deep/`, `wiki/sources`) onto our matrix.
2. **`wiki/concepts` vs `wiki/entities` swap (open note #1 in vault-schema.md).** Both reference repos
   use the conventional mapping (entities = concrete people/companies/tools; concepts = abstract
   ideas/frameworks). Our v0.2 matrix has them **reversed**. CO/OSB precedent supports the conventional
   mapping. **Flag for user** - do not silently change docs; but skill ports will need the resolved
   mapping. (Backend cannot edit docs.)
3. **`objetive/topic` typo (open note #2)** and reference-folder presence/absence (notes #3-5) remain
   user decisions; the reference repos do not resolve them.
4. **RBAC has no precedent to copy.** CO subagents share full tool access; we must author the
   `PreToolUse` path-guard hooks from scratch and verify they actually deny (P1/P2/P3 verification
   steps). The hooks.json shapes from CO are the starting point but the guard logic is new.
5. **Cost accounting blocker.** `claude_agent.sh` today `exec claude -p` and discards usage; neither
   reference repo records usage, so there is nothing to copy. P0 step 4 (capture `--output-format json`
   usage into `cost_tracker.record`) is a hard prerequisite for window budgeting and must be built first.
6. **research_deep propagation model conflicts with RBAC.** OSB `research_deep.py` emits a propagation
   payload telling Claude to run `/obsidian-save` and write across People/Projects/Ideas. In our model
   the `research` agent **cannot write `wiki/`**; the payload must instead enqueue/route to the `wiki`
   agent and `objective/` writes only. Port the gap-analysis/synthesis logic, **rewrite the
   propagation target** to respect RBAC.
7. **autoresearch ownership.** Catalog assigns it to `research+code`, but its writes land in `wiki/`
   (sources/concepts/entities). Under RBAC only `wiki` writes `wiki/`. Resolve: `research` runs the
   loop and discovers/enqueues; `wiki` performs the filing on approval. Confirm the split when building P2/P3.
8. **No nightly/cron in either repo.** Our entire Phase 4 orchestration + window budgeting is greenfield.

---

## Appendix: key file paths (reference, read-only)
CO retrieval: `.co_reference/scripts/{retrieve,bm25-index,rerank,contextual-prefix}.py`,
`.co_reference/skills/wiki-retrieve/SKILL.md`, `.co_reference/bin/setup-retrieve.sh`.
CO hooks/agents/CLAUDE: `.co_reference/hooks/hooks.json`, `.co_reference/agents/{verifier,wiki-ingest,wiki-lint}.md`,
`.co_reference/CLAUDE.md`, `.co_reference/AGENTS.md`, `.co_reference/skills/{wiki-ingest,autoresearch,save,think,wiki-query,wiki-lint,defuddle}/SKILL.md`.
OSB research/health: `.osb_reference/scripts/research/{research,research_deep,notebooklm,youtube_extract,x_read}.py`,
`.osb_reference/scripts/research/lib/sources/*`, `.osb_reference/scripts/{vault_health,vault_stats,export_okf}.py`,
`.osb_reference/commands/{obsidian-reconcile,obsidian-synthesize,obsidian-connect,obsidian-challenge,obsidian-export,obsidian-health}.md`,
`.osb_reference/hooks/{validate-ai-first.sh,load_vault_context.py}`, `.osb_reference/architecture.md`,
`.osb_reference/references/{ai-first-rules,write-rules,vault-schema}.md`.
