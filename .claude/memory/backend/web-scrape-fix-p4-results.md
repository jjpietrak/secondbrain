# Web-scrape fix P4 -- in-loop tuning RESULTS (2026-07-09)

Phase 4 of the web-scrape work FINALIZED (infra was built earlier; see
`web-scrape-fix-p4-experiment.md`). Worktree `.claude/worktrees/gracious-boyd-4d9ea4`.
NOT committed (orchestrator handles git). All runs `$0`, `dry_run=True`, `--limit 3`.

## Harness change (Task 1)
`scripts/experiments/eval_retrieval.py` gained a `--with-backfill` flag.
- `_compute_backfill_ids(vault_root)` calls `web_backfill.build_backfill` (OFFLINE -- only
  scans wiki + ingest index; `fetch_arxiv_by_id` is the only network call and is NOT used)
  and returns `{b["id"] for b in res["backfill"]}` (SET membership, not the `--limit`-capped
  "would stage" list).
- `evaluate(result, targets, backfill_ids=None)`: a `present_in_vault` target is RETRIEVED
  if crawl-selected OR in the backfill set; reachability targets are crawl-only (backfill
  n/a). New row fields `backfill` + `retrieved`; report gains `n_retrieved_targets` +
  `with_backfill`. `recall_at_5 = n_retrieved / n_targets`.
- +1 hermetic test `test_evaluate_with_backfill_lifts_present_in_vault` (5 tests total, green).
- Verified: `build_backfill(Disagg-Exp)` backfill set has 16 ids incl. all 3 targets
  (2504.02263, 2311.18677, 2507.19635) -- because `web_backfill` now catches TEXTUAL arXiv
  citations (`arXiv 2504.02263`), not just `arxiv.org/abs/` URLs.

## Matrix (Task 2) -- reward = recall@5 over the 4-target set
| config | recall@5 | note |
|--------|:--------:|------|
| baseline (crawl-only) | 0.25 | only chiplog selected (rank 1) |
| +backfill | 1.00 | 3 papers retrieved via backfill set |
| +backfill +apply_to_embedding=true (WINNER) | 1.00 | + demotes NVIDIA vendor blogs on embedding path |

Per-target under winner: MegaScale + Gimlet not in crawl pool -> retrieved via backfill;
Splitwise found crawl rank 23 (unselected) -> retrieved via backfill; Chiplog crawl rank 2,
SELECTED. Pool ~182.

## NVIDIA rank (Task 2 detail)
`category_weights.apply_to_embedding` default only reweights the deterministic score; set
true it also applies `vendor_blogs:0.5` on the LIVE EMBEDDING rank path. Effect:
- baseline (off): top NVIDIA vendor blog `nvidia-vera-max` at RANK 20 (score 0.6301);
  developer.nvidia.com blogs at 25/40/50/61/92/139.
- true: all NVIDIA vendor blogs sink to ranks 177-182 (scores ~0.28-0.32); the legit
  SemiAnalysis "nvidia-gpu-debt" article rises to RANK 1 (0.7128). No change to recall.

## Winning config (Task 3)
`scripts/experiments/exp-config.json` set to `category_weights.apply_to_embedding: true`
(only knob changed from the baseline copy). Confirmed via the shipped CLI:
`eval_retrieval.py --limit 3 --with-backfill` -> recall@5 = 1.00 (4/4). Other exp-config
knobs already match the shared config (lanes 3/1/1, reformulate:false, registry_boost 0.25,
vendor_blogs 0.5). `baseline.md` left as the historical 0.25 record; `results.md` written.

## Promotion recommendation (NOT applied -- shared config not touched by me)
1. Promote `category_weights.apply_to_embedding: true` to `.claude/web/web-config.json`:
   demotes vendor blogs on the embedding path, zero recall cost, genuine analysis still tops.
2. Backfill is a shipped standalone command (`/web-backfill`), not a config knob -- adopt the
   WORKFLOW (backfill known-cited papers first, then crawl for discovery). Nothing to promote.

## Files (all worktree, NOT committed)
- scripts/experiments/eval_retrieval.py (+--with-backfill, _compute_backfill_ids, evaluate arg)
- scripts/experiments/exp-config.json (apply_to_embedding true)
- scripts/experiments/results.md (new)
- tests/test_eval_retrieval.py (+1 test, 5 total green)
- scripts/experiments/baseline.md unchanged (historical 0.25)
