# Web-scrape fix P4 -- in-loop tuning experiment INFRASTRUCTURE (2026-07-08)

Plan: `C:\Users\kubap\.claude\plans\cuddly-meandering-pinwheel.md` (Phase 4). Built the
machinery for the human-in-loop tuning loop (the loop itself runs later, orchestrator-
driven). NOT committed. Branch worktree `.claude/worktrees/gracious-boyd-4d9ea4`. P0-2
were already committed (HEAD c31d63c).

## What was built

### Part A -- throwaway experiment vault `Disagg-Exp`
- Live vault `/mnt/c/Obsidian/Disagg-Exp` (isolated clone of Inference-Disagg; NEVER
  written back). Seeded so the 4 control targets are REFERENCED in-graph but NOT
  ingested (so the crawl has real targets to retrieve; ingest-dedup would drop them if
  ingested):
  - `objective/purpose/PURPOSE.md` (copied, `vault:` field -> Disagg-Exp; purpose =
    inference disaggregation, unchanged).
  - `wiki/gap/` = all OPEN top-level `GAP-*.md` from Inference-Disagg (21 files incl.
    index/_template) PLUS a hand-authored OPEN `GAP-11-splitwise...md` (status:open,
    `fillable_by: arxiv`, `## Missing` names "Splitwise ... arXiv 2311.18677") so the
    gap lane derives a Splitwise arXiv query. (Real GAP-11 lives in `closed/` and
    `parse_gaps` only globs top-level `GAP-*.md`, so I recreated it open.)
  - Citing pages copied (references exist in-graph, none are `sources/` pages):
    concepts {attention-ffn-disaggregation, prefill-decode-disaggregation,
    disaggregated-inference, heterogeneous-disaggregation, kv-cache-transfer,
    all-to-all-dispatch-combine, agentic-ai-workloads, deployment-plan-search};
    entities {splitwise, gimlet-labs, zain-asgar, stepfun, bytedance-seed, distserve,
    deepep, deepseek-v3}.
  - 2 directions {DIR-0004 optical-prior-art, DIR-0005 3tier-dispatch-combine} for the
    research lane.
  - Fresh EMPTY `meta/ingest_index.json` (`{"schema_version":3,"sources":{}}`) -> zero
    target entries -> all four count as not-ingested. No seen cache.
  - Deliberately NOT copied: `wiki/sources/<target>.md` (megascale-infer, asgar-2507,
    splitwise-2311) and `raw/papers/*.pdf` -> targets look un-ingested.
- Registered: `config/vaults/Disagg-Exp/{vault,topics,budget}.yaml` (WORKTREE repo).
  budget cap 0.0 (must stay $0). Added `Disagg-Exp enabled:false` to
  `config/secondbrain.yaml` vaults registry (nightly VAULT=all skips it).
- Resolution VERIFIED: `CODE_PATH=<worktree> VAULT=Disagg-Exp python -m
  agents.vault_config path` -> `/mnt/c/Obsidian/Disagg-Exp`. NOTE the CODE_PATH pin:
  vault_config's default CODE_PATH is the MAIN repo `/home/jpietrak/second_brain`; the
  config lives in the WORKTREE, so callers running from the worktree must set
  `CODE_PATH=$(pwd)`. The eval harness does this automatically (os.environ.setdefault).

### Part B -- `scripts/experiments/targets.json`
Ground-truth manifest: present_in_vault [megascale 2504.02263, gimlet/asgar 2507.19635,
splitwise 2311.18677]; reachability [chiplog FFN-disagg post, via websearch].

### Part C -- `scripts/experiments/eval_retrieval.py`
- Loads targets + an EXPERIMENT config copy `scripts/experiments/exp-config.json` (copy
  of `.claude/web/web-config.json`; tuning mutates THIS, never the shared config).
- `run_crawl`: monkeypatches `web_crawl._load_config` to return the exp-config dict,
  then `web_crawl.crawl(vault_root=Disagg-Exp, dry_run=True, per_source_limit=limit,
  agent_candidates_path=<chiplog fixture>)`; restores `_load_config` in a finally.
- chiplog injected via the Phase-2 `--agent-candidates` path from fixture
  `scripts/experiments/chiplog-candidate.json` (scripts can't call real WebSearch).
- `evaluate`: ranked pool read from `trace.find("rank","scores")[-1].data.candidates`
  (list of {ident,score}), sorted by score desc -> rank (1-based). Matching: arXiv
  targets match by bare number ignoring `vN` version suffix; reachability by normalised
  URL. selected idents via `web_harvest.candidate_ident` over the <=5 selected dicts.
  recall@5 = selected_targets / total_targets. Prints scorecard; `--json` emits JSON.
- CWD-independent (paths off `__file__`). $0, bounded by `--limit` (default 3).

### Part D -- baseline
`scripts/experiments/baseline.md`. **recall@5 = 0.25 (1/4)**, pool 181, 5 selected:
- Chiplog: found rank 1, SELECTED (Phase-2 injection + Phase-1 reweight works E2E).
- Splitwise 2311.18677: FOUND rank 21 (via open GAP-11 arXiv query) but NOT selected
  -> primary hill for the loop (rank it into top-5).
- MegaScale 2504.02263 + Gimlet/Asgar 2507.19635: NOT retrieved at all -- no seeded gap
  names them precisely; deterministic arXiv queries miss them. Genuine retrievability
  gap (needs WebSearch injection / targeted query / web-backfill), not a harness bug.

## Tests
`tests/test_eval_retrieval.py` -- 4 OFFLINE tests (canned plan/selected/trace via real
DecisionTrace; run_crawl with web_crawl.crawl monkeypatched). 4 passed 0.25s. Full suite
NOT run (avoid long/network). Did NOT run git.

## Files (all worktree)
- config/vaults/Disagg-Exp/{vault,topics,budget}.yaml ; config/secondbrain.yaml (+1 entry)
- scripts/experiments/{targets.json, exp-config.json, chiplog-candidate.json,
  eval_retrieval.py, baseline.md}
- tests/test_eval_retrieval.py
- vault seeded on disk at /mnt/c/Obsidian/Disagg-Exp (NOT in git; Windows Obsidian dir)

## For the loop (next, orchestrator-driven)
Change ONE knob per iteration in exp-config.json and/or web_rank.py (category_weights,
W_KW/W_REL/W_REC, reformulate, per_source_limit, lane quotas), applied to Disagg-Exp
ONLY, re-run the harness, log config->recall, hill-climb toward recall@5=1.0. Two targets
(megascale, gimlet) likely need injected WebSearch candidates or a backfill pass to be
retrievable at all -- flag to user before over-tuning ranking for them.
