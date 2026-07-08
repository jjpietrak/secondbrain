# Web-scrape fix plan (cuddly-meandering-pinwheel) — Phase 0 + Phase 1

Plan: `C:\Users\kubap\.claude\plans\cuddly-meandering-pinwheel.md`. Symptom being fixed:
after a few `web-scrape` runs, results degraded to near-pure NVIDIA vendor-blog. Root cause
was mostly bugs + a vendor-blog structural head-start, not bad scoring intuition. Scope
choice (user): diversify by **reweight only** — NO hard per-domain caps, NO round-robin.

## Phase 0 (already landed before this task, commit 36e6a73 on claude/v2-prototype)
- YAML block-list frontmatter fix in `web_decision._parse_frontmatter` (revived the gap lane:
  `fillable_by` / `topics` were parsing empty -> 0 gap candidates).
- `seen.json` scoped to STAGED items only (web_crawl): un-staged papers can resurface;
  `prior_seen_keys = updated_seen - new_ids_set`, persist old-seen + newly-staged only.
- Learned model detox + reset (agent_learn): stop harvesting reject-reason keywords; fix
  engine-rep asymmetry; neutral `learned.json`.

## Phase 1 (this task — code changes, NOT committed; on branch claude/gracious-boyd-4d9ea4)
Four changes, all deterministic/$0, back-compat (new params default to None = old behaviour):

1. **Paper/blog relevance parity** — `web_rank.py`. RSS blogs carry source_id = registry
   feed id (got `relevance/5 * W_REL`); papers carry a per-ITEM id (`arxiv:2401.09670`) that
   is never a registry id, so they scored 0.0 relevance -> a ~+0.40 blog head-start. Added
   `_engine_paper_maps(registry)` (reuses `web_crawl._paper_engine_for_entry`) to build
   engine->relevance + engine->category maps from the paper-publisher section; `_registry_relevance`
   now falls back to the candidate's harvest `engine` when source_id is not a registry id.
2. **`category_weights` reweight knob** (the diversify lever) — new block in
   `.claude/web/web-config.json` (`vendor_blogs: 0.5`; `hardware_analysis 1.2` /
   `benchmarking 1.15` / `specialist_research 1.2` / `research_institutions 1.1`; papers 1.0;
   `_default 1.0`). Consumed in TWO deterministic places, threaded as optional params:
   - `web_rank._deterministic_score`: `rel_score *= category_weight(candidate_category)`.
     Category resolved from registry entry (RSS) or engine-derived category (papers).
     Threaded via `score_candidates(category_weights=...)` <- `web_crawl` passes
     `config.get("category_weights")`. NOT applied to the ollama embedding cosine path
     (kept pure) — only the deterministic fallback rel_score.
   - `web_decision.news_target(category_weights=...)`: multiplies each blog source's
     relevance in the top-5 sort. Threaded from `build_plan(config)`.
3. **Deterministic queries** — `web-config.json` `query.reformulate: false` (was true;
   LLM reformulation emitted salad like "sigma sigma lstab"). `web_query.py` kept, off by
   default. Improved `web_decision._derive_gap_queries` + new `_clean_query_text` helper:
   strips `[[wikilinks]]`, `[text](url)`, citation brackets `[3]-[6]`, markdown marks;
   gap queries = clean title + cleaned first `## Missing` sentence.
4. **Prompt-doc reconciliation** — rewrote `.claude/agents/web.md` Role section
   (inputs = `wiki/gap/GAP-*.md` + `objective/direction/DIR-*.md`; orchestrator
   `web_crawl.py`; rank = web_rank category-weighted; select = lane quotas + spillover;
   deterministic queries default; reweight/no-caps intent) and fixed two stale `wiki/gaps.md`
   path refs; updated `.claude/skills/web-scrape/SKILL.md` (per-gap files, reformulation OFF
   by default, category reweighting note). RBAC/boundaries/memory sections kept as-is.

## Files touched (worktree /home/jpietrak/second_brain/.claude/worktrees/gracious-boyd-4d9ea4)
- scripts/web_rank.py (Changes 1, 2a) ; scripts/web_crawl.py (thread category_weights)
- scripts/web_decision.py (Changes 2b, 3) ; .claude/web/web-config.json (Changes 2, 3)
- .claude/agents/web.md ; .claude/skills/web-scrape/SKILL.md (Change 4)
- tests/test_web_rank.py (+TestEnginePaperMaps/TestEngineRelevanceParity/TestCategoryWeights;
  fixed test_registry_relevance_bonus_changes_order to use engine="" for the true-zero case),
  tests/test_web_decision.py (+TestDeriveGapQueries; +news_target category_weight tests),
  tests/test_web_crawl.py (fake score_candidates signatures accept category_weights).

## Tests + demo
- 505/505 pass across the 6 web test files. Full suite: only 10 unrelated FileNotFound
  failures (test_obj_reconcile / test_obj_synth_helper CLI subprocess hard-codes a
  worktree-relative `.venv` that doesn't exist — venv lives in the MAIN repo; env artifact,
  not a Phase-1 regression).
- Live read-only demo (Inference-Disagg, /mnt/c/Obsidian/Inference-Disagg):
  `web_decision.py plan --json` -> NEWS routed_sources now
  `[semianalysis_newsletter, inferencex_semianalysis, anandtech, interconnect_research,
  mlcommons_blog]` (no NVIDIA vendor_blogs in top-5); gap queries are clean phrases.

## Open decisions / notes for the user
- Embedding (ollama cosine) path is left PURE (no category weight), per plan. If the
  reweight should also bias the embedding path, that is an explicit follow-up.
- `category_weights` values are Phase-1 starting values; tuned in Phase 4 (eval harness).
- Research-lane DIR seed_queries still contain `[[wikilinks]]` (authored in
  objective/direction/*.md); `_clean_query_text` only cleans GAP-derived queries. Cleaning
  direction seed_queries is out of Phase-1 scope — flag if wanted.
- `paid_scrape.enabled` is still `true` in web-config (plan's global note suggests flipping
  to false); left as-is since it was not in the Phase-1 change list — confirm.
