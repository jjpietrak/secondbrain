# Web Agent -- Memory index

One line per memory. Fact files live beside this index (one fact per file). Keep this
index current; never store fact bodies here.

## Project context

- Second Brain v0.2 build on branch `claude/v2-prototype` in `/home/jpietrak/second_brain`.
- Active vault: Inference-Disagg (inference disaggregation research).
- Vault PURPOSE: resolve the role of memory-bandwidth disaggregation in serving large
  language models at scale. PRIMARY relevance filter for all crawl queries.
- Phase 3 plan: `plans/phase-3-web.md` (grill-me locked 2026-06-22). Agent def: `.claude/agents/web.md`.
- Authoritative docs (frozen): `docs/{requirements,agents,vault-schema,skills-description}.md`.

## Phase 3A scope (FREE, $0)

- Free-tier crawl only: RSS (feedparser) + free APIs (arXiv, OpenAlex, Semantic Scholar, CrossRef,
  HackerNews, Reddit, Lobsters, GitHub releases) + native WebSearch/WebFetch + local ollama rerank.
- Paid engines (Perplexity SP, then Apify/crawl4AI) = **Phase 3B**, gated off
  (`web-config.paid_scrape.enabled: false`).
- Hard cap **5 new candidates/run** (the focus/"temperature" knob).
- Typed budget: **3 gap / 1 research / 1 news**, spillover **gap > research > news**.

## DECISION model (built: `scripts/web_decision.py`)

- Reads `wiki/gaps.md` `## Knowledge Gaps` GAP-NN blocks (TOP PRIORITY) + open
  `objective/direction/DIR-*` + the registry + `web-config.json`.
- **Merge gap<->direction** one-to-one greedy on `targets_gap`<->gap-text Jaccard (domain stoplist)
  + small topic bonus; `MERGE_THRESHOLD=0.18`. A merged target keeps origin=gap (precedence) and
  uses the direction's seed_queries + expected_evidence. (Live: GAP-08+DIR-0004, GAP-02+DIR-0005
  merge; DIR-0001/2/3 -> research lane. Threshold sits between legit ~0.19 and topic-only 0.05.)
- Lanes: gap (any gap-origin), research (standalone direction), news (top registry blog/RSS feeds).
- `route_to_sources`: fillable_by -> registry by relevance + focus overlap.
- `select_candidates`: per-item dedup + ingest-dedup (drop ingested/pending/sticky-rejected) + lane
  quotas + spillover + cap 5.

## Pipeline scripts (built)

- `scripts/web_harvest.py` -- Tier-0 free harvest (poll_rss / query_papers / query_forum /
  poll_github_releases / dedup_seen). Reuses `scripts/research/lib/sources/*`.
- `scripts/web_rank.py` -- ollama cosine (reuses rerank.py primitives) with deterministic
  keyword+relevance+recency fallback when `nomic-embed-text` is absent.
- `scripts/web_crawl.py` -- the orchestrator: `--vault <root> [--dry-run]`. Non-dry-run enqueues
  waiting_approval + writes `meta/nightly_report/<date>.md` (frontmatter `written_by: web`).
- **Per-item identity:** dedup + enqueue key on `web_harvest.candidate_ident` (arXiv id / DOI /
  url), NOT the feed `source_id` (which stays the registry association for the rank bonus). Fixing
  this stopped distinct same-feed RSS posts collapsing to one queue row.

## Config (exists)

- `.claude/web/web-config.json` -- quotas/spillover/registry_boost/paid_scrape. User-editable.
- `.claude/web/sources/{paper-publisher,github-repos,blog-newsfeed}.json` -- the pre-scored registry.
- `.claude/web/cache/seen.json` -- cross-crawl RSS seen-cache (gitignored).

## RBAC boundary

- Web writes ONLY `meta/nightly_report/` via the Write tool. enqueue is a Bash CLI call
  (`agents.ingest_index enqueue`), NOT a Write tool (so unguarded by rbac_guard).
- NEVER write wiki/, raw/, objective/, research/. NEVER use Edit. rbac_guard ALLOWLIST["web"] =
  ["meta/nightly_report/"]. Frozen matrix updated (user-authorized): nightly_report write =
  Research / Web.

## Feedback loop (3A: emit; 4: persist)

- Read prior `approve` (positive) + `reject --reason` (negative, sticky) from the ingest index to
  tune future crawls. Persistence via `agent-learn` is Phase 4 -- do not implement learning in 3A.

## CRAWL QUALITY finding (2026-06-22 live demo)

- Live $0 demo PASSED end-to-end (plan -> real arXiv+RSS harvest -> rank -> 5 distinct
  waiting_approval + digest; reject->sticky verified). arXiv path on-target (SpectrumKV ->
  DIR-0003). **RSS-blog path is noisy under the deterministic fallback ranker** (favors recent
  generic posts). FIX FOR QUALITY: `ollama pull nomic-embed-text` -> web-rank uses semantic cosine;
  and prefer query-driven paper sources for specific gaps, reserve RSS for the news lane. The
  fallback is a floor, not the intended ranker.

## Build progress

- [x] Phase 3A complete 2026-06-22 (commits 954dbc1 W1, a548a75 W2, b1948e3 W3): agent + RBAC +
  config/registry + harvest + decision + rank + orchestrator + 2 skills; 625 tests; live $0 demo.
- [ ] Skills `/web-scrape` `/web-rank` + the `web` agent need a **CLI restart** to register.
- [ ] Phase 3B: paid scraper adapters behind the cost gate.
- [ ] Phase 4: nightly_run.sh integration (web after research) + agent-learn feedback persistence.
