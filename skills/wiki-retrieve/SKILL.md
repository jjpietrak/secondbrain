---
name: wiki-retrieve
description: >
  Provision and run the hybrid retrieval pipeline over the wiki vault (BM25 over
  contextualized chunks, then an Ollama cosine rerank). Run setup once via
  scripts/setup-retrieve.sh to build .vault-meta/{chunks,bm25}; query via
  scripts/retrieve.py to get the most relevant pages with absolute paths and
  snippets. Embeddings + rerank run LOCALLY on Ollama (bulk, $0); BM25 always
  works even when Ollama is absent (rerank no-ops). This skill is consumed by
  wiki-query (it consumes; this builds + serves). Triggers on: "set up retrieval",
  "build the search index", "provision wiki-retrieve", "reindex the wiki",
  "rebuild the index", "/wiki-retrieve", "retrieve from wiki", "search index".
allowed-tools: Read Glob Grep Bash
---

# wiki-retrieve: provision + serve hybrid retrieval

Owner: **wiki**. Two jobs:

1. **Provision** the pipeline once (and re-provision after a full re-ingest or schema
   change): `bash scripts/setup-retrieve.sh` builds `.vault-meta/chunks/` (contextual-prefix
   chunks) + `.vault-meta/bm25/index.json` (inverted index) under the ACTIVE vault.
2. **Serve** queries: `python scripts/retrieve.py "<query>"` returns ranked candidate pages
   as JSON (absolute paths + snippets + bm25/rerank scores) for a caller to Read + synthesize.

This skill builds and serves the index; `wiki-query` CONSUMES it. Follows
`references/ai-first-rules.md` and `references/write-rules.md`. ASCII only.

## LLM routing (local + free by default)
- Embeddings + rerank -> **Ollama `bulk`** (`nomic-embed-text`, cosine), localhost only, $0.
- Contextual-prefix chunk context -> **tier-3 synthetic (on-machine), the default**. Egress
  tiers (Anthropic API / claude CLI) are opt-in ONLY via `--allow-egress` + explicit consent.
- No metered/paid route is used by this skill.

## Step 0 - resolve the vault (never hard-code a path)
```bash
eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
```
`.vault-meta/` lives under `$VAULT_ROOT` (the active vault), NOT in the code repo. The
retrieval scripts themselves are read from the code repo's `scripts/` dir.

## Provision (run once, then after re-ingest)
```bash
bash scripts/setup-retrieve.sh           # default: tier-3 synthetic prefix ($0/local), BM25 build
bash scripts/setup-retrieve.sh --check   # diagnostics only; no provisioning
bash scripts/setup-retrieve.sh --rebuild # rebuild all chunks (after a big content change)
bash scripts/setup-retrieve.sh --no-llm  # force synthetic prefix, skip the egress prompt
```
Idempotent: `body_hash` skips already-processed chunks, so a re-run only touches changed
pages. The script (1) sanity-checks the four helper scripts, (2) makes `.vault-meta/{chunks,
bm25}`, (3) probes Ollama (informational), (4) runs `contextual-prefix.py --all`, (5) builds
the BM25 index, (6) smoke-tests `retrieve.py`.

### Ollama posture
- Ollama reachable + `nomic-embed-text` pulled -> rerank uses cosine (best quality).
- Ollama absent or model not pulled -> **rerank no-ops; BM25-only retrieval still works**
  (the pipeline degrades, it does not fail). To enable rerank later:
  `ollama pull nomic-embed-text` then re-query (no re-provision needed).
- `OLLAMA_URL` pointing off-localhost is REFUSED unless `--allow-remote-ollama` is passed
  (mirrors `rerank.py`'s localhost gate). Default is local-only.

## Serve (query the index)
```bash
python scripts/retrieve.py "<the question verbatim>" --top 5
python scripts/retrieve.py "<query>" --top 10 --explain   # deep mode + per-stage diagnostics
python scripts/retrieve.py "<query>" --no-rerank          # BM25-only (skip rerank stage)
```
Output JSON has a `candidates` array; each candidate carries `absolute_path` (Read it),
`page_path`, `snippet`, `bm25_score`, `rerank_score`, and `rerank_source`. `strategy` reports
which path ran (`bm25+rerank:cosine:nomic-embed-text`, `bm25+noop-rerank`, or `bm25-only`).

## Exit-10 fallback (required of every consumer)
`retrieve.py` exits **10** when the pipeline is NOT provisioned (no `.vault-meta/bm25/
index.json` or empty `.vault-meta/chunks/`). Consumers (e.g. `wiki-query`) MUST treat exit
10 as "not provisioned" and fall back to the legacy hot->index->drill read order - never a
user-visible error. The fix is to run `bash scripts/setup-retrieve.sh`. Exit 2 = usage error.

## Provisioned-detection (how a consumer feature-detects)
```bash
if [ -f "$VAULT_ROOT/.vault-meta/bm25/index.json" ] && [ -d "$VAULT_ROOT/.vault-meta/chunks" ]; then
  python scripts/retrieve.py "<query>" --top 5    # use the pipeline
else
  : # fall back to hot->index->drill
fi
```

## Locking
Provisioning writes only under `<vault>/.vault-meta/` (chunks + bm25 index), which is
backend/agent infra, not shared wiki knowledge - no per-note Layer-2 lock is required for
the index build. Querying is read-only and takes no lock. (If a future nightly re-provision
runs concurrently with writers, the Layer-1 lease in the orchestrator covers it - P4.)

## Boundaries
- Wiki-owned. Writes ONLY `<vault>/.vault-meta/{chunks,bm25}/` (retrieval infra). Reads
  `wiki/` pages to chunk + index them.
- Does NOT write wiki knowledge pages, `objective/`, `research/`, or
  `meta/{health,cost}_report/`. Does NOT browse the web. Local/$0 by default.
