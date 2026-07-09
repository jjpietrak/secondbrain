# Backend Agent — Memory index

One line per memory. Fact files live beside this index (one fact per file). Keep this index
current; never store fact bodies here.

## Project
- (seed) Second Brain v0.2 build is underway on branch `claude/v2-prototype` in
  `/home/jpietrak/second_brain`. Plan: `C:\Users\kubap\.claude\plans\elegant-juggling-sparrow.md`.
  Source-of-truth docs (frozen, read-only for agents): `docs/{requirements,agents,vault-schema,skills-description}.md`.

## Build log
- [phase0-build.md](phase0-build.md) — Quick Phase 0 SHIPPED 2026-06-19: claude_agent.sh
  usage-capture contract, wiki-lock.sh (Layer-2) port, vault_lease.sh (Layer-1, wiring
  deferred to P4), locking.md snippet, 5 hermetic tests (all green via tests/run_phase0.sh).
- [phase1-complete.md](phase1-complete.md) — Phase 1 COMPLETE 2026-06-19: wiki agent + all
  13 skills (incl. the final wiki-retrieve SKILL), ingest_index v3, retrieval pipeline.
  wiki-init reconcile APPLIED to the live Inference-Disagg vault (folders/templates/hot-
  collapse/stray-relocate/daily+output-drop, all confirmed; existing pages untouched).
  End-to-end demo green: ingest/cite (real Gemini Flash validation call, $0.000653)/query/
  health/stats. vault_health dead-link resolver fixed (path-qualified wikilinks). Commits:
  code e8851c9, vault 445c46c (chain cbbbf53->910ea87->445c46c, revertible). Not pushed.
- [wiki-init-v2.md](wiki-init-v2.md) — wiki-init structural cleanup DONE 2026-06-19:
  Change1 (templates/ drop + 4 producer _template.md) + Change2 (vault _CLAUDE.md delete)
  applied to Inference-Disagg. SCHEMA_VERSION 1->2. All 15 on-disk checks pass.
  Vault commit df2c5df; code commit (see wiki-init-v2.md). Not pushed.
- [objective-edges-relink.md](objective-edges-relink.md) — Phase 2.5 COMPLETE 2026-06-22 (code 821759e,
  vault 2839e08 -> origin/master): objective edges = full path-qualified wikilinks via `objectives.py
  relink` (no aliases); `written_by` on ALL items (provenance unify, generated_by gone); vault_health
  degree-based orphan + missing_written_by check; templates+skills wire new nodes to self-link;
  written_by_backfill stamped 72 wiki/research pages. objective orphaned 22->0, missing_written_by 0. 374 tests.
- [vault-ops-skills.md](vault-ops-skills.md) — vault-push + vault-health added 2026-06-22 (commit
  664ef1d): vault-push commits+pushes the vault repo (formalizes obsidian-sync); vault-health
  generalizes vault_health.py to all areas (--area, per-type FM, cross-area links). Docs updated.
  319 tests green. written_by/edge-orphan checks deferred to land WITH Phase 2.5.
- [phase3a-web.md](phase3a-web.md) — Phase 3A (web agent, FREE) COMPLETE 2026-06-22 (954dbc1/a548a75/
  b1948e3): web agent + RBAC + .claude/web config/registry + web_harvest (Tier-0) + web_decision
  (gaps+dirs -> one-to-one merge -> lanes -> typed budget 3 gap/1 research/1 news + spillover) +
  web_rank + web_crawl orchestrator + web-scrape/web-rank skills. Live $0 demo passed (real arXiv+RSS
  -> 5 waiting_approval + digest; reject->sticky). 625 tests. Quality: enable `nomic-embed-text` for
  semantic rerank (RSS noisy on fallback). 3B paid + P4 nightly/agent-learn deferred. Restart to register.
