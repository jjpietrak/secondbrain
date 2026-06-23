# Phase 3 (web agent) — BUILD CONCLUSION + deferred revision (2026-06-23)

**User sign-off verdict:** Phase 3 web scraping is **functionally tested and confirmed working OK,
with minor bugs**. The end-to-end pipeline runs (free Tier-0 + gated Perplexity Tier-2):
gaps/directions → DECISION → harvest → rank → select → interactive approve/reject report →
ingest → backlog. **BUT result accuracy/relevance is NOT good enough to trust yet, and the
DECISION logic is obscure.** Perplexity (Sonar) returned only *slightly* relevant web resources;
the free arXiv path (nomic embeddings) was more on-topic but still not great.

**Phase 3 is CLOSED as a working baseline, NOT production-trusted.** Do NOT wire the web crawl
into the nightly as a trusted auto-source until the revision below. Current web output is
candidate-only, user-approved.

## DEFERRED — a COMPLETE revision is required before relying on web (user's explicit ask)
The next web iteration must redo, together:
1. **Query formulation** (the weakest link). Today: a direction's `seed_queries` (LLM-written back
   at obj-synth time) + a gap's `missing`/title text. These don't reliably retrieve on-target
   sources. Rethink how queries are FORMED — per source type, LLM-reformulated per target at crawl
   time, PURPOSE-conditioned, query expansion, etc.
2. **Ranking** (`web_rank`). nomic cosine vs PURPOSE+expected_evidence (or keyword fallback);
   relevance mediocre. Reconsider the signal — cross-encoder rerank, an LLM relevance judge,
   evidence-fit scoring (does the candidate actually contain the `expected_evidence`?), not just
   embedding cosine.
3. **Gap/direction analysis (the DECISION: merge/lanes/route)** — currently **obscure** and hard to
   reason about. Make the what-to-search decision more legible AND more accurate. (The DecisionTrace
   tooling is the lens to diagnose it — use `--explain`.)
4. **Feedback loops (the priority).** The user wants accept/reject to actually IMPROVE query
   formulation + ranking over time — a real CLOSED loop, not just persisting the signal. This is the
   central goal of the next web iteration and overlaps the `agent-learn` design. "Better feedback
   loops and formulating" is the headline ask.

## What IS solid (keep — the plumbing is fine; the intelligence layer is what's weak)
- Pipeline plumbing: free harvest (RSS / arXiv-by-category / paper+forum APIs), the candidate model
  + per-item `candidate_ident` dedup, the typed budget + lane quotas, the **interactive approve/reject
  report + backlog**, the **enforced working-URL gate**, per-gap `wiki/gap/` files with graph edges,
  the cost gate.
- **DecisionTrace / `--explain` / embedded `## Decision trace`** — the transparency tooling is good;
  it is the right instrument for the revision.
- **Perplexity as a gated Tier-2 harvest engine (shared backend)** — the ADAPTER pattern is sound;
  the query/rank FEEDING it is the problem, not the wiring. Same pattern will take Apify/crawl4AI.

## State at close
- Code complete + committed (`954dbc1` … `a8a7d02`), **939 tests**, NOT pushed. Skills/agent need a
  CLI restart to register.
- Mid-phase bug FIXED: ingest_index status-persistence (`report_approve` now passes explicit
  `root=`; the live `VAULT` env had clobbered the old resolution).
- Known minor (deferred): Perplexity cost ledger logs the call with 0 tokens/$0 (Sonar `usage` not
  wired); smarter paid-call scheduling is a TODO; Apify/crawl4AI adapters not built.
- Vault working tree (user to review/push): `wiki/gap/` migration, kept `nightly_report/2026-06-23.md`,
  wiki-agent approve-run memory, and a user `D-0001-no-web-research.md` deletion.

## NEXT
**Awaiting user 'go'** before Phase 4 (nightly orchestration + token-window budgeting + `agent-learn`).
Recommendation: fold the web-pipeline revision (items 1–4) INTO the `agent-learn` grill-me — the
feedback loop is the shared backbone. Do not auto-trust web output until then.
