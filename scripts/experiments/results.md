# web-scrape retrieval eval -- RESULTS (Phase-4 in-loop tuning)

The Phase-4 loop tunes the web-scrape retrieval stack against the seeded throwaway vault
`Disagg-Exp`. The reward is **recall@5 over a fixed 4-target set** (3 papers cited in the
graph but not yet ingested + 1 reachability blog):

- `arxiv:2504.02263` MegaScale-Infer  (present_in_vault)
- `arxiv:2507.19635` Gimlet/Asgar     (present_in_vault)
- `arxiv:2311.18677` Splitwise        (present_in_vault)
- chiplog Attention/FFN Disaggregation blog (reachability, via WebSearch injection)

A `present_in_vault` target counts as **retrieved** if its id is in the crawl-selected
top-N OR in the deterministic `web_backfill.build_backfill` SET (set membership = backfill
would surface it; the per-run `--limit` is just batching). Reachability targets are
measured via the crawl+WebSearch-injection path only. Runs are `$0` (free arXiv/S2 APIs +
local/offline rank, `dry_run=True`), bounded by `--limit 3`. Tuning mutates the isolated
`scripts/experiments/exp-config.json`, never the shared `.claude/web/web-config.json`.

## Matrix

| # | config | recall@5 | retrieved | note |
|---|--------|:--------:|:---------:|------|
| 1 | baseline (crawl-only, `apply_to_embedding=false`) | **0.25** | 1/4 | only chiplog selected |
| 2 | +backfill (`apply_to_embedding=false`)            | **1.00** | 4/4 | 3 papers via backfill set |
| 3 | +backfill +`apply_to_embedding=true` (WINNER)     | **1.00** | 4/4 | + tidies crawl-lane NVIDIA ranks |

### Per-target (found / rank / selected / backfill -> retrieved)

| target | config 1 | config 2 | config 3 (winner) |
|--------|----------|----------|-------------------|
| MegaScale-Infer `2504.02263` | not found; miss | not found by crawl; **backfill -> retrieved** | not found by crawl; **backfill -> retrieved** |
| Gimlet/Asgar `2507.19635`    | not found; miss | not found by crawl; **backfill -> retrieved** | not found by crawl; **backfill -> retrieved** |
| Splitwise `2311.18677`       | found r21, not selected; miss | found r21 (not selected); **backfill -> retrieved** | found r23 (not selected); **backfill -> retrieved** |
| Chiplog blog (reachability)  | found r1, **SELECTED** | found r1, **SELECTED** | found r2, **SELECTED** |

Pool size ~182 ranked candidates in every config. In config 3 the target ranks shift
slightly (splitwise 21->23, chiplog 1->2) because the category reweight reshuffles the
crawl lane, but the 4/4 retrieval is unchanged.

### NVIDIA crawl-lane ranking under `apply_to_embedding`

The `category_weights` knob only reweights the deterministic score by default; setting
`apply_to_embedding: true` also applies `vendor_blogs: 0.5` on the live embedding rank
path, which demotes NVIDIA vendor blogs (`blogs.nvidia.com` / `developer.nvidia.com`):

- **baseline (off):** the top NVIDIA vendor blog (`nvidia-vera-max...`) sat at **rank 20**
  (score 0.6301); other developer.nvidia.com blogs at ranks 25/40/50/61/92/139.
- **`apply_to_embedding=true`:** every NVIDIA *vendor blog* sinks to the **bottom of the
  pool (ranks 177-182**, scores ~0.28-0.32); the one legitimately-relevant SemiAnalysis
  article that merely mentions "nvidia" rises to **rank 1** (score 0.7128).

So yes -- the lone NVIDIA vendor blog at rank ~20 in the baseline drops to rank 177, while
genuine analysis is not penalized. This does not change target recall (still 1.00) but
cleans the news/crawl lane.

## Conclusion

- **Backfill is the dominant lever.** It deterministically surfaces the three cited-but-not-
  ingested known papers (MegaScale, Gimlet, Splitwise), taking `present_in_vault` recall
  from 0/3 (crawl-only) to **3/3**. The crawl's gap/direction queries only ever reached
  Splitwise (rank 21, unselected) and never MegaScale/Gimlet -- backfill closes that gap
  by construction, not by ranking luck.
- **Chiplog is handled by the crawl + WebSearch-injection path** (selected at rank 1-2 in
  every config), so the two lanes are complementary: backfill for known-cited papers,
  crawl+WebSearch for reachable-but-uncited sources.
- **`apply_to_embedding=true` tidies the crawl lane's NVIDIA ranking** (vendor blogs
  rank ~20 -> 177) with no cost to recall.
- **Final recall reached: recall@5 = 1.00 (4/4)** with the winning config
  (+backfill +`apply_to_embedding=true`).

## Winning config + promotion recommendation

`scripts/experiments/exp-config.json` is set to the winner (`apply_to_embedding: true`).

Recommended for promotion to the shared `.claude/web/web-config.json` (NOT changed here --
owner's call):

1. **`category_weights.apply_to_embedding: true`** -- worth promoting. It demotes vendor
   blogs on the live embedding rank path with no downside to target recall; genuine
   analysis (e.g. SemiAnalysis) still ranks top.
2. **Run `/web-backfill` before exploratory crawls.** Backfill is already a shipped
   standalone command (`scripts/web_backfill.py`), not a crawl config knob, so there is
   nothing to promote in the config -- just adopt the workflow: backfill the known-cited
   papers first, then crawl for discovery.

The other exp-config knobs (lane quotas 3/1/1, `query.reformulate: false`,
`registry_boost 0.25`, `vendor_blogs 0.5`) already match the shared config and need no
change.
