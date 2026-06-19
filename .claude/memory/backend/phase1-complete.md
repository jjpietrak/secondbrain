# Fact: Phase 1 (Wiki agent + skills) COMPLETE - shipped 2026-06-19

Branch `claude/v2-prototype` (code repo) + live vault Inference-Disagg (its own git repo,
branch `master`). Phase 1 closed with the wiki-init reconcile APPLIED to the live vault + a
full end-to-end demo. NOT pushed (user pushes).

## What shipped across P0 + P1
- **P0 (foundational infra):** claude_agent.sh usage-capture, wiki-lock.sh (Layer-2),
  vault_lease.sh (Layer-1, wiring deferred to P4), locking.md snippet, 5 hermetic tests.
  See phase0-build.md.
- **P1 Wave 1 (commit fb1d7c0):** ingest_index v3 (waiting_approval/rejected + enqueue/queue/
  approve/reject + deletion-sweep guard), retrieval pipeline (contextual-prefix/bm25-index/
  rerank/retrieve + setup-retrieve.sh), vault_health.py + vault_stats.py (backend-owned),
  wiki agent def + RBAC hook + wiki-init.
- **P1 Wave 2 (commit 2ed8b05):** wiki skills - ingest/save/defuddle, query/cite, reconcile/
  synth/lint, think/challenge/connect (SKILL.md each).
- **P1 final (commit e8851c9):** wiki-retrieve SKILL.md (the one missing skill), vault_health
  dead-link resolver fix + regression test, wiki/hot.md path alignment across skills.

## Live-vault apply (wiki-init reconcile) - DONE on Inference-Disagg
Vault git chain (fully revertible): bb1248e (bootstrap) -> cbbbf53 (pre-wiki-init checkpoint)
-> 910ea87 (wiki-init reconcile) -> 445c46c (demo: retrieval index + health/stats reports).
Reconcile actions confirmed on disk:
- created folders: raw/{opinions,code,notebooklm}, research/query, meta/nightly_report
  (meta/health_report already existed from the dry-run report).
- updated 4 wiki/_template.md to the merged-superset frontmatter (adds bi-temporal timeline:
  + frozen tags; drops nothing). EXISTING knowledge pages NOT rewritten (verified: zero diff
  on co-packaged-optics.md, iris-tetra.md).
- collapsed wiki/hot/hot.md -> wiki/hot.md (file); dropped the wiki/hot/ folder.
- relocated stray root stepfun-mfa.md -> wiki/concepts/stepfun-mfa.md.
- dropped daily/ + output/ (preserved in vault git history at cbbbf53).
Apply is idempotent + additive (a second --apply run is a no-op).

## Demo results (live vault, executed the SKILL.md procedures manually = "as the wiki agent")
- **wiki-ingest:** Photons_to_Tokens.pdf already ingested (6 ingested / 0 pending). PDF text
  extract via scripts.pdf_extract works ($0, deterministic, 81746 chars). Source page
  wiki/sources/photons-to-tokens.md is the [[sources/X]] citation target; 7 concept/entity
  pages cite it.
- **wiki-cite light fact-check:** validated end-to-end. Claim "Lprog=30us drops GEMM TOPS by
  ~90% and LLM throughput by 14x" -> verdict=supported, route=keep, with the exact raw-PDF
  excerpt. Layer-2 lock taken+released on the page. The LiteLLM proxy on :4000 WAS reachable
  on /v1/chat/completions (even though /health + /v1/models 404) -> a REAL Gemini Flash
  validation call fired: 542 in / 196 out tokens, cost_usd=0.000653 (the only paid spend all
  phase; logged to logs/cost_ledger.jsonl, source=pay-as-you-go, role=validation).
- **wiki-query:** retrieve.py over the live vault returns the right pages for "co-packaged
  optics x attention/FFN disaggregation tradeoff" (top hits: attention-ffn-disaggregation,
  iris-tetra, disaggregation-thesis-2025-2026, sram-vs-hbm). Synthesized a fully [[wikilink]]-
  cited answer inline (P1 inline-only; research/query/ filing is P2). strategy was
  bm25+rerank:noop-no-model (BM25 only) because nomic-embed-text is not pulled.
