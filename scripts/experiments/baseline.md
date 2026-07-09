# web-scrape retrieval eval -- BASELINE (Disagg-Exp)

Baseline scorecard for the Phase-4 in-loop tuning experiment. Produced by
`scripts/experiments/eval_retrieval.py` against the seeded throwaway vault
`Disagg-Exp` with the Phase-0/1/2 code and the starting experiment config
(`scripts/experiments/exp-config.json`, a copy of the production web-config).

- Date: 2026-07-08
- Command: `CODE_PATH=<worktree> python scripts/experiments/eval_retrieval.py --limit 3`
- Cost: $0 (free arXiv/S2 APIs for harvest + local/offline rank; `dry_run=True`).
- Timing: single dry-run crawl over the free paper APIs; completed within the 150s
  bound. The chiplog reachability target is injected from the fixture
  `scripts/experiments/chiplog-candidate.json` (scripts cannot call the real WebSearch).

## Result

```
pool size  : 181 ranked candidates
selected   : 5 (crawl top-N)

RECALL@5   : 0.25  (1/4 targets selected)

target                     kind              found  rank  selected
------------------------------------------------------------------
MegaScale-Infer            present_in_vault  no     -     no
Gimlet/Asgar               present_in_vault  no     -     no
Splitwise                  present_in_vault  yes    21    no
Chiplog Attention/FFN Dis  reachability      yes    1     YES
```

Target keys:
- MegaScale-Infer: `arxiv:2504.02263`
- Gimlet/Asgar: `arxiv:2507.19635`
- Splitwise: `arxiv:2311.18677`
- Chiplog: `https://www.chiplog.io/p/inside-attention-ffn-disaggregation`

## Reading of the baseline

- **recall@5 = 0.25**: only the injected chiplog reachability target lands in the
  selected top-5 (rank 1). The Phase-2 WebSearch-injection + Phase-1 category reweight
  path works end to end: an on-topic blog URL is discoverable, rankable, and selectable.
- **Splitwise (`2311.18677`) is FOUND but not SELECTED (rank 21).** The seeded open
  GAP-11 (`fillable_by: arxiv`, title names "Splitwise ... arXiv 2311.18677") drives an
  arXiv query that returns the paper into the pool of 181, but it is out-ranked by 20
  other candidates and misses the 5 slots (3 gap / 1 research / 1 news). This is the
  primary hill for the tuning loop to climb.
- **MegaScale-Infer (`2504.02263`) and Gimlet/Asgar (`2507.19635`) are NOT retrieved
  at all** by the current gap/direction queries. No seeded gap names them precisely
  enough for the deterministic arXiv query to surface them (GAP-13/GAP-12/GAP-19 are
  thematically adjacent but do not name the papers). Expected: these need either a
  WebSearch-injected candidate (as chiplog was) or a more targeted query / a
  `/web-backfill` pass. This is a genuine retrievability gap, not a harness bug -- it is
  exactly the kind of signal the loop is meant to expose.

## Levers available to the loop (from the plan's search space)

- `category_weights` (demote/boost by source class) -- e.g. lift `preprint_repository`
  / `paper_api` so found-but-low-ranked papers like Splitwise climb toward the top-5.
- `W_KW / W_REL / W_REC` in `web_rank.py` (keyword vs relevance vs recency weights).
- `reformulate` on/off; `per_source_limit` (wider pool, more coverage).
- gap-query precision (seed gaps that name MegaScale / Gimlet), or additional
  WebSearch-injected candidates for the two un-retrieved targets.
- lane quotas (`gap`/`research`/`news`) -- more gap slots would give ranked-but-unselected
  papers like Splitwise a better chance.

The loop changes exactly ONE knob per iteration (in `exp-config.json` and/or
`web_rank.py`, applied to Disagg-Exp only), re-runs this harness, and records
config -> recall to hill-climb toward recall@5 = 1.0.
