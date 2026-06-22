---
name: web-rank
description: >
  Rank web candidates by relevance to vault PURPOSE and target expected evidence.
  Uses local ollama embedding (nomic-embed-text cosine similarity) with deterministic
  offline fallback (keyword + relevance + recency heuristics). Zero cost, completes in <5s per batch.
allowed-tools: Read, Bash
---

**Ownership: `web` agent.** If you are NOT the `web` subagent (e.g. the main orchestrator or
another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: web`,
pass the user's full request, let the web agent run the steps below, and relay its result.
Do NOT run the steps yourself - running as the `web` agent is what activates the RBAC/write-scope
boundary. If you ARE the `web` agent, proceed.

# web-rank: local relevance scoring for candidates

Owner: **web**. This skill ranks web candidates discovered by `web-scrape` (step 5) by
semantic relevance to the vault PURPOSE statement and the specific target's expected evidence.
Scoring is fast ($0), local-only: ollama embedding (cosine similarity) when available, else
deterministic keyword + relevance + recency fallback.

## When to use

Called by `web-scrape` at step 5 for each batch of candidates harvested per target. Input is
a list of candidate dicts from `web_harvest.py`; output is the same list with a `score` field
(0.0-1.0) added to each, sorted by descending score.

## Interface (authoritative)

Normally `web-rank` is invoked **internally by `web_crawl.py`** (you do not call it by hand in the
nightly flow). For manual/debug use it reads a JSON LIST of candidates on **STDIN** and writes the
scored list to STDOUT:

```bash
cat /tmp/candidates.json | .venv/bin/python scripts/web_rank.py \
  --query "<PURPOSE + the target's expected_evidence>" \
  --registry .claude/web/sources \
  > /tmp/ranked.json
# add --allow-remote-ollama to permit a non-localhost OLLAMA_URL
```

- **Input:** a bare JSON array of candidate dicts (`{title,url,source_id,id_type,published,
  snippet,engine}`) — NOT a `{target_id, candidates:[...]}` wrapper.
- **Output:** the same array, each candidate gaining a `score` float, sorted desc. No `rank` field.
- `--registry` (the `.claude/web/sources/` dir) lets the fallback add a relevance bonus by
  matching a candidate's `source_id` to a registry entry.

## How it works

### Input: candidates JSON

```json
{
  "target_id": "GAP-0042",
  "candidates": [
    {
      "url": "https://...",
      "title": "Paper: ...",
      "source": "arxiv",
      "snippet": "Abstract...",
      "published_date": "2025-06-01",
      ...other metadata...
    },
    ...
  ]
}
```

### Query string

Combine PURPOSE (from `objective/purpose/PURPOSE.md`) with target's `expected_evidence`
(from the direction node or gap description). Example:

```
PURPOSE: Improve distributed inference scalability.
EXPECTED_EVIDENCE: techniques for distributed model inference, load balancing, tensor parallelism.
```

### Ranking strategy

#### Path 1: Local ollama embedding (preferred, <5s)

If `ollama pull nomic-embed-text` has been run (model available locally):

1. Embed the combined query string via ollama: `ollama embed nomic-embed-text "<query>"`.
2. Embed each candidate's (title + snippet) via the same model.
3. Compute cosine similarity between query embedding and each candidate embedding.
4. Normalize scores to [0.0, 1.0] range (scale by max + clamp).
5. Sort candidates by descending score; assign rank 1, 2, 3, ... .
6. Return JSON with `score` and `rank` fields added.

Cost: zero (local inference, ~50-200ms per candidate depending on hardware).

#### Path 2: Offline fallback (no ollama, deterministic, <100ms)

If ollama is not available or the model is not pulled:

1. **Keyword matching:** count how many expected_evidence keywords appear in title + snippet.
   (Split evidence by whitespace and punctuation; case-insensitive.)
   Score component: `keyword_matches / total_keywords` (0.0-1.0).

2. **Relevance heuristics:**
   - Source authority: arxiv=0.8, papers=0.8, reddit=0.5, news=0.4, github=0.6.
   - Snippet length: prefer non-empty snippets (longer = more likely substantive). Bonus +0.1
     if snippet > 100 chars.

3. **Recency bonus:** prioritize recent sources over old (published_date > 1 year ago = -0.1;
   < 3 months ago = +0.1; recency_bonus in [-0.1, 0.1]).

4. **Combine:** `score = 0.6 * keyword_matches + 0.3 * source_authority + 0.1 * recency_bonus`.
   Clamp to [0.0, 1.0].

5. Sort and rank as above.

Cost: deterministic, no LLM calls, no web access. <100ms for 100 candidates.

### Output: ranked JSON

```json
{
  "target_id": "GAP-0042",
  "scored_candidates": [
    {
      "url": "https://...",
      "title": "Paper: ...",
      "source": "arxiv",
      "snippet": "Abstract...",
      "published_date": "2025-06-01",
      ...original metadata...,
      "score": 0.87,
      "rank": 1,
      "ranking_method": "ollama_nomic" | "offline_fallback"
    },
    ...
  ]
}
```

## Setup: enable embedding path

To enable the faster ollama path (recommended for repeated crawls):

```bash
ollama pull nomic-embed-text
```

This is a ~300MB download (one-time). The script detects availability and auto-falls back
if missing. No action needed to use fallback; it activates automatically.

Verify:
```bash
ollama list | grep nomic-embed-text
```

## Cost

- **Ollama path:** $0 (local inference)
- **Offline fallback:** $0 (deterministic heuristics)
- No paid LLM calls, no web access, no API quotas consumed

Total per-batch cost: <5s wall-clock, zero credits.

## Conventions

- Scores in [0.0, 1.0]; higher = more relevant.
- Rank starts at 1 (top candidate).
- Ranking method is logged in output (debugging: was this ollama or offline?).
- All candidate fields are passed through (no loss of metadata).
