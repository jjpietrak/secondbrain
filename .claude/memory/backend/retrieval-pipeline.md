# Fact: claude-obsidian hybrid retrieval pipeline (copy whole)

Source: `.co_reference/scripts/` + `.co_reference/skills/wiki-retrieve/SKILL.md`. Based on Anthropic's
Sept-2024 contextual-retrieval research. Drives our `wiki-retrieve` (wiki, P1) and `web-rank` (web, P3).

Pipeline:
1. **contextual-prefix.py** (505 lines, ingest-side): chunk each page on paragraph boundaries
   (~500-token target, 200-char overlap); add a 1-2 sentence contextual prefix per chunk; write
   `.vault-meta/chunks/<page-address>/chunk-NNN.json` (`raw_text`, `contextualized_text`, `body_hash`,
   `page_address`, `page_path`, `chunk_index`). Prefix tiers: (1) Anthropic API Haiku + prompt cache
   GATED behind `--allow-egress`; (2) `claude` CLI subprocess; (3) synthetic (title+first-para, local, default).
2. **bm25-index.py** (293 lines): pure-stdlib Okapi BM25 (k1=1.5, b=0.75) over `contextualized_text`;
   Unicode tokenizer; fcntl-locked atomic write to `.vault-meta/bm25/index.json`; `build`/`query`/`stats`.
3. **rerank.py** (312 lines): cosine over `nomic-embed-text` via local ollama (127.0.0.1:11434);
   embedding cache keyed by `body_hash`; localhost-only guard (off-localhost needs
   `--allow-remote-ollama`); degrades to no-op (BM25 order) if ollama/model absent.
4. **retrieve.py** (195 lines, orchestrator): bm25 top-20 -> rerank top-5 -> dedupe by page-address ->
   JSON candidates with `absolute_path`. Exit 10 = not provisioned (caller falls back to legacy
   hot->index->drill). Imports siblings as modules.

Provision via `bin/setup-retrieve.sh`. Default path is fully local ($0); only egress is the
contextual-prefix API tier and remote ollama, both double-consent-gated - matches our $0/free-route
discipline. Benchmark claim: +32pp top-1, +41% error reduction vs page-level baseline.
`web-rank` reuses the same `rerank.py`.
