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

## UPDATE 2026-06-23 — quality + columns + interactive report (commits e31d43a, 0c9bf85)
- **arXiv-category routing** (e31d43a): ArxivSource.search(category=) -> `cat:cs.AR`; web_crawl `_arxiv_category` derives cs.AR/dc/lg/eess.SP; dedup key now (engine,query,category) so the 3 arxiv-category sources make 3 MEANINGFUL per-category calls (trace shows `[cat:..]`).
- **ingest_index columns** (e31d43a): new `published` field; render_md + queue show Date Published / Date Ingested (ingested_at) / Rationale / Relevance (`_relevance_links`: DIR/Q/T/D/QP glob -> full-path wikilink, GAP -> [[wiki/gaps]]).
- **nomic-embed-text PULLED into the WSL ollama** (web_rank now path=embedding). GOTCHA: a bare `ollama pull` via the Bash tool hits the WINDOWS ollama; web_rank uses the WSL one — pull via `wsl.exe -- bash -lc 'ollama pull nomic-embed-text'`. Quality crawl flipped the ranking: on-topic arXiv papers (Cronus 0.71->GAP-02/DIR-0005, SARATHI 0.69, Q4-KV 0.68) instead of the fallback's blog noise (SpectrumKV was 0.28 last; now 0.65). Embedding cache in `<vault>/.vault-meta/embed-cache.json`.
- **Interactive approve/reject report** (0c9bf85): nightly_report now has Obsidian checkboxes per source (`- [ ] approve`/`- [ ] reject` + optional `- reason:` line) + columns (published/score/lane/relevance-links/content-rationale/url) + embedded `## Decision trace`. web_crawl wires `published=` + 1-2 sentence content rationale (`_content_rationale`, HTML-stripped snippet) into enqueue.
- **Read-back** (0c9bf85): `scripts/report_approve.py` (reusable; `parse_report` -> approve/reject/conflict, ignores everything from `## Decision trace` down; `apply` dry-run default, `--apply` flips ingest_index) + `wiki-approve` SKILL (dispatch subagent_type: wiki: preview -> apply -> fetch approved into raw/<type>/ -> wiki-ingest -> annotate). Both manual `/wiki-approve` AND nightly auto-read (parser reused; nightly wiring = P4). Live demo: approve(Cronus)+reject-with-reason(SARATHI) parsed correctly. 768 tests. **`/wiki-approve` also needs the CLI restart.**

## UPDATE 2026-06-23b — per-gap files + richer report + enforced URL + gap graph edges (87534cd, ab8a7c8; docs eb2cca5)
- **wiki/gaps.md -> wiki/gap/GAP-NN-<slug>.md per-file** (87534cd): `scripts/wiki_gaps_split.py` (analysis -> per-gap files + index.md; YAML safe_dump, idempotent); `web_decision.parse_gaps(gap_dir)` reads frontmatter (output shape unchanged -> merge/lane/route untouched); `_relevance_links` GAP -> `[[wiki/gap/GAP-NN-<slug>]]`; `wiki-gaps` writes via the splitter; `wiki/gap/_template.md`; vault-schema row. Live-migrated 10 gaps (gaps.md removed; all yaml.safe_load clean). Facts: [wiki-gaps-split.md], [gap-readers-migration.md].
- **Richer report** (ab8a7c8): per source a **Summary** para (<=200 words, abstract via `_content_summary`) + **Relevance** para (lane+GAP/DIR+expected_evidence+score+engine via `_relevance_paragraph`) + **retrieved via {engine}**/**source {source_id}** attrs; candidates tagged with target expected_evidence. LLM/WebFetch enrichment for thin snippets noted (not wired).
- **Enforced URL** (ab8a7c8): ingest_index `url` field + `_valid_url`/`has_working_url`; `enqueue(url=)`; web_crawl derives arXiv abs URL; `report_approve.apply` BLOCKS approve of a row without a working http(s) URL (`blocked[]`).
- **Gap graph edges** (ab8a7c8): each gap file renders a `## Shows up in` body of `[[wiki/...]]` links (real Obsidian edges to the dictating source pages) + frontmatter list kept. Re-split live.
- **Doc** `docs/web-agent-workflow.md` (Mermaid + per-stage code refs + tune table). 880 tests. NOTE: user deleted vault `objective/decision/D-0001-no-web-research.md` (flagged, not mine).
