---
type: reference/architecture
---
# Fact: hybrid retrieval pipeline

Related: [[locking]], [[rbac]], [[cost-ledger]]

The wiki-retrieve feature uses a single hybrid retrieval stack built on three
scripts under `scripts/`: `contextual-prefix.py`, `bm25-index.py`, `rerank.py`, and
`retrieve.py` (the orchestrator). The design follows Anthropic's Sept-2024 contextual-retrieval
research and delivers a purely local ($0) default path.

## Pipeline stages

1. **Contextual-prefix (ingest-side)** -- `scripts/contextual-prefix.py`. Chunks each page on
   paragraph boundaries (~500-token target, 200-char overlap). Adds a 1-2 sentence contextual
   prefix per chunk and writes chunks to `.vault-meta/chunks/<page-address>/chunk-NNN.json`
   (fields: `raw_text`, `contextualized_text`, `body_hash`, `page_address`, `page_path`,
   `chunk_index`). Prefix generation tiers (first available wins):
   - Anthropic API Haiku + prompt cache: gated behind `--allow-egress` (metered).
   - `claude` CLI subprocess: local credit-pool call.
   - Synthetic (title + first paragraph): fully local, default, $0.

2. **BM25 index** -- `scripts/bm25-index.py`. Pure-stdlib Okapi BM25 (k1=1.5, b=0.75) over
   `contextualized_text`. Unicode tokenizer. fcntl-locked atomic write to
   `.vault-meta/bm25/index.json`. Verbs: `build`, `query`, `stats`.

3. **Cosine rerank** -- `scripts/rerank.py`. Embeds chunks with `nomic-embed-text` via local
   ollama (127.0.0.1:11434). Embedding cache keyed on `body_hash`. localhost-only guard
   (remote ollama requires `--allow-remote-ollama`). Degrades gracefully to a no-op (BM25
   order unchanged) when ollama or the model is absent.

4. **Orchestrator** -- `scripts/retrieve.py`. BM25 top-20 -> rerank top-5 -> dedupe by
   page-address -> returns JSON candidates with `absolute_path`. Exit code 10 = not
   provisioned (caller falls back to legacy hot/index/drill path).

## Cost posture

The default execution path is fully local ($0): synthetic prefix + BM25 + rerank absent
(or with local ollama). The only egress paths are the contextual-prefix API tier
(`--allow-egress`) and remote ollama (`--allow-remote-ollama`), both requiring explicit
opt-in -- consistent with the system-wide $0/free-route discipline. Provision with
`scripts/setup-retrieve.sh`.
