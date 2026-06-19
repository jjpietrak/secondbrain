---
name: wiki-query
description: >
  Answer a question from the wiki vault. Reads the hot cache first, then the index,
  then the most relevant pages (via the BM25+rerank retrieval pipeline when
  provisioned, else hot->index->drill). Synthesizes a cited answer using [[wikilinks]]
  only. In Phase 1 the answer is returned INLINE in chat (query history is filed to
  research/query/ in Phase 2 - not yet). Supports quick, standard, and deep depths.
  Triggers on: "what do you know about", "query:", "what is", "explain", "summarize",
  "find in wiki", "search the wiki", "based on the wiki", "/wiki-query", "wiki query
  quick", "wiki query deep".
allowed-tools: Read Glob Grep Bash
---

# wiki-query: answer questions from the wiki

Owner: **wiki**. The wiki has already done the ingest/synthesis work - read strategically,
answer precisely, cite every claim back to its page. Synthesis reasoning runs on the
**wiki agent credit pool** (no extra metered cost). Follows `references/ai-first-rules.md`
(cite sources, no fabrication, exhaustive search) and `references/write-rules.md`. ASCII
only.

Phase-1 scope: the answer is returned INLINE in chat. Filing query history to
`research/query/` is Phase 2 - do NOT create a `wiki/questions/` folder, and do NOT file
answers as wiki pages in Phase 1.

## Step 0 - resolve the vault (never hard-code a path)
```bash
eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
```
All reads are filesystem reads (`Read`, `Glob`, `Grep`) under `$VAULT_ROOT`. There is no
transport selector - the Second Brain is filesystem-only.

## Retrieval (preferred when provisioned)
Standard and Deep modes consult the hybrid retrieval pipeline BEFORE the legacy
hot->index->drill chain, when it is feature-detected:
```bash
if [ -f "$VAULT_ROOT/.vault-meta/bm25/index.json" ] && [ -d "$VAULT_ROOT/.vault-meta/chunks" ]; then
  python scripts/retrieve.py "<the user's question verbatim>" --top 5
fi
```
Output is JSON with a `candidates` array; each candidate has `absolute_path` to the source
page, a `snippet`, and `bm25_score` + `rerank_score`. `Read` the cited pages and synthesize
with page-level citation.

**Graceful fallback (required):** if `retrieve.py` exits **10** (not provisioned - no
`.vault-meta/{chunks,bm25}`), or any pipeline step errors, fall back to the legacy
hot->index->drill read order below. No user-visible breakage. Quick mode always skips
retrieval (hot cache only - keeps the ~1,500-token budget).

Setup/provisioning is `wiki-retrieve` (`bash scripts/setup-retrieve.sh`). This skill only
CONSUMES the pipeline; it does not build it.

## Query modes
| Mode | Trigger | Reads | Approx tokens | Best for |
|------|---------|-------|---------------|---------|
| Quick | `query quick:` or a simple factual Q | hot cache + index only | ~1,500 | "What is X?", date/fact lookups |
| Standard | default (no flag) | retrieve top-5 OR hot+index+3-5 pages | ~3,000 | most questions |
| Deep | `query deep:` or "thorough"/"comprehensive" | retrieve + every relevant page | ~8,000+ | "compare A vs B across everything", gap analysis |

The hot cache lives at `wiki/hot.md` (a file; collapsed from the old `wiki/hot/hot.md`
folder by wiki-init). The master index is `wiki/index.md`.

## Quick mode
1. Read `wiki/hot.md`. If it answers the question, respond immediately.
2. Else read `wiki/index.md`; scan descriptions for the answer.
3. If found in the index summary, respond without opening any page.
4. If not, say: "Not in the quick cache. Run as a standard query?" Do not open pages.

## Standard mode
1. Read `wiki/hot.md` first (it may already hold the answer or direct context).
2. If retrieval is provisioned, run `scripts/retrieve.py` and Read the top candidate pages.
   Otherwise read `wiki/index.md` to pick 3-5 relevant pages, then Read those. Follow
   wikilinks to depth-2 for key entities; no deeper.
3. Synthesize the answer in chat. Cite every claim with a `[[wikilink]]` to the page it
   came from (wikilinks ONLY - never `(path)` markdown links).
4. If the question reveals a GAP, say so (see Gap handling). Do NOT file the answer (P1).

## Deep mode
1. Read `wiki/hot.md` and `wiki/index.md`.
2. If retrieval is provisioned, run `scripts/retrieve.py --top 10`; also enumerate every
   relevant page across `wiki/{concepts,entities,sources,synthesis}/` (search completeness -
   do not sample).
3. Read every relevant page. No skipping.
4. Synthesize a comprehensive, fully cited answer. The wiki is the ground truth; do NOT
   supplement from training data on domain-specific facts (no web access in this agent).
5. Return inline. (Phase 2 will file deep-answer history to `research/query/`.)

## Citations and the vault PURPOSE filter
- Cite with `[[Page Name]]` wikilinks. Every external/factual claim traces to a page.
- Weigh relevance against the vault PURPOSE (`python -m agents.vault_config purpose`) -
  it is a high-priority relevance filter for what counts as on-topic for this vault.

## Gap handling (no fabrication)
If the wiki cannot answer the question well:
1. Say it plainly: "I don't have enough in the wiki to answer this well."
2. Name the specific gap: "I have nothing on <subtopic>."
3. Offer: "Want me to find a source on this?" (handing to ingest/research, not web here).
4. Do NOT fabricate. Do NOT answer domain-specific questions from training data. Verify
   absence by listing/grepping the vault, never from memory (ai-first-rules: false absence
   is the most common failure mode).

## Token discipline
hot cache (~500) -> stop if it answers. index (~1,000) -> stop once 3-5 pages identified.
3-5 pages (~300 each) -> usually enough. 10+ pages -> only for deep synthesis.

## Boundaries
- Wiki-owned, READ-ONLY over content in Phase 1 (no filing). No web access.
- Reads `wiki/`, and `raw/`/`wiki/sources/` only to trace a citation. Writes nothing in P1.
- Does not take vault locks (read-only).
