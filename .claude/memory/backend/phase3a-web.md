# Phase 3A (web search agent, free-tier) COMPLETE 2026-06-22

Commits (NOT pushed): `954dbc1` W1, `a548a75` W2, `b1948e3` W3. 625 tests green. Plan: `plans/phase-3-web.md`.

## Grill-me locked (2026-06-22)
1. Ranking = strict two-tier WITHIN a **typed source budget** in `.claude/web/web-config.json`:
   total 5, quotas {gap:3, research:1, news:1}, spillover gap>research>news. User picks 0-5 to ingest.
2. **3A free / 3B paid** split. 3A = $0 (RSS + free APIs + WebFetch + ollama rerank). 3B = Perplexity SP -> Apify/crawl4AI behind cost gate, deferred.
3. Registry = **seed/prior** (relevance boost), open WebSearch allowed at lower rank.
4. **Merge gap<->direction** into one target (origin=gap precedence; direction's queries/evidence as HOW).

## Built
- `.claude/agents/web.md` + `.claude/memory/web/` ; `rbac_guard.py` web role = Write only `meta/nightly_report/` (enqueue is a Bash call, unguarded). +test_rbac_guard_web.
- `.claude/web/` : `web-config.json` (quotas, user-editable) + `sources/*.json` (registry git-moved from `sources/`) + cache/ (gitignored) + README.
- `scripts/web_harvest.py` (Tier-0 free: reuse `research/lib/sources/*` + feedparser + GitHub) ; `scripts/web_decision.py` (parse gaps/dirs, one-to-one merge `MERGE_THRESHOLD=0.18`, lanes, route, select+budget+spillover, `plan` CLI) ; `scripts/web_rank.py` (rerank.py ollama primitives + deterministic fallback) ; `scripts/web_crawl.py` (orchestrator, `--vault [--dry-run]`).
- Skills `.claude/skills/{web-scrape,web-rank}/SKILL.md` (dispatch -> subagent_type: web; reconciled to the real `web_crawl.py` single-entry + `web_rank` stdin interface; `written_by` not generated_by).

## Key decisions / findings
- **Per-item identity bug fixed:** RSS candidates share the feed `source_id`, so enqueue/dedup collapsed distinct posts (5 selected -> 3 enqueued). Added `web_harvest.candidate_ident` (arXiv:/doi:/url); `select_candidates` + `web_crawl` enqueue key on it. `source_id` STAYS the feed/registry association (web_rank relevance bonus reads it).
- **Frozen-doc matrix grant (user-authorized):** `docs/vault-schema.md:44` meta/nightly_report write = Research **/ Web** Agent. (Docs are user-only; user chose this in the grill.)
- **CRAWL QUALITY:** live $0 demo passed end-to-end (real arXiv+RSS -> 5 distinct waiting_approval + digest; reject->sticky verified; demo artifacts then cleaned from the live vault). arXiv path on-target (SpectrumKV->DIR-0003). **RSS-blog path noisy under the deterministic fallback ranker** because ollama `nomic-embed-text` is not pulled. To improve: pull the embed model (web-rank then uses semantic cosine), and prefer query-driven paper sources for specific gaps, RSS for the news lane. The fallback is a floor.

## Restart + deferrals
- NEW skills `/web-scrape` `/web-rank` + the `web` agent need a **CLI restart** to register (same as every prior new-skill batch).
- Deferred: Phase 3B paid adapters; nightly_run.sh integration + `agent-learn` feedback persistence (Phase 4); newsletter GUI approval (Phase 5); the WebFetch/WebSearch augmentation of research-lane targets is described in the skill but executed by the agent, not the script.
- Built by the `backend` orchestrator fanning out parallel Sonnet/Haiku sub-agents per guideline #4; I (Opus) integrated + verified every test claim + ran the live demo.

## UPDATE — explainable DecisionTrace (user request, commits c939dce + 3c9b8a5)
User: "no black box — log the gap↔direction merges with scoring tables; export the full debug log on request." Added:
- `web_decision.DecisionTrace` + `render_trace_markdown` (scoring tables): threaded through parse/merge/lanes/route/select. `merge/scores` logs EVERY (gap,direction) pair (jaccard|topic_bonus|total|threshold|decision); `select` logs per-lane considered/selected/rejected with reasons (over-quota|intra-pool-dedup|ingest-dedup:<status>) + spillover.
- `web_crawl` threads one trace through the whole run, records **harvest queries** (engine|query|n_returned|error) + **rank** (path embedding/fallback + scores), embeds the full trace as a `## Decision trace` section in EVERY `nightly_report`, and exposes `--explain` (stderr) + `--trace-out PATH` (export). `web_decision.py plan --explain/--trace-out` shows the planning trace.
- The trace **immediately exposed a bug**: arXiv-category sources (cs.AR/dc/lg) all map to `engine=arxiv`, so identical queries fired ~3x/target. FIXED: `_harvest_target` dedups calls by (engine, query/feed/repo) — each unique call once (live: 3x→1x). Future enhancement (noted, not done): pass the arXiv category so cs.AR/dc/lg become meaningfully distinct calls.
- 677 tests. To tweak selection logic, edit `scripts/web_decision.py` (MERGE_THRESHOLD, lane quotas via `.claude/web/web-config.json`, route_to_sources, select_candidates) and inspect via `--explain`.
