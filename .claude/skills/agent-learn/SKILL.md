---
name: agent-learn
description: >
  Apply accept/reject outcomes to the per-source/engine reputation model and
  reject-keyword patterns stored in .claude/memory/<agent>/learned.json. Surfaces
  a ## Learning briefing section at the top of each nightly crawl report.
  Triggers on: "agent-learn", "update reputation", "learning briefing",
  "apply learning", "show learned", "what has the web agent learned".
allowed-tools: Read, Bash
---

**Ownership: `web` agent (dispatched as subagent_type: web).** If you are NOT the
`web` subagent, DISPATCH it: call the Task tool with `subagent_type: web`, pass the
user's full request, let the web agent run the steps below, and relay its result.
Do NOT run the steps yourself.

# agent-learn: Reputation Learning Core

> ## For future Claude
> agent_learn.py turns human approve/reject decisions stored in the ingest index
> into durable per-source and per-engine reputation scores, reject-keyword patterns,
> and score calibration. Results live in .claude/memory/<agent>/learned.json.
> The ranking and routing layers read learned.json on every crawl. Nothing is hidden:
> every learned.json update is summarised in the ## Learning briefing section at the
> top of the nightly crawl report.
> ai-first: true

---

## What agent-learn does

agent-learn turns accept/reject outcomes into three durable signals:

1. **Per-source reputation** (`sources.<source_id>.rep` in [-1, +1]): smoothed
   accept-vs-reject ratio for each registry source id. A source with rep=+0.6 ranks
   higher in routing and scoring; rep=-0.6 ranks lower and triggers the reject penalty.

2. **Per-engine reputation** (`engines.<engine>.rep`): same smoothing applied to
   the harvest engine ("arxiv", "rss", "hackernews", ...) when no source-level rep
   exists. Falls back to 0.0 (neutral) when neither source nor engine has been seen.

3. **Reject-keyword patterns** (`reject_patterns.keywords`): tokens extracted from
   human rejection reasons, filtered through English and domain stopwords. Candidates
   whose title/snippet contain a learned keyword receive the reject penalty.

**Score calibration** tracks the running mean accepted/rejected score so future
improvements can use the distribution gap for threshold tuning.

**Idempotency**: each ingest row is counted once (tracked in `processed_ids`). Re-runs
are safe; only NEW decisions since the last run are applied.

---

## How it is wired (automatic, no approval step)

web_crawl.py runs agent_learn automatically at the START of every crawl:

1. Reads ALL ingest index rows discovered by the `web` agent.
2. Filters to newly-decided rows (status: ingested/pending = ACCEPT; rejected = REJECT).
3. Updates per-source and per-engine reputation, extracts reject keywords, updates calibration.
4. Writes `.claude/memory/web/learned.json` (unless `--dry-run`).
5. Returns a `## Learning briefing` markdown summary of what changed.

web_crawl embeds that briefing at the TOP of the nightly report (before the first
candidate block). The ## Decision trace section stays at the bottom; report_approve.py
parsing is unaffected (it keys on candidate checkbox lines and stops at ## Decision trace).

The ranking layer (web_rank.score_candidates) adds the learned prior to every score:

  score = base + w_rep * rep_for(source_id, engine) - w_rej * reject_penalty(candidate)

Default weights (from web-config learn block): w_rep=0.15, w_rej=0.2.

The routing layer (web_decision.route_to_sources) adds rep_for to the sort key so
higher-reputation sources are routed first within each fillable_by class.

When `learn.enabled=false` in web-config.json OR agent_learn raises, the crawl
degrades gracefully (learned={}, briefing="") with no crash.

---

## Learned.json location and schema

Path: `.claude/memory/web/learned.json` (in the code repo, not the vault)

```json
{
  "updated": "2026-06-24",
  "sources": {
    "arxiv_cs_dc": {"accept": 3, "reject": 1, "rep": 0.333333}
  },
  "engines": {
    "arxiv": {"accept": 5, "reject": 2, "rep": 0.142857}
  },
  "reject_patterns": {
    "keywords": ["clickbait", "sponsored"]
  },
  "calibration": {
    "accepted_score_mean": 0.72,
    "rejected_score_mean": 0.41,
    "n": 12
  },
  "processed_ids": ["arxiv:2401.10001", "arxiv:2401.10002"]
}
```

rep formula (prior_strength=1.0):
  p   = (accept + 1) / (accept + reject + 2)
  rep = 2*p - 1   -- cold-start = 0.0, range [-1, +1]

---

## Reusability

agent-learn is reusable across agents. The `agent` parameter selects the memory
directory (`.claude/memory/<agent>/learned.json`) and the `discovered_by` filter
on ingest rows. Future wiki and research agent instances use the same module:

  python scripts/agent_learn.py --vault <V> --agent wiki [--dry-run] [--json]

---

## Manual invocation

Run from the repo root (the venv must be active):

```bash
VAULT_ROOT="$(.venv/bin/python -m agents.vault_config path)"

# Show what would change (no write):
.venv/bin/python scripts/agent_learn.py --vault "$VAULT_ROOT" --agent web --dry-run

# Apply and print briefing:
.venv/bin/python scripts/agent_learn.py --vault "$VAULT_ROOT" --agent web

# Apply and dump full JSON result:
.venv/bin/python scripts/agent_learn.py --vault "$VAULT_ROOT" --agent web --json
```

Exit codes: 0 = success, 2 = usage error.

---

## What not to do

- Do not manually edit learned.json to adjust reputations -- re-approve/re-reject the
  relevant ingest rows and re-run agent_learn instead.
- Do not call agent_learn with `apply=True` during a dry-run crawl (web_crawl handles
  this automatically: dry-run -> apply=False).
- Do not worry about the ## Learning briefing section when parsing reports -- report_approve.py
  already stops at ## Decision trace and keys on the candidate checkbox lines, so the
  briefing section does not affect parsing.
