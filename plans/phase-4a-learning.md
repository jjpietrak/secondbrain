# Phase 4A — Agent learning + closed web feedback loop

Phase 4 is split: **4A (this plan) = the learning/feedback layer** (stabilize + improve the web
agent's result quality and make it self-improving); **4B = nightly orchestration + token-window
budgeting** (automation), deferred until 4A makes the web agent trustworthy. Grill-me locked
2026-06-23. No web crawl is auto-wired into the nightly until 4A lands (Phase 3 conclusion).

## Goal
Close the web retrieval feedback loop: your accept/reject decisions become **durable, auto-applied,
transparent** learning that improves the web agent's source **routing + ranking** over time.
**Step 1 (this plan)** = the LEARNING LAYER (source/engine reputation + reject-patterns +
calibration). **Step 2 (next, deferred)** = crawl-time query reformulation (the "formulating"
weakest-link revision).

## Locked decisions
1. `agent-learn` = a **reusable mechanism** (any agent self-updates its `memory/<id>/` from logs),
   **instantiated web-first**. wiki/research instances follow once proven.
2. **Learning layer first** (reputation + calibration + reject-patterns → routing & ranking);
   query reformulation = step 2.
3. **Auto-apply + fully visible.** Each crawl auto-updates learned state and **opens its report
   with a `## Learning briefing`** (what was learned from the previous crawl + how it changed this
   run). No approval step; nothing hidden. (`--auto` vs propose is moot — it auto-applies but is
   always surfaced in the report; a `--dry-run` exists for inspection/tests.)

## The cycle
`agent_learn` runs at the **START of each web crawl**: read the previous run's now-decided
outcomes → update `learned.json` → emit a briefing → the crawl applies `learned.json` in
route + rank → embeds the briefing at the top of the report.

## Signal (from `ingest_index`, `discovered_by="web"`)
- **ACCEPT (+1):** status in `{ingested, pending}` (you approved it).
- **REJECT (-1):** status `rejected` (+ `rejection_reason`).
- **NEUTRAL (0):** `waiting_approval` / backlog (undecided — "not yet" != "no").
- Each decided row must carry `source_id`, `engine`, `objective_ids`, `relevance_score`,
  `rejection_reason`. **NEW: `enqueue` must persist `source_id` + `engine`** (today it doesn't) so
  outcomes are attributable to a source/engine.
- **Incremental:** `processed_ids` in `learned.json` ensures each decision is counted once.

## `learned.json`  (`.claude/memory/web/learned.json`)
```json
{
  "updated": "<date>",
  "sources": {"<source_id>": {"accept": 3, "reject": 0, "rep": 0.6}},
  "engines": {"<engine>":   {"accept": 0, "reject": 1, "rep": -0.33}},
  "reject_patterns": {"keywords": ["agentic", "biology"]},
  "calibration": {"accepted_score_mean": 0.68, "rejected_score_mean": 0.61, "n": 5},
  "processed_ids": ["arxiv:...", "url:..."]
}
```
- **Reputation (smoothed, cold-start neutral):** `p = (accept + a) / (accept + reject + 2a)`;
  `rep = 2p - 1` in (-1, 1); `a = prior_strength`. Fresh source (0,0) -> `rep = 0` (neutral); one
  reject (0,1) -> ~-0.33; 3 accepts -> ~+0.6. One reject never tanks a fresh source.
- **reject_patterns:** content keywords from `rejection_reason` (lowercase, drop stopwords +
  domain-stoplist, len >= 4).
- **calibration:** accepted vs rejected score means (OBSERVED + briefed only; **not an active
  ranking lever in v1** — a weak/non-predictive gap is exactly the evidence that motivates the
  step-2 query redesign).

## Feed-back wiring
- **`web_rank`:** `score' = base(cosine|fallback) + w_rep * rep(source_id|engine) - w_rej *
  reject_penalty(candidate)`. `reject_penalty` = 1 when the candidate's title/snippet hits a reject
  keyword (or its source rep is strongly negative), else 0. Weights small so the base signal
  dominates while data is thin.
- **`web_decision.route_to_sources`:** within each `fillable_by` class, reorder routed sources by
  `(registry relevance + rep)` — productive sources first.
- **`web_crawl`:** run `agent_learn` at the start (update `learned.json` + get the briefing); load
  `learned.json` and pass it to `web_rank` + `route_to_sources`; embed the briefing at the top of
  the report.
- **`web-config.json`:** `learn: {enabled: true, w_rep: 0.15, w_rej: 0.2, prior_strength: 1.0}`.

## The briefing (top of each report)
```
## Learning briefing (applied from the <date> crawl)
- arxiv_cs_dc reputation up (3/3 accepted) -- ranked higher this run
- nvidia_developer_blog down (0/2; rejected "off-topic") -- down-ranked
- perplexity down (0/1) -- down-ranked; reject keywords added: agentic, biology
- rank calibration: accepted ~= rejected scores -> cosine weakly predictive (-> step-2 query redesign)
```
Cold start (no prior outcomes): "No prior outcomes yet -- learning starts after your first
approve/reject batch."

## `agent-learn` skill
`.claude/skills/agent-learn/SKILL.md` -- reusable; the web instance dispatches to
`subagent_type: web` and wraps `scripts/agent_learn.py`. (wiki/research instances later.) The web
crawl invokes `agent_learn.py` directly at crawl start; the skill is the manual/standalone entry.

## Defaults chosen (locked unless changed)
- Granularity: per `source_id` + per `engine` (not per-topic yet -- too sparse early).
- Deferred/backlog = neutral.
- Calibration = observed + briefed, not yet an active lever.
- Weights small (config).

## NOT in this step (deferred)
- **Step 2: crawl-time query reformulation** (the weakest-link "formulating" redesign).
- Per-topic reputation; calibration as an active lever; nightly auto-run of `agent_learn` (4B).

## Build waves
- **W1 (core + signal plumbing):** `ingest_index.enqueue` stores `source_id` + `engine`; `web_crawl`
  passes them; `scripts/agent_learn.py` (learn core: read decided web rows -> reputation +
  reject-patterns + calibration -> `learned.json` + briefing + `processed_ids`; reusable + CLI);
  `web-config` `learn` block; tests.
- **W2 (feed-back wiring):** `web_rank` + `route_to_sources` read `learned.json`; `web_crawl` runs
  `agent_learn` at start, passes `learned` to rank+route, embeds the briefing; `agent-learn` skill;
  tests.
- **W3 (integrate):** live demo on the REAL `/wiki-approve` outcomes (your 2 accept / 1 reject /
  2 deferred) -> show `learned.json` + the briefing + reweighted ranking; commit + memory.

## Verification / live demo
Hermetic unit tests per wave (reputation math incl. cold-start neutral + smoothing; processed_ids
idempotency; reject-keyword extraction; calibration; briefing content; rank/route reweighting).
Live: run `agent_learn` over the real vault outcomes from your `/wiki-approve` run, show the
`learned.json` + briefing, then a crawl that opens with the briefing and reorders sources by
reputation.

---

## Step 2 — query reformulation (Design B, + C-escalation TODO)

The "formulating" weakest-link fix. Today queries are static `seed_queries` (frozen at obj-synth
time, PURPOSE-blind at crawl time, engine-agnostic, ignore what the wiki already knows). Step 2
makes query formation a **crawl-time, inspectable, LLM-reformulated** step (Design **B**), with a
**deterministic fallback** (Design A) and **TODO hooks for selective escalation** (Design C).

### `scripts/web_query.py` — `reformulate(targets, *, purpose, vault_root, learned=None, use_llm=True)`
- For each target, build the **wiki-context delta**: read the origin gap's `shows_up_in`
  `[[wiki/...]]` pages -> a short "already known" snippet, so the LLM queries for the MISSING piece,
  not the whole topic. (The single biggest quality lever.)
- **LLM path** (default): ONE batched `scripts/claude_agent.sh --agent web` call rewrites ALL
  targets into 2-3 **per-engine** queries each (arxiv = precise terms/title; semantic_scholar/web =
  natural-language; forum = keywords), conditioned on PURPOSE + gap `missing` + `expected_evidence`
  + the wiki-context delta + learned good-query terms. Output = fenced JSON, defensively parsed.
  Cost-ledgered via `claude_agent.sh` (~$0 on the agent pool; one call per crawl).
- **Deterministic fallback** (`_deterministic_queries`, Design A): when LLM is unavailable / disabled
  / parse fails -> term-extraction from gap.missing + expected_evidence + PURPOSE -> per-engine
  variants. The crawl ALWAYS produces queries ($0, no hard dependency on the LLM).
- Returns targets with `queries` (+ `queries_by_engine`) replaced, plus a per-target reformulation
  record (old -> new, method=llm|fallback, rationale) for the **trace**.
- Config: `web-config.json` `query: {reformulate: true, use_llm: true, max_queries_per_engine: 3}`.

### Integration (`web_crawl`)
After `build_plan`, before harvest: `web_query.reformulate(targets, purpose, vault_root, learned)`
-> harvest uses the reformulated per-engine queries. The DecisionTrace records the old->new queries +
method + rationale (so `--explain`/the report show exactly what was searched and why). Back-compat:
`reformulate=false` -> the old seed_queries path, unchanged.

### TODO — Design C (selective escalation, deferred)
Leave clear `# TODO` notes at the reformulation site: eventually reformulate **selectively** — only
targets flagged with a **low retrieval/learning score** (origin gap/topic with low `calibration`,
repeated rejects, or thin yield in `learned.json`) — instead of all targets every crawl, to save LLM
calls and focus effort. Requires the learning signal to warm up first; for now reformulate all
enabled targets. (This is Design C built on top of B.)

### Tests + demo
Hermetic: mock the `claude_agent.sh` subprocess (canned structured queries) -> per-engine queries
produced; `--no-llm`/error/parse-fail -> deterministic fallback; wiki-context read from a tmp vault;
trace records the reformulation. No real LLM call in tests. Live: `--explain` shows static
seed_queries replaced by reformulated, delta-aware, per-engine queries.
