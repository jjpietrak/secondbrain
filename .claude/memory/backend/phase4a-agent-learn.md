# Phase 4A (learning layer / closed web feedback loop) — Waves 1-2 DONE (2026-06-24)

Phase 4 split: **4A = learning/feedback layer** (this); **4B = nightly orchestration + token-window
budgeting** (deferred until 4A makes web trustworthy). Plan: `plans/phase-4a-learning.md`. Grill-me
locked: agent-learn = reusable, web-first; learning layer first (reputation + reject-patterns +
calibration → routing & ranking), query-reformulation = step 2; **auto-apply + always surfaced as a
`## Learning briefing` at the top of each crawl report** (no approval step, nothing hidden).

## Built (code complete; 1023 tests; NOT pushed)
- **`scripts/agent_learn.py`** (reusable core): `learn(vault_root, agent="web", *, apply, today) ->
  {learned, briefing, delta, n_new}`. Reads decided `discovered_by=<agent>` ingest rows
  (ingested/pending=ACCEPT, rejected=REJECT, waiting=neutral); incremental via `processed_ids`.
  Helpers: `rep_for(learned, source_id, engine)` (source>engine>0), `reject_penalty(learned, cand)`,
  `render_briefing(delta, prev)`. learned.json at `.claude/memory/<agent>/learned.json`.
- **Reputation** smoothed Beta: `p=(a+prior)/(a+r+2·prior)`, `rep=2p-1` ∈ (-1,1); fresh source → 0
  (cold-start neutral); one reject ≈ -0.33; `prior_strength` from web-config.
- **Signal plumbing**: `ingest_index.enqueue` now persists `source_id`+`engine` (in `_V3_DEFAULTS`);
  `web_crawl` passes them. Pre-existing rows lack these → no reputation from them (builds next crawl on).
- **Feed-back wiring (W2)**: `web_rank.score_candidates(..., learned=, weights=)` →
  `score = base + w_rep·rep(src|eng) − w_rej·reject_penalty`; `web_decision.route_to_sources(...,
  learned=)` reorders sources within each fillable_by class by `relevance+rep`;
  `build_plan(..., learned=)` threads it. `web_crawl.crawl` runs `agent_learn.learn` at START
  (apply=not dry_run; graceful if disabled/errors), passes `learned` to rank+route, embeds the
  briefing at the TOP of the report. `learned=None` everywhere = byte-identical back-compat.
- **`.claude/web/web-config.json`** `learn: {enabled:true, w_rep:0.15, w_rej:0.2, prior_strength:1.0,
  min_reject_freq:2}`.
- **`.claude/skills/agent-learn/SKILL.md`** (reusable; web instance dispatches subagent_type: web).

## reject-keyword SAFETY fix (the live demo caught it)
First live dry-run pulled HARMFUL reject keywords from single free-text reasons — core PURPOSE terms
(`disaggregation, cache, iris, tetra, optical, pipeline`) + generic noise (`authors, much, thesis...`).
Penalizing `disaggregation` (the PURPOSE) would tank relevant candidates. FIX: (1) **frequency
threshold** — `reject_patterns` stores `keyword_counts` (cumulative); `keywords` (what reject_penalty
reads) = derived = tokens with count ≥ `min_reject_freq` (2). (2) **PURPOSE/topic guard** —
`_build_protected_terms` reads `objective/purpose/PURPOSE.md` + `objective/topic/*` → those tokens can
NEVER become reject keywords. Old-schema learned.json migrates additively. Re-demo: reject keywords → none.

## LIVE DEMO (real /wiki-approve outcomes, $0, dry-run, clean slate)
4 real decisions processed; **calibration accepted 0.70 vs rejected 0.33** (the embedding ranker WAS
predictive on this batch — counters the earlier "weak cosine" worry); reject keywords none (safe);
reputation empty (old rows lack source_id/engine). Loop runs correctly + safely on real signal.

## learned.json = RUNTIME STATE (not tracked)
A test had leaked fixture data to the real `.claude/memory/web/learned.json`. Deleted it + added
`.claude/memory/.gitignore` (`learned.json`): learned state is machine-generated, per-vault,
reconstructible from outcomes — persists on disk + surfaces via the briefing but NOT version-controlled
(like the embed/seen caches). A fresh `pytest tests/` leaves no real learned.json (tests are isolated).

## DEFERRED
- **Step 2: crawl-time query reformulation** (the "formulating" weakest-link redesign) — NOT done.
- Per-topic reputation; calibration as an ACTIVE ranking lever (now observed+briefed only); nightly
  auto-run of agent_learn (4B). Reputation accrues from post-W1 crawls (rows with source_id/engine + decisions).

## NEXT
Step-1 learning layer complete + demoed. Step 2 (query reformulation) is the obvious follow-on for the
"formulating" half; 4B (nightly + budgeting) still deferred. Awaiting user direction.
