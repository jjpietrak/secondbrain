---
name: web-scrape
description: >
  Coordinate free-tier web crawl engines to discover source candidates that fill wiki gaps
  and serve research directions, and stage <=5 per run for user approval. Phase 3A harvests RSS
  feeds + free APIs (arXiv, OpenAlex, Semantic Scholar, CrossRef, HackerNews, Reddit, Lobsters,
  GitHub), ranks locally, and enqueues waiting_approval candidates with an approval digest.
allowed-tools: Read, Write, Glob, Grep, Bash, WebSearch, WebFetch
---

**Ownership: `web` agent.** If you are NOT the `web` subagent (e.g. the main orchestrator or
another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: web`,
pass the user's full request, let the web agent run the steps below, and relay its result.
Do NOT run the steps yourself - running as the `web` agent is what activates the RBAC/write-scope
boundary. If you ARE the `web` agent, proceed.

# web-scrape: free-tier crawl discovery pipeline

Owner: **web**. Discovers web sources to fill wiki Gaps + serve research Directions, ranks them,
and stages <=5 candidates per run as `waiting_approval` in the ingest index plus an approval
digest in `meta/nightly_report/<date>.md`. The user then approves 0-5 for ingest.

Phase 3A is **free-tier, $0 marginal**: RSS polls + free APIs (arXiv, OpenAlex, Semantic Scholar,
CrossRef, HackerNews, Reddit, Lobsters, GitHub) + local embedding rerank (ollama, with an offline
keyword fallback). Paid engines (Perplexity SP, Apify, crawl4AI) are **Phase 3B**, gated off.

## When to use

- Nightly: the orchestrator triggers web-scrape after `obj-synth` emits new `objective/direction/*`
  and `wiki-gaps` refreshes `wiki/gaps.md`.
- Manual: "search the web for", "find sources on", "run a web crawl".
- Approval: the user reviews a prior run's `meta/nightly_report/<date>.md`.

## Task-start protocol (MANDATORY)

1. **Decisions (hard constraints):**
   `.venv/bin/python -m agents.objectives --json decisions` — honor every decision with
   `scope: all` or `scope: web` (e.g. D-0001 no-web proxies, cost caps).
2. **Memory:** read `.claude/memory/web/MEMORY.md` + referenced facts — prior approvals/rejections
   and whether paid scrape is enabled.

## Pipeline — ONE orchestrator command

The whole flow (plan -> harvest -> rank -> select -> stage) is implemented by
`scripts/web_crawl.py`. Run it as the web agent:

```bash
# Preview only (no writes, no enqueue) — recommended first:
.venv/bin/python scripts/web_crawl.py --vault "$VAULT_ROOT" --dry-run --json

# Real run — enqueues <=5 waiting_approval + writes meta/nightly_report/<date>.md:
.venv/bin/python scripts/web_crawl.py --vault "$VAULT_ROOT"
```

Resolve `$VAULT_ROOT` first: `eval "$(.venv/bin/python -m agents.vault_config env)"` (or pass
the path directly, e.g. `--vault /mnt/c/Obsidian/Inference-Disagg`). `--allow-remote-ollama`
forwards to the ranker; `--limit N` bounds per-source harvest.

### What `web_crawl.py` does internally (and how to inspect each stage)

1. **DECISION** — `scripts/web_decision.py build_plan` reads `wiki/gaps.md` (the `## Knowledge
   Gaps` GAP-NN blocks — TOP PRIORITY) + open `objective/direction/*` + the registry + config,
   and emits a ranked crawl plan. Inspect it standalone:
   ```bash
   .venv/bin/python scripts/web_decision.py plan --vault "$VAULT_ROOT" --json
   ```
   Each `plan.targets[]` carries: `target_id` (e.g. `GAP-08+DIR-0004`, `GAP-03`, `DIR-0002`,
   `news`), `lane` (`gap`|`research`|`news`), `origin_ids` (`[GAP-..,DIR-..]`), `priority`,
   `queries`, `expected_evidence`, `fillable_by`, `routed_sources` (registry ids). A direction
   that targets the same need as a gap is **merged** into it (origin keeps gap precedence).
2. **HARVEST** (free Tier-0, `scripts/web_harvest.py`): per target, by `routed_sources`/`fillable_by`
   — `poll_rss` (registry feeds), `query_papers` (arxiv/openalex/semantic_scholar/crossref),
   `query_forum` (hackernews/reddit/lobsters), `poll_github_releases`. Research-lane targets with
   empty `routed_sources` default to arXiv-API on their seed queries (you MAY additionally run a
   `WebSearch` and `WebFetch` a promising URL for a richer preview — $0, allowed in 3A).
   Cross-crawl `dedup_seen` drops items seen in earlier runs.
3. **RANK** (`scripts/web_rank.py` = the `web-rank` skill): score candidates vs PURPOSE +
   `expected_evidence`. ollama cosine when `nomic-embed-text` is pulled; else a deterministic
   keyword+relevance+recency fallback (lower quality — see Cost note).
4. **SELECT** (`web_decision.select_candidates`): per-item dedup, ingest-dedup (drop
   ingested/pending/sticky-rejected), lane quotas from `web-config.json` (`3 gap / 1 research /
   1 news`) with spillover `gap>research>news`, cap at `new_sources_total` (5).
5. **STAGE**: each selected candidate -> `ingest_index.enqueue(ident, discovered_by="web",
   objective_ids=[GAP-/DIR-], score, rationale)` (status `waiting_approval`); and a digest row in
   `meta/nightly_report/<date>.md` (title, source_id, lane, origin, score, url, snippet). The
   enqueue/dedup key is the per-ITEM identity (arXiv id / DOI / url), NOT the feed `source_id`.

## Approval flow (user-driven)

The user reviews `meta/nightly_report/<date>.md` and picks **0-5**:
- **Approve:** `.venv/bin/python -m agents.ingest_index approve <id>` -> `pending`; the **wiki**
  agent then fetches the source into `raw/<type>/` and runs `wiki-ingest`.
- **Reject:** `.venv/bin/python -m agents.ingest_index reject <id> --reason "<why>"` -> sticky
  `rejected` (never re-proposed; the Feedback-for-Learning signal).
- Inspect the queue any time: `.venv/bin/python -m agents.ingest_index queue`.

## Cost discipline (Phase 3A = $0)

- Free engines only: RSS + arXiv/OpenAlex/Semantic Scholar/CrossRef + HN/Reddit/Lobsters + GitHub;
  local ollama rerank + offline fallback. `WebSearch`/`WebFetch` are $0 (native).
- **Per-run cap = 5 new candidates** (`new_sources_total` in `.claude/web/web-config.json`) — the
  focus/"temperature" knob; lower = more focused, higher = broader.
- **Quality note:** for the best gap-fill, pull the embedding model once
  (`ollama pull nomic-embed-text`) so `web-rank` uses semantic cosine. Without it, the keyword
  fallback over-favors recent generic RSS posts; prefer query-driven paper sources for specific
  gaps and reserve RSS for the `news` lane.
- Paid scrape stays OFF (`web-config.paid_scrape.enabled: false`) — Phase 3B.

## RBAC boundaries

- **MAY write:** `meta/nightly_report/<date>.md`; the ingest queue via
  `agents.ingest_index enqueue` (a Bash call). MAY raise `objective/agent_todo/` only if a future
  decision grants it (not in 3A).
- **MUST NOT write:** `wiki/` (wiki owns ingest), `raw/` (Needs-Approval, wiki writes on approve),
  `objective/direction|research_question|decision|purpose|topic`, `meta/health_report|cost_report`.
- **No `Edit`** anywhere; analysis is Read/Grep/Glob + WebSearch/WebFetch only.

## Phase 3B (deferred — NOT functional in 3A)

When `web-config.paid_scrape.enabled: true`: paid engines (Perplexity SP first, then Apify /
crawl4AI) behind a per-crawl cost cap + `cost_tracker` logging, used only for JS-walled / bulk
pages that Tier-0/1 cannot retrieve.

## Feedback loop (persistence = Phase 4 `agent-learn`)

The web agent reads prior `approve` (positive) and `reject --reason` (negative) signals from the
ingest index to tune future crawls. In Phase 3A just PERSIST the signal (enqueue/reject); the
learning step lands with `agent-learn` in Phase 4.

## Conventions

- Follow `skills/references/ai-first-rules.md` + `write-rules.md`. ASCII only.
- Candidates dedupe by stable per-item id (arXiv / DOI / canonical URL), never by filename.
- `written_by: web` on the nightly_report (provenance unify; never `generated_by`).