- [gap-readers-migration.md](gap-readers-migration.md) — 2026-06-23: gap readers migrated from
  wiki/gaps.md (single file) to wiki/gap/GAP-NN-<slug>.md (per-file). parse_gaps new sig:
  parse_gaps(gap_dir: str, *, trace=None). _relevance_links GAP glob -> [[wiki/gap/<stem>]] or
  fallback [[wiki/gap/index]] (GAP-##). Both ingest_index.py + web_crawl.py inline copy updated.
  814 tests green.
- [phase3b-perplexity.md](phase3b-perplexity.md) — 2026-06-23: (1) FIX ingest_index status didn't persist
  on the live vault (report_approve VAULT_PATH env trick clobbered by VAULT=Inference-Disagg) -> explicit
  `root=` kwarg threaded through _load/_save/approve/reject (load==save at vault_root). (2) Perplexity as a
  GATED Tier-2 harvest engine: `web_harvest.query_perplexity` (reuses research/lib/perplexity citations) +
  `web_crawl --perplexity` (skill arg) gated on paid_scrape.enabled+KEY, top-2 targets, cost-logged; decision/
  rank/select/stage 100% reused. enabled=false default (no spend). 939 tests. PENDING: live $ test + Apify/crawl4AI.
- [phase4a-agent-learn.md](phase4a-agent-learn.md) — **Phase 4A (learning layer) Waves 1-2 DONE 2026-06-24.**
  agent_learn.py (reusable; learn/rep_for/reject_penalty/render_briefing; smoothed-Beta reputation,
  cold-start neutral; source_id+engine persisted on enqueue); WIRED into web_rank (learned prior) +
  route_to_sources (rep reorder) + web_crawl (agent_learn at crawl start, `## Learning briefing` at
  report top; back-compat when learned=None); web-config learn block; agent-learn SKILL. Auto-apply +
  always briefed. reject-keyword SAFETY fix (freq>=2 + PURPOSE/topic guard — never penalizes core terms;
  caught live pulling `disaggregation`). Live $0 demo (real /wiki-approve): calibration accepted 0.70 vs
  rejected 0.33 (ranker predictive), reject keywords none, reputation empty (old rows pre-date plumbing).
  learned.json = runtime state, gitignored. 1023 tests. DEFERRED: step-2 query reformulation; 4B nightly.
- [phase4a-query-reform.md](phase4a-query-reform.md) -- **Phase 4A step-2 (query reformulation) FULLY WIRED 2026-06-25.**
  web_query.py (reformulate API); web_crawl.py integration (after build_plan+agent_learn, _harvest_target
  per-engine routing, --no-reformulate, trace "reformulate/target"); render_trace_markdown ## Reformulate;
  SKILL.md step 1b. 12 new tests (TestQueryReformulation). 1054 total tests green, 0 real LLM calls.
- [web-scrape-fix-p0-p1.md](web-scrape-fix-p0-p1.md) — **Web-scrape fix P0 (landed) + P1 (this task,
  not committed) 2026-07-08.** Diversify away from NVIDIA vendor-blog by REWEIGHT ONLY (no caps): (1)
  paper/blog relevance parity via engine fallback in web_rank; (2) `category_weights` knob consumed in
  web_rank._deterministic_score + web_decision.news_target (embedding path kept pure); (3) deterministic
  queries (`query.reformulate:false`) + cleaned `_derive_gap_queries`; (4) web.md/SKILL.md reconciled.
  505 web tests green; live demo: news lane now SemiAnalysis-led, gap queries clean.
- [web-scrape-fix-p4-experiment.md](web-scrape-fix-p4-experiment.md) — **Web-scrape fix P4
  (in-loop tuning experiment INFRASTRUCTURE, this task, not committed) 2026-07-08.** Seeded
  throwaway vault `Disagg-Exp` (isolated clone; 4 control targets referenced-not-ingested;
  open GAP-11 for splitwise); registered config/vaults/Disagg-Exp/*; targets.json +
  exp-config.json (config copy so tuning never mutates shared) + chiplog fixture; eval harness
  `scripts/experiments/eval_retrieval.py` (monkeypatch _load_config -> crawl dry_run ->
  recall@5 + per-target rank/selected); 4 offline tests. BASELINE recall@5=0.25 (chiplog rank1
  selected; splitwise found rank21 unselected; megascale+gimlet not retrieved). CODE_PATH pin
  gotcha noted.
- [phase3-conclusion.md](phase3-conclusion.md) — **PHASE 3 CLOSED 2026-06-23 (user sign-off): functionally
  works OK w/ minor bugs, but relevance/accuracy NOT good enough + DECISION is obscure.** Perplexity returned
  only slightly-relevant results; arXiv path better but not great. NOT production-trusted — do NOT auto-wire web
  into nightly yet. DEFERRED complete revision (with agent-learn): (1) query formulation [weakest], (2) ranking,
  (3) gap/direction DECISION legibility, (4) real closed feedback loop. Plumbing/transparency/adapter pattern are
  solid; the intelligence layer is what's weak. Awaiting 'go' for Phase 4.

## Architecture
- [retrieval-pipeline.md](retrieval-pipeline.md) — claude-obsidian hybrid retrieval
  (contextual-prefix + BM25 + ollama cosine rerank); copy whole into `scripts/`; drives
  `wiki-retrieve` + `web-rank`; default path is $0/local.
- [skill-source-map.md](skill-source-map.md) — verified skill->source-repo map; `docs/skills-description.md`
  citations are accurate; key correction: think=CO, challenge+connect=OSB.
- [co-phase1-skills.md](co-phase1-skills.md) — per-skill PORT/ADAPT/DROP for CO Phase-1 skills +
  scripts (retrieval pipeline, wiki-lock, ingest minus DragonScale) + hooks + verifier template.
- [osb-phase1-pieces.md](osb-phase1-pieces.md) — OSB lift: vault_health/vault_stats, reconcile/synth/
  connect/challenge commands, validate-ai-first ASCII gate; we already have our own vault_health.py.
- [current-code-inventory.md](current-code-inventory.md) — exact v0.1 state: ingest_index v2 schema/
  verbs, vault_config, cost_tracker, claude_agent.sh usage-discard blocker, command->skill map.
- [live-vault-divergences.md](live-vault-divergences.md) — live Inference-Disagg vs frozen schema:
  daily/output/research-subfolders, meta files-vs-folders, frontmatter status-enum + bi-temporal
  conflict, concepts<->entities reversal, stray root page.

## Decisions
- [reference-decision.md](reference-decision.md) — claude-obsidian chosen as the backend
  implementation model; what to Copy/Take/Build-new/Drop. Full report: `plans/reference-comparison.md`.
- Two-layer write safety LOCKED (P0): Layer-2 wiki-lock.sh per-file (runtime, every skill via
  the locking.md snippet) + Layer-1 vault_lease.sh whole-vault cross-host git lease (nightly
  only, wired in P4). Lease policy: humans never block; only automated writers call acquire
  (--mode auto defers with exit 75). Details: [phase0-build.md](phase0-build.md).
- claude_agent.sh now CAPTURES usage (no longer `exec claude -p`): wraps --output-format json,
  records a cost_tracker row (source=agent-sdk-credit), re-emits only .result. --agent tags
  per-agent spend. Backward-compatible with nightly_run.sh. Details: phase0-build.md.
- _(append architecture decisions as they are made)_

## Audits
- Cost ledger now receives a row for EVERY agent-SDK claude call (P0.1). Per-agent attribution
  available via `--agent <id>` -> action tag; P4 will tag all nightly call sites + add the
  5h-window/window_tokens fields. No paid spend incurred building/testing Phase 0 (all hermetic).
- **HEALTH BASELINE (Inference-Disagg, 2026-06-19, post wiki-init):** score 0/100, 61 pages.
  After the dead-link resolver fix: orphaned=1, dead_links=170 (all genuine forward-references
  to not-yet-created pages), duplicate=1 (step-3 entity vs source - expected), missing_fm=1
  (stepfun-mfa), empty=1 (stepfun-mfa stub), untyped=1. Type split: entity 27 / concept 26 /
  source 6 / synthesis 1. Status: developing 40 / seed 19 / mature 1. The 0 score is a formula
  artifact (see structural proposal), not vault rot. Reports in meta/health_report/.
- **PAID SPEND (Phase 1):** exactly ONE metered call - the wiki-cite demo Gemini Flash
  validation judge, $0.000653 (542/196 tok, role=validation, source=pay-as-you-go). Everything
  else ($0): BM25/retrieval (local), pdf_extract (deterministic), health/stats (local). Budget
  (~$5 paid Anthropic) untouched; LiteLLM proxy is reachable on /v1/chat/completions (but
  /health + /v1/models 404 - probe the chat endpoint, not /health).

## Phase 2 plan
- [phase2-objective-seed.md](phase2-objective-seed.md) — objective/ graph SEEDED 2026-06-21:
  purpose + 8 topics + 7 RQs (merge+renumber) + D-0001. Vault commit baa3996. One-time
  RBAC bypass audit trail recorded here. next_id: topic=9, rq=8, decision=2.
- [phase2-plan.md](phase2-plan.md) — Phase 2 detailed action plan drafted 2026-06-21:
  objective/ node layout (7 types, id scheme Q-/T-/D-/DIR-/QP-/TODO-), objectives.py design
  (markdown-native scan/frontier/next_id/read_decisions), research agent def, 7 skills
  (obj-query/obj-synth/obj-reconcile/deep-synthesis/wiki-gaps/question-promote/question-solve),
  research/ layout + templates, seeding approach, 4-wave parallel plan, live demo, 8 open risks.
  Full draft at plans/phase-2-objectives.md.
- [phase2-demo.md](phase2-demo.md) — Phase 2 LIVE DEMO 2026-06-22 (code 5544c70, vault e3ad8b3):
  research+wiki subagents over the live vault validated dispatch+RBAC+reasoning end-to-end
  (obj-synth→5 directions+QP-0001; deep-synthesis→research/Q-0001.md; wiki-gaps→wiki/gaps.md).
  Fixed wiki_gaps_gather parenthetical-header bug (293 tests green). PHASE 2 COMPLETE: user
  signed off the demo; R8 reconcile DONE (vault 88494fe: research/notes/ + drop legacy dirs).
  Next = Phase 3, GATED on the Web DECISION grill-me. P5 flag: re-key nlm sync to research/notes/.

- [wiki-gaps-split.md](wiki-gaps-split.md) -- per-gap files DONE 2026-06-23: wiki/gap/GAP-NN-<slug>.md
  structure + wiki/gap/index.md; scripts/wiki_gaps_split.py (slugify/parse_analysis/split);
  scripts/templates/gap_template.md; SKILL.md updated (Step 5 pipes to splitter, Step 5b drops
  template, lock on index.md); wiki_gaps_fill.py build_gaps_frontmatter deprecated (kept for
  compat); web_decision.parse_gaps migrated to per-gap files (pre-existing change); 39 new tests +
  1 test_web_crawl assertion updated; 814 tests total, all green.

## Open structural proposals
- [reference-conflicts.md](reference-conflicts.md) — conflicts between the reference architectures
  and our v0.2 plan (path remapping, concepts<->entities swap to flag, RBAC/cost greenfield,
  research_deep + autoresearch RBAC rewrites, no cron in either repo).
- **Health score formula (ADR-style, awaiting user):** `vault_health.render_report` uses
  `score = max(0, 100 - 2*total_issues)`, so any vault with >50 issues floors to 0 -
  uninformative for a growing wiki whose dead-links are mostly legitimate forward-references
  (links to pages the wiki intends to create). Proposal: cap the per-category penalty (esp.
  dead_links), and/or split dead-links into "broken link to existing-then-removed page" (real)
  vs "link to never-created page" (backlog signal, lower weight). NOT implemented (scope guard
  #6). Decision needed before the score is used as a nightly gate.
- **Vault content findings for the wiki agent (not backend's to fix):** wiki/concepts/
  programming-latency.md has a Unicode "approximately equal" glyph (write-rules ASCII
  violation); stepfun-mfa.md (relocated) is missing type/created/updated/sources frontmatter +
  is a stub; 170 forward-reference pages are wanted but uncreated. Route to wiki-lint/wiki-cite.
- **docs/vault-schema.md needs user update (not backend's to write):** Two lines to drop +
  one convention to add: (1) drop the `templates/` folder entry from the vault tree (it is
  gone); (2) drop the `_CLAUDE.md` entry from the vault root (it is gone; agent rules are
  in the code repo; PURPOSE is in vault.yaml); (3) add a note that each producer folder
  carries a colocated `_template.md` (meta/health_report, meta/nightly_report, research/deep,
  research/query). docs/ is frozen for agents; route to user.
- _(append proposed structural updates awaiting user confirmation)_
