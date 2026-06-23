# Phase 3B (start): Perplexity Tier-2 engine + approval status-persistence fix (2026-06-23)

Commits (NOT pushed): `bbc1758` status fix, `43ca7a1` Perplexity. 939 tests (`pytest tests/`).

## Approval status-persistence FIX (bbc1758)
- BUG (user-reported after a real `/wiki-approve`): approve->pending / reject->rejected did NOT persist to the live `meta/ingest_index.json`.
- ROOT CAUSE: `report_approve.apply` routed `ingest_index` via a fragile `VAULT_PATH` env trick + `name=None`. The wiki subagent process runs with `VAULT=Inference-Disagg` set, which clobbered the resolution -> load/save hit different/registered paths, so the flip was lost.
- FIX: added an explicit `root=` kwarg to `ingest_index._load/_save/render_md/approve/reject`; `report_approve` passes `root=Path(vault_root)` everywhere (approve/reject/url-gate/backlog) -> **load==save at vault_root**, independent of VAULT/VAULT_PATH/registration. +`TestLiveVaultPersistence`. **Lesson: resolve the ingest store from the explicit vault_root, never from process env.**

## Perplexity = gated Tier-2 HARVEST engine (43ca7a1) — SHARED BACKEND
User constraint: do NOT rewrite decision/gap-analysis/ranking for Perplexity. Achieved: Perplexity adds candidates at HARVEST only; **decision/merge/lanes/route + web_rank + select_candidates + stage are 100% reused**.
- `web_harvest.query_perplexity(query, *, limit=8, model=None)`: reuses `scripts/research/lib/perplexity.py` (`call()` ALREADY returns `citations`); maps each citation -> candidate `{engine:"perplexity", url, id_type: arxiv/doi/url (so it dedupes vs free arxiv), source_id, snippet=answer slice}`. Returns `[]` if no `PERPLEXITY_API_KEY`; never spends in tests (mocked).
- `web_crawl --perplexity` (skill arg `/web-scrape perplexity`): GATED — requires `paid_scrape.enabled=true` AND `PERPLEXITY_API_KEY`, else refuses (exit 2, NO spend, no free fallback). Allocates the top `max_calls_per_crawl` (2) targets by priority (gap lane first; `# TODO smarter schedule TBD`); tags candidates with lane/origin_ids/expected_evidence; **one `cost_tracker.record(action="web-scrape-perplexity", provider="perplexity", model="sonar", source="pay-as-you-go")` row per call**; trace records engine=perplexity. Candidates join the SAME pool.
- `.claude/web/web-config.json` `paid_scrape={enabled:false, max_calls_per_crawl:2, engines:["perplexity"]}` — **enabled FALSE by default (no spend until the user flips it).**

## To test with real $ (user, when ready, FRUGAL — ~$5 budget)
1. `export PERPLEXITY_API_KEY=...` (Perplexity key; distinct from the reserved ANTHROPIC_API_KEY).
2. set `paid_scrape.enabled: true` in `.claude/web/web-config.json`.
3. `/web-scrape perplexity` (or `python scripts/web_crawl.py --vault <V> --perplexity`). Spends <=2 Sonar calls, cost-logged.
Gate verified to REFUSE (no spend) with enabled=false.

## STILL PENDING in Phase 3B
- **Live real-$ Perplexity test** (above) — not yet run (no key/enabled in the build env).
- **Apify / crawl4AI** adapters — same Tier-2 harvest-engine pattern (drop-in like query_perplexity), not built.
- Smarter paid-call scheduling (currently top-2-by-priority + manual flag) — TBD.
