# Web Agent -- Memory index

One line per memory. Fact files live beside this index (one fact per file). Keep this
index current; never store fact bodies here.

## Project context

- Second Brain v0.2 build on branch `claude/v2-prototype` in `/home/jpietrak/second_brain`.
- Active vault: Inference-Disagg (inference disaggregation research).
- Vault PURPOSE: resolve the role of memory-bandwidth disaggregation in serving large
  language models at scale. This is the PRIMARY relevance filter for all crawl queries.
  A candidate is high-value if it directly addresses HBM/CXL/disaggregated memory in the
  context of LLM inference throughput, prefill/decode disaggregation, or KV-cache offload.
- Phase 3A plan: `.claude/agents/web.md`. Web agent definition: `.claude/agents/web.md`.
- Authoritative docs (frozen, read-only): `docs/{requirements,agents,vault-schema,skills-description}.md`.

## Phase 3A scope

- Free-tier crawl only: WebSearch, WebFetch, RSS, arXiv API, Semantic Scholar API, HN Algolia.
- Budget: $0 per run. No paid APIs (Perplexity, Apify, crawl4AI) until Phase 3B is approved.
- Max 5 candidates staged per run as `waiting_approval` in the ingest index.
- Typed budget per run: 3 gap-driven + 1 direction-driven + 1 news. Spillover: gap > direction > news.

## DECISION model

- Read wiki/gaps.md (gap topics with no source coverage) -> fill gap slots.
- Read objective/direction/DIR-*.md (active, non-resolved directions) -> fill direction slot.
- Merge + deduplicate; assign query types (gap / direction / news).
- Score each candidate: relevance (0-3) + recency (0-3) + authority (0-3) + gap-overlap (0-2).
- Dedup by URL; skip URLs already in meta/ingest_index/ (any status).
- Keep top <=5 globally across all slots.

## Config location

- Main config: `.claude/web/web-config.json` (RSS feeds, default query params, score thresholds).
- Source registry: `.claude/web/sources/*.json` (one file per source class).
- Neither file exists yet; to be created in Phase 3A skill build (Wave 1b onward).

## RBAC boundary

- Web writes ONLY `meta/nightly_report/` via the Write tool.
- Ingest index enqueue is a Bash CLI call (`agents.ingest_index enqueue`), NOT a Write tool call.
- NEVER write wiki/, raw/, objective/, research/ from the web agent.
- NEVER use Edit or MultiEdit; the web agent toolset has no Edit tool.
- rbac_guard.py ALLOWLIST["web"] = ["meta/nightly_report/"] (added in Phase 3 Wave 1a).

## Feedback loop (Phase 3A inline, Phase 4 persistent)

- Before each run: scan meta/ingest_index/ for approved/rejected entries from prior web runs.
- Rejected domain/topic -> -1 relevance penalty for matching candidates this run.
- Approved domain -> +1 relevance bonus for matching candidates this run.
- Phase 4 (deferred): persist per-domain weights to .claude/memory/web/source-weights.json.

## Build progress

- [x] Phase 3 Wave 1a: web.md, MEMORY.md, rbac_guard.py web role, test_rbac_guard_web.py (2026-06-22).
- [ ] Phase 3 Wave 1b: .claude/web/web-config.json + sources/*.json scaffold.
- [ ] Phase 3 Wave 2: crawl skills (web-crawl, web-stage, web-digest).
- [ ] Phase 3 Wave 3: nightly_run.sh integration; web agent called after research agent.