- **wiki-health:** score 0/100 over 61 pages. Stray-root flag GONE (stepfun-mfa relocated).
  After the resolver fix: orphaned 5->1, dead_links 433->170 (the 170 are GENUINE forward-
  reference gaps to not-yet-created pages, e.g. [[mla-multi-head-latent-attention]],
  [[grouped-query-attention]], [[tco-per-million-tokens]]). Report:
  meta/health_report/health-2026-06-19.md.
- **wiki-stats:** 61 pages. By type: entity 27, concept 26, source 6, synthesis 1, untyped 1
  (= relocated stepfun-mfa.md, lacks type:). By status: developing 40, seed 19, mature 1,
  unset 1. Report: meta/health_report/stats-2026-06-19.md.

## Retrieval provisioning (live vault)
setup-retrieve.sh --no-llm -> 68 pages, 134 chunks (tier-3 synthetic), BM25 index
(.vault-meta/bm25/index.json, vocab 3907, avg_dl 197.5). Ollama IS running but
nomic-embed-text is NOT pulled -> rerank no-ops; BM25-only retrieval works (documented
graceful degrade). To enable rerank later: `ollama pull nomic-embed-text` (no re-provision).
.vault-meta/.gitignore added to ignore transient lock artifacts (.bm25.lock, .wiki-lock.meta,
locks/*.lock); chunks/ + bm25/ ARE committed (regenerable but portable on clone).

## File/skill map (code repo)
- skills/wiki-retrieve/SKILL.md  (NEW this step)
- skills/{wiki-init,wiki-ingest,wiki-save,wiki-defuddle,wiki-cite,wiki-query,wiki-reconcile,
  wiki-synth,wiki-lint,wiki-health,wiki-stats}/SKILL.md + skills/{think,challenge,connect}/
- agents/{ingest_index.py(v3),vault_health.py(resolver-fixed),vault_stats.py,vault_config.py,
  cost_tracker.py}
- scripts/{wiki_init.py,bm25-index.py,retrieve.py,rerank.py,contextual-prefix.py,
  setup-retrieve.sh,wiki_cite_check.py,pdf_extract.py,wiki-lock.sh,vault_lease.sh,
  claude_agent.sh}
- .claude/agents/{backend.md,wiki.md} + RBAC hook
- tests/test_vault_health.py (added path-qualified-link regression)

## Key decisions made this step
- vault_health resolves path-qualified wikilinks by BASENAME (the vault links as
  [[sources/X]]/[[concepts/Y]]; bare-stem matching false-flagged all of them). This is a
  backend-audit-tool correctness fix, NOT a vault-content change.
- Fixed wiki/hot/hot.md -> wiki/hot.md in 6 skill files + locking.md after the reconcile
  collapsed the folder (skills are code-repo, backend-writable; left the wiki-init descriptive
  lines intact).
- Committed the BM25 index to the vault (portable); ignored transient locks.

## Deferrals / flagged (-> P2 / P4 / user)
- HEALTH SCORE FORMULA (PROPOSAL, needs user): score = 100 - 2*(total issues), so any vault
  with >50 issues floors to 0 - uninformative for a growing wiki full of legitimate forward-
  reference links. Propose: cap the dead-link penalty per category, OR weight "link to a not-
  yet-created page" (a backlog signal) less than a true broken link. Did NOT change scoring
  (would expand scope; guideline #6).
- 170 genuine dead links = real wiki-agent backlog (pages the wiki wants but hasn't created):
  mla-multi-head-latent-attention, grouped-query-attention, tco-per-million-tokens (note:
  tco-per-million-tokens.md was DELETED in the working tree before the checkpoint - preserved
  in vault history at the parent of cbbbf53), dlp991uuv, mzi-mesh, etc. Wiki-agent task.
- ASCII/write-rules violation in wiki/concepts/programming-latency.md line ~24: uses the
  Unicode "approximately equal" glyph. wiki-lint/wiki agent should fix (wiki-owned content).
- P2: research/* reconcile (afd-simulator/daily/notebooklm/youtube subfolders), objective/
  scaffold, query-history filing to research/query/, ingest_index approval surface.
- P4: wire vault_lease.sh into the nightly orchestrator; --agent tags at all call sites;
  5h-window budget fields in cost_tracker; nightly re-provision of retrieval.
- nomic-embed-text not pulled -> rerank inert until `ollama pull nomic-embed-text`.
