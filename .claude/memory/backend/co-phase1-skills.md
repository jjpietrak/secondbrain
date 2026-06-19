# Fact: CO Phase-1 skills + scripts - PORT/ADAPT/DROP per skill

Read 2026-06-19 from `.co_reference`. For each CO artifact: what it does, port decision.

## Skills (`.co_reference/skills/<name>/SKILL.md`)
- **wiki (scaffold)**: knowledge-architect SCAFFOLD op (folders, _templates, index/log/hot,
  CLAUDE.md, css snippets, git init). PORT scaffold logic into our `wiki-init`. DROP: methodology
  modes (`references/modes.md`), the "Community Footer" marketing block, canvas routing.
- **wiki-cli**: Obsidian-CLI transport wrapper (read/write/append/search/property:set/backlinks)
  with filesystem fallback; reads `.vault-meta/transport.json`. ADAPT->DROP transport zoo: we are
  filesystem-only (settings.json grants Write/Edit over /mnt/c/Obsidian). Keep ONLY the filesystem
  fallback idioms. Do NOT port the MCP/CLI detection.
- **wiki-ingest**: the strong source. Single/URL/image/batch ingest; delta-tracking by hash;
  contradiction `> [!contradiction]` callouts on BOTH pages; batch cross-ref pass; context-window
  discipline (read hot->index->3-5 pages; PATCH not rewrite); 10-principle mapping. PORT MINUS:
  (1) Transport section, (2) Mode-awareness `wiki-mode.py route`, (3) entire "Address Assignment /
  DragonScale Mechanism 2" section + `.raw/.manifest.json` (our `ingest_index.py` v3 replaces the
  manifest), (4) `.raw/` path -> our `raw/<type>/` subfolders, (5) `wiki/domains` (we have no domains
  folder). KEEP: read-completely, contradiction callouts, batch cross-ref, log/hot/index updates,
  immutable raw. ADAPT concurrency: `wiki-lock acquire` before each shared-target write (our P0 port).
- **wiki-query**: 3 modes (quick=hot+index ~1500 tok; standard=+3-5 pages ~3000; deep=full+web).
  Feature-detects `retrieve.py` + `.vault-meta/{chunks,bm25}` and calls
  `python3 scripts/retrieve.py "<q>" --top 5` BEFORE legacy hot->index->drill; exit 10 = fallback.
  Cites `(Source: [[Page]])`; offers to file answer as `wiki/questions/...`. PORT. ADAPT: file
  answers to `wiki/synthesis/` (we have no `wiki/questions/`); citations are `[[wikilinks]]` only.
- **save**: conversation->wiki note; note-type table (synthesis/concept/source/decision/session);
  Step-0 destination root; lock per file in sorted-path order; updates index/log/hot. PORT as
  `wiki-save`. ADAPT: drop transport + mode router + personal-vault `~/Documents` routing + the
  kanban/tasks/daily targets; map types to our 4 wiki subfolders (no decision/session folder ->
  synthesis or wiki/log).
- **wiki-lint**: orphans/dead-links/stale/missing-pages/missing-xref/frontmatter-gaps/empty-sections/
  stale-index; Dataview dashboard + canvas + naming conventions; writes
  `wiki/meta/lint-report-YYYY-MM-DD.md`. PORT skill-side checks. DROP: Address Validation
  (DragonScale), Semantic Tiling (`tiling-check.py`), canvas. This is the SKILL complement to
  backend `wiki-health` (OSB `vault_health.py` is the SCRIPT side). ADAPT report path: our schema has
  no `wiki/meta/`; write to `meta/health_report/`.
- **defuddle**: `npm i -g defuddle-cli`; `defuddle <url>` -> clean md to stdout; called by
  wiki-ingest on URL. PORT as `wiki-defuddle` ~verbatim (no RBAC conflict; tool-only).
- **think**: OBSERVE-OBSERVE-LISTEN-THINK-CONNECT-CONNECT-FEEL-ACCEPT-CREATE-GROW 10-principle loop;
  every CO skill carries a mapping table. PORT as a shared reference skill (research+wiki use it).
- **wiki-mode**: methodology-mode router. DROP entirely (our schema is fixed by docs/vault-schema.md).

## Scripts (`.co_reference/scripts/`)
- **bm25-index.py** (293 lines): pure-stdlib Okapi BM25 (k1=1.5,b=0.75) over `contextualized_text`;
  fcntl-locked atomic write to `.vault-meta/bm25/index.json`; build/query/stats. PORT verbatim.
- **retrieve.py** (195): orchestrator bm25 top-20 -> rerank top-5 -> dedupe -> JSON w/ absolute_path;
  exit 10 = not provisioned. PORT verbatim; drives `wiki-retrieve` (P1) + `web-rank` (P3).
- **rerank.py** (312): cosine over `nomic-embed-text` via local ollama 127.0.0.1:11434; cache by
  body_hash; localhost guard (off-host needs `--allow-remote-ollama`); no-op degrade if ollama absent.
  PORT verbatim. Matches $0/Ollama-bulk route.
- **contextual-prefix.py** (505): ingest-side chunker; prefix tiers (1) Haiku API `--allow-egress`
  GATED, (2) claude CLI, (3) synthetic local default. PORT verbatim; default tier-3 = $0/local.
- **wiki-lock.sh**: age-based noclobber lockfiles `.vault-meta/locks/<sha1(path)>.lock`,
  STALE_AFTER_SEC=60, acquire/release/list/clear-stale/peek, path-traversal/newline/symlink guards,
  `WIKI_LOCK_VAULT` override for tests, `with_meta_lock` flock wrapper. Exit 75=held. PORT verbatim
  (Layer-2 of P0). Acquire=cheap path is `set -o noclobber` atomic create.
- **setup-retrieve.sh** (`bin/`): provisioner -> chunks+bm25 index. PORT, adapt paths.
- DROP: allocate-address.sh, boundary-score.py, tiling-check.py, wiki-mode.py, detect-transport.sh,
  baseline/benchmark runners (DragonScale/modes/transport).

## Tests (PORT WITH the scripts, guideline #8)
- `test_wiki_lock.sh` (unit: acquire/release/list/clear-stale/peek/path-validation; mktemp sandbox via
  `WIKI_LOCK_VAULT`). `test_concurrent_write.sh` (10 workers acquire same file, append tagged line,
  release; asserts exactly N+seed lines, every tag once, no orphan locks, clear-stale=0, no garbled).
  Also `test_bm25_index.py`, `test_retrieve.py`, `test_contextual_prefix.py` ship with the pipeline.

## hooks.json (shapes to copy, NOT verbatim)
SessionStart (cat hot.md + `wiki-lock clear-stale --max-age 3600` + silent-read prompt); PostCompact
(re-read hot.md); PostToolUse Write|Edit (auto git add wiki/.raw/.vault-meta, DEFERS if any lock held,
honors `.vault-meta/auto-commit.disabled`); Stop (emit WIKI_CHANGED -> refresh hot.md). We ADD
`PreToolUse` path/tool RBAC guards (new, no CO precedent) and remap paths to our schema.

## agents/verifier.md (template for our backend audit role)
Read-only (Read/Grep/Glob/Bash, model sonnet, maxTurns 25), dispatched pre-commit; six-cut kernel +
4 mandatory checks (data egress consent gate / atomic writes / failure rollback / hermetic test
coverage) + git-hygiene + additive-without-pruning; 4-tier output BLOCKER/HIGH/MEDIUM/LOW + verdict.
ADOPT as the shape for backend's pre-commit verification + the agent-definition frontmatter template.
