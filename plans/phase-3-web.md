# Phase 3 — Web Search Agent + crawl/scrape + approval queue + feedback loop

Detailed per-phase plan (implementation guideline #1: plan + grill-me before building).
Grill-me held 2026-06-22; the four locked answers are in **§0**. Companion: the Web DECISION
narrative in `plans/objective-flow.md` (the block this plan finally designs). Authoritative docs
(frozen, read-only): `docs/{requirements,agents,vault-schema,skills-description}.md`.

---

## §0 — Locked decisions (grill-me 2026-06-22)

1. **Ranking = strict two-tier WITHIN a typed source budget** (not a flat gaps-first list). Each
   `web-scrape` run proposes up to **N=5 new sources**, split by a **per-type quota** stored in a
   per-vault `web-config.json`. Starting quota:
   - **3 — gap lane** (wiki GAPs: missing papers, required references from core citations, …);
   - **≥1 — research lane** (a source serving a research direction/question, *not* reducible to a
     current wiki gap);
   - **1 — news lane** (a registry blog/newsletter RSS item, recent + PURPOSE-relevant).
   Within the gap lane, order strictly by gap priority (high > med > low). The user then **chooses
   0–5** of the proposed candidates to include in the next nightly ingest (the approval step).
2. **Iterative scraper build. Phase 3A = free-tier only; Phase 3B = paid.** 3A's default crawl path
   is **$0** (RSS + free paper/forum APIs + Claude-native `WebFetch` + local ollama rerank +
   deterministic `pdf_extract`). 3B adds metered engines (Perplexity SP, then Apify/crawl4AI) behind
   a cost gate. Build/test 3A now; defer 3B until budgeted.
3. **Registry = seed/prior, not a hard allowlist.** The 62 registry sources get a relevance **boost**
   in ranking; the agent **may** also follow citations and run open `WebSearch`/`WebFetch` ($0) to
   discover unlisted sources — those simply rank lower.
4. **Merge gap ↔ direction into one crawl target.** When a direction targets the same need as a wiki
   GAP (shared question/topic, e.g. `DIR-0004` ≈ `GAP-08` optical prior art), collapse them into ONE
   target so they don't each consume budget: **origin = gap** (keeps gap precedence), and the
   direction's `seed_queries` + `expected_evidence` supply the **HOW**.

**Standing constraints (still in force):** ~$5 paid Anthropic budget → test on free routes only;
never commit secrets; `ANTHROPIC_API_KEY` is the LiteLLM-proxy's only; **commit the code repo, never
push it**; the vault repo (`secondbrain-matter`) may be pushed when the user asks; `docs/` are frozen
(user-only edits) — any matrix change is routed to the user, not made by an agent.

---

## §1 — What the source registry gives us

The user placed three JSON files in `/home/jpietrak/second_brain/sources/` (move to config in W1):

| File | Count | Shape (per entry) |
|---|---|---|
| `paper-publisher.json` | 8 | arXiv cs.AR/cs.DC/cs.LG/eess.SP (**`rss_feed`** + arXiv API), Papers with Code, Emergent Mind (RSS), **Semantic Scholar API**, **OpenAlex API** — each with `relevance` 1–5, `priority`, `focus`, `update_frequency` |
| `github-repos.json` | 20 | vLLM, TVM, MLIR, DeepSpeed, FlashAttention, … with `monitor: [releases, PRs, …]`, `relevance`, `priority` |
| `blog-newsfeed.json` | 44 + 10 conf | vendor blogs (NVIDIA, AMD, Cerebras, Google TPU…), benchmarking (MLCommons, InferenceX), analysts (SemiAnalysis), institutions, model labs, hyperscalers — most with **`rss_feed`**; + 10 conference venues |

**Consequence:** discovery is *poll the known feeds + query the known free APIs*, not open-ended
crawling. This is the cost story. Almost every entry maps to an existing free client (§4) or an RSS
feed (`feedparser`, installed).

---

## §2 — DECISION inputs are already structured

- **Wiki GAPs** (`wiki/gaps.md`, written_by: wiki): `GAP-01..GAP-10` blocks, each with
  `shows_up_in`, `missing`, **`fillable_by`** (arxiv/web/github/forum → registry category), `topic`,
  `priority`. Plus the **Open-Question Harvest** (TOP PRIORITY) lifted from each page's
  `## Open questions` / `## For future Claude`. **These take precedence** (user's hard rule).
- **Directions** (`objective/direction/DIR-*`, written_by: research): `serves_question`, `topics`,
  `targets_gap`, **`expected_evidence`**, **`seed_queries`** (engine-tagged web|x|arxiv|github|forum),
  `solves_when`, `priority`. The HOW for ranking + querying.
- **Open research questions** (`objective/research_question/Q-*`, solved:no) + **PURPOSE** — for
  relevance scoring + lane assignment.

Nothing new needs to be authored in the objective graph; the DECISION *reads* these.

---

## §3 — Architecture: the free-first tiered funnel

```
  DECISION ──► HARVEST ──► RANK ──► DE-DUP ──► CAP(quota) ──► STAGE ──► (user) APPROVE ──► wiki INGEST
 (web_decision)(web_harvest)(web-rank)(ingest_index)(web-config) (enqueue+      (0-5 of 5)   (raw/ + wiki-ingest)
                 Tier 0/1                            wiki gaps    nightly digest)
                                                     precede
                                                                       └─(accept/reject)─► FEEDBACK ► memory/web/
```

| Tier | Mechanism | Cost | Phase |
|---|---|---|---|
| **0 — Harvest** | RSS (`feedparser`); arXiv/OpenAlex/Semantic Scholar/Crossref clients; GitHub releases API; HN/Reddit/Lobsters | **$0** | 3A |
| **1 — Fetch** | Claude-native `WebFetch` (one URL → md); arXiv PDF → `pdf_extract.py` | **$0** | 3A |
| **Rank** | `web-rank` = `rerank.py` ollama cosine vs PURPOSE + `expected_evidence`; **deterministic fallback** if ollama down (exit 10) | **$0** | 3A |
| **2 — Scrape** | Perplexity SP → Apify/crawl4AI, JS-walled/bulk only; capped + `cost_tracker`-logged | **$ metered** | **3B** |

---

## §4 — Reusable assets (port/wrap, don't rewrite)

- `scripts/research/lib/sources/{arxiv,openalex,semantic_scholar,crossref}.py` → the **paper-API
  lane** (free, key-less). `{hackernews,reddit,lobsters,devto}.py` → **forum lane**.
  `{duckduckgo,wikipedia}.py` → open-web fallback (seed/prior).
- `feedparser` (installed) → **RSS lane** (also parses arXiv Atom).
- `agents/ingest_index.py` **`enqueue(id|url, title, rationale, score, discovered_by, objective_ids)`**
  → the staging primitive; already idempotent, sticky-`rejected`, with the exact queue fields. Verbs
  `queue`/`approve`/`reject --reason` already exist.
- `scripts/rerank.py` → `web-rank` (ollama cosine, embed cache, localhost guard).
- `scripts/pdf_extract.py` → arXiv PDF → text (deterministic, $0).
- `scripts/research/lib/perplexity.py` → 3B Perplexity adapter.
- RBAC: `scripts/rbac_guard.py` (extend with a `web` role). Cost: `agents/cost_tracker.py`.

---

## §5 — The DECISION engine (`scripts/web_decision.py`, NEW)

Pure-Python, $0, hermetically testable. Steps:

1. **Parse** `wiki/gaps.md` → gap targets; `objective/direction/*` (status:open) → dir targets;
   load open `Q-*`, PURPOSE, the registry, and `web-config.json`.
2. **Merge** (locked #4): a dir whose `serves_question`/`targets_gap` matches a GAP (same topic +
   question, or text overlap on the missing-knowledge) folds into that GAP target →
   `{origin: gap, gap_id, dir_id, queries: dir.seed_queries, evidence: dir.expected_evidence,
   priority: max(gap, dir)}`.
3. **Lane assignment:**
   - **gap** — targets tracing to a wiki GAP (merged or standalone). Sort by gap `priority`, then
     prefer "missing paper / core-citation reference" character.
   - **research** — directions *not* reducible to any current GAP (forward trajectories, e.g.
     simulator-design `DIR-0002`/served by `Q-0002`/`Q-0007`).
   - **news** — a freshness pull: top registry blog/newsletter RSS item(s), PURPOSE-relevant, not
     tied to a specific gap.
4. **Route** each target to candidate sources: `fillable_by` / seed-query engine-tag → registry
   `category` → concrete feeds/APIs; registry sources get `registry_boost`; open `WebSearch`
   permitted as a lower-ranked fallback (locked #3).
5. **Budget** from `web-config.json`: `{total:5, quota:{gap:3, research:1, news:1},
   spillover_order:[gap,research,news]}`. Each lane fills up to its quota; an underfilled lane
   donates slots per `spillover_order`; total ≤ 5.
6. Emit a **crawl plan**: ordered `[{target, lane, sources[], queries[], expected_evidence,
   origin_ids:[GAP-/DIR-], priority}]`.

`web-config.json` lives in the **web agent's own subfolder** `.claude/web/web-config.json` (locked
2026-06-22, point C) — user-editable (manually or via Claude) to retune quotas/splits later; the
source registry moves to `.claude/web/sources/*.json` alongside it. (Trade-off vs per-vault config:
registry `relevance` is tuned to this vault's PURPOSE; if a 2nd vault is added later, parameterize.)
```json
{
  "new_sources_total": 5,
  "lanes": {
    "gap":      {"quota": 3},
    "research": {"quota": 1, "min": 1},
    "news":     {"quota": 1}
  },
  "spillover_order": ["gap", "research", "news"],
  "registry_boost": 0.25,
  "paid_scrape": {"enabled": false, "max_calls_per_crawl": 0, "engines": []}
}
```

---

## §6 — HARVEST + RANK + STAGE

- **`scripts/web_harvest.py` (NEW, Tier 0/1, $0):** per crawl-plan target, poll routed RSS feeds
  (feedparser) + run routed free-API queries (reuse `lib/sources/*`) + GitHub releases poll;
  Tier-1 `WebFetch` a specific URL when needed. Dedup new RSS entries by guid against a seen-cache
  (`config/vaults/<v>/web_seen.json` or `meta/web/seen.json`). Emit candidates
  `{title,url,source_id,date,snippet,lane,origin_ids}`.
- **`web-rank`:** score candidates vs PURPOSE + target.`expected_evidence` via `rerank.py`. Fallback
  (ollama down): deterministic `registry.relevance + keyword-overlap(expected_evidence) + recency`.
- **DE-DUP:** drop ids/urls already `ingested`/`pending`, or sticky-`rejected`, in `ingest_index`.
- **CAP + STAGE:** apply lane quotas → ≤5; for each survivor:
  `ingest_index.enqueue(id|url, title=…, rationale=…, score=…, discovered_by="web",
  objective_ids=[GAP-/DIR-])` → `waiting_approval` (via Bash), AND append a candidate row + embedded
  preview (title, source, lane, origin GAP-/DIR-, snippet/abstract, rationale, score) to
  `meta/nightly_report/<date>.md` (web-written, per locked point A — the user picks 0–5 to approve).
  **No `raw/` write** (Needs-Approval gate; wiki fetches into `raw/<type>/` on approve).

---

## §7 — Agent + RBAC

- **`.claude/agents/web.md`** (`id: web`, `memory: .claude/memory/web/`): tools `Read, Grep, Glob,
  Bash, WebSearch, WebFetch` (+ `Write` confined per below). `## Role` coordinator+learner directed
  by research; `## Task scope`; `## Memory protocol`. Seed `memory/web/MEMORY.md`.
- **`rbac_guard.py` `web` role:** the guard only evaluates Write/Edit-class tools, so web's
  `ingest_index enqueue` (a `Bash` CLI call) is NOT blocked by it. The guard's `ALLOWLIST["web"]` only
  needs the vault paths web writes **via the `Write` tool**: **`meta/nightly_report/`** (the approval
  digest). It **denies** `Edit` anywhere and any `wiki/`/`raw/`/`objective/` write. The web agent's
  `tools:` frontmatter (no `Edit`) is the primary control; the guard is the backstop. **Read**:
  `wiki/` (gaps + context), `objective/` (directions/questions/purpose), `meta/ingest_index`, registry.
- **RBAC conflict RESOLVED (locked 2026-06-22, point A):** the user authorized extending the frozen
  matrix — `docs/vault-schema.md:44` now reads **`meta/nightly_report/` write = Research Agent / Web
  Agent**. So web writes the `meta/nightly_report/` digest **directly** (candidate previews embedded)
  AND enqueues to `ingest_index` waiting_approval (machine source of truth, via Bash). No separate
  staging folder needed for 3A; add `meta/web_staging/` only if fetched-artifact previews require it.

---

## §8 — Skills (web-owned; dispatch headers → `subagent_type: web`)

- **`web-scrape`** (NEW): the crawl orchestrator. 3A runs DECISION → HARVEST (Tier 0/1) → RANK →
  DE-DUP → CAP → STAGE. 3B adds Tier-2 engine selection (`--engine perplexity|apify|crawl4ai`,
  gated by `web-config.paid_scrape.enabled` + cost gate).
- **`web-rank`** (PORT): `rerank.py` wrapper + deterministic fallback.

(`autoresearch`, newsletter GUI, `--auto` nightly, youtube/x-read/nlm stay deferred per the master
plan; the **feedback signal** — reading `ingest_index` approve/reject + reasons — is emitted in 3A,
but its *persistence* via `agent-learn` is Phase 4.)

---

## §9 — Tests (hermetic, $0) + live demo

- `tests/test_web_decision.py` — parse gaps/dirs, merge (GAP-08+DIR-0004), lane assignment, quota
  fill + spillover, dedup vs a fake ingest_index, cap = 5. Fixtures: sample `gaps.md` + `DIR-*` +
  fake registry + fake ingest_index.
- `tests/test_web_harvest.py` — RSS parse over a local fixture XML; `lib/sources/*` + GitHub poller
  **mocked** (no network); seen-cache dedup.
- `tests/test_rbac_guard_web.py` — web denied `wiki/`/`raw/`/`objective/` writes + `Edit`; allowed
  `meta/web_staging` + `ingest_index enqueue` + `WebFetch`/`WebSearch`.
- `tests/test_web_rank_fallback.py` — ollama-down → deterministic ranking is stable/ordered.
- **Live demo (3A, $0)** over `Inference-Disagg`: DECISION reads the real `gaps.md`
  (`GAP-01..10` + Open-Question Harvest) + the 5 `DIR-*` → merges (e.g. GAP-08+DIR-0004) → lanes
  (3 gap: e.g. the 4 optical prior-art papers / a missing P/D paper (DistServe/Splitwise) / Rubin CPX
  spec; 1 research: a simulator-design direction; 1 news: a recent NVIDIA/SemiAnalysis RSS item) →
  HARVEST via **free arXiv API + RSS** → rank → enqueue **≤5 `waiting_approval`** → show
  `ingest_index queue` + the staging previews. Then exercise feedback: `approve` 2, `reject 1
  --reason`; confirm sticky-rejected re-enqueue is a no-op. Entirely $0.

---

## §10 — Open points — ALL RESOLVED (locked 2026-06-22)

**A. nightly_report RBAC →** user authorized extending the frozen matrix; `docs/vault-schema.md:44`
   now grants `meta/nightly_report/` write to **Research Agent / Web Agent**. Web writes the digest
   directly + enqueues to `ingest_index`. (Done.)
**B. Lane budget →** quotas `{gap:3, research:1, news:1}`, total 5, **with spillover**
   `[gap, research, news]` (a short lane donates its slot to the next).
**C. Config location →** the **web agent's own subfolder** `.claude/web/` holds `web-config.json` +
   `sources/*.json` (user-editable to retune quotas/splits), not `config/vaults/`.
**D. Demo scope →** real free-network acceptance bar: live arXiv API + RSS, enqueue ≤5,
   approve/reject — all $0.

---

## §11 — Build waves (parallel per guideline #4; model-routed)

- **W1 (foundation, parallel):** (a) `web.md` agent + `memory/web/` seed + `rbac_guard` web role +
  `test_rbac_guard_web` [Sonnet]; (b) move registry → config + author `web-config.json` [Haiku];
  (c) `scripts/web_harvest.py` (reuse `lib/sources/*` + feedparser + GitHub) + tests [Sonnet].
- **W2:** `scripts/web_decision.py` (parse/merge/lane/budget/spillover/dedup/cap) + tests [Sonnet].
- **W3:** skills `web-scrape` (orchestrator, Tier 0/1) + `web-rank` (rerank wrapper + fallback) +
  dispatch headers [Sonnet].
- **W4 (me, Opus):** integration, $0 live demo, backend+web memory updates, plan/doc reconciliation.
- **Phase 3B (later):** Tier-2 paid adapters (Perplexity SP → Apify/crawl4AI) behind the cost gate +
  metered tests when budgeted.
