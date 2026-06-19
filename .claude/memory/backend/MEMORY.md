# Backend Agent — Memory index

One line per memory. Fact files live beside this index (one fact per file). Keep this index
current; never store fact bodies here.

## Project
- (seed) Second Brain v0.2 build is underway on branch `claude/v2-prototype` in
  `/home/jpietrak/second_brain`. Plan: `C:\Users\kubap\.claude\plans\elegant-juggling-sparrow.md`.
  Source-of-truth docs (frozen, read-only for agents): `docs/{requirements,agents,vault-schema,skills-description}.md`.

## Build log
- [phase0-build.md](phase0-build.md) — Quick Phase 0 SHIPPED 2026-06-19: claude_agent.sh
  usage-capture contract, wiki-lock.sh (Layer-2) port, vault_lease.sh (Layer-1, wiring
  deferred to P4), locking.md snippet, 5 hermetic tests (all green via tests/run_phase0.sh).
- [phase1-complete.md](phase1-complete.md) — Phase 1 COMPLETE 2026-06-19: wiki agent + all
  13 skills (incl. the final wiki-retrieve SKILL), ingest_index v3, retrieval pipeline.
  wiki-init reconcile APPLIED to the live Inference-Disagg vault (folders/templates/hot-
  collapse/stray-relocate/daily+output-drop, all confirmed; existing pages untouched).
  End-to-end demo green: ingest/cite (real Gemini Flash validation call, $0.000653)/query/
  health/stats. vault_health dead-link resolver fixed (path-qualified wikilinks). Commits:
  code e8851c9, vault 445c46c (chain cbbbf53->910ea87->445c46c, revertible). Not pushed.
- [wiki-init-v2.md](wiki-init-v2.md) — wiki-init structural cleanup DONE 2026-06-19:
  Change1 (templates/ drop + 4 producer _template.md) + Change2 (vault _CLAUDE.md delete)
  applied to Inference-Disagg. SCHEMA_VERSION 1->2. All 15 on-disk checks pass.
  Vault commit df2c5df; code commit (see wiki-init-v2.md). Not pushed.

## Architecture
- [retrieval-pipeline.md](retrieval-pipeline.md) — claude-obsidian hybrid retrieval
  (contextual-prefix + BM25 + ollama cosine rerank); copy whole into `scripts/`; drives
  `wiki-retrieve` + `web-rank`; default path is $0/local.
- [skill-source-map.md](skill-source-map.md) — verified skill->source-repo map; `docs/skills-description.md`
  citations are accurate; key correction: think=CO, challenge+connect=OSB.
- [co-phase1-skills.md](co-phase1-skills.md) — per-skill PORT/ADAPT/DROP for CO Phase-1 skills +
  scripts (retrieval pipeline, wiki-lock, ingest minus DragonScale) + hooks + verifier template.
- [osb-phase1-pieces.md](osb-phase1-pieces.md) — OSB lift: vault_health/vault_stats, reconcile/synth/
  connect/challenge commands, validate-ai-first ASCII gate; we already have our own vault_health.py.
- [current-code-inventory.md](current-code-inventory.md) — exact v0.1 state: ingest_index v2 schema/
  verbs, vault_config, cost_tracker, claude_agent.sh usage-discard blocker, command->skill map.
- [live-vault-divergences.md](live-vault-divergences.md) — live Inference-Disagg vs frozen schema:
  daily/output/research-subfolders, meta files-vs-folders, frontmatter status-enum + bi-temporal
  conflict, concepts<->entities reversal, stray root page.

## Decisions
- [reference-decision.md](reference-decision.md) — claude-obsidian chosen as the backend
  implementation model; what to Copy/Take/Build-new/Drop. Full report: `plans/reference-comparison.md`.
- Two-layer write safety LOCKED (P0): Layer-2 wiki-lock.sh per-file (runtime, every skill via
  the locking.md snippet) + Layer-1 vault_lease.sh whole-vault cross-host git lease (nightly
  only, wired in P4). Lease policy: humans never block; only automated writers call acquire
  (--mode auto defers with exit 75). Details: [phase0-build.md](phase0-build.md).
- claude_agent.sh now CAPTURES usage (no longer `exec claude -p`): wraps --output-format json,
  records a cost_tracker row (source=agent-sdk-credit), re-emits only .result. --agent tags
  per-agent spend. Backward-compatible with nightly_run.sh. Details: phase0-build.md.
- _(append architecture decisions as they are made)_

## Audits
- Cost ledger now receives a row for EVERY agent-SDK claude call (P0.1). Per-agent attribution
  available via `--agent <id>` -> action tag; P4 will tag all nightly call sites + add the
  5h-window/window_tokens fields. No paid spend incurred building/testing Phase 0 (all hermetic).
- **HEALTH BASELINE (Inference-Disagg, 2026-06-19, post wiki-init):** score 0/100, 61 pages.
  After the dead-link resolver fix: orphaned=1, dead_links=170 (all genuine forward-references
  to not-yet-created pages), duplicate=1 (step-3 entity vs source - expected), missing_fm=1
  (stepfun-mfa), empty=1 (stepfun-mfa stub), untyped=1. Type split: entity 27 / concept 26 /
  source 6 / synthesis 1. Status: developing 40 / seed 19 / mature 1. The 0 score is a formula
  artifact (see structural proposal), not vault rot. Reports in meta/health_report/.
- **PAID SPEND (Phase 1):** exactly ONE metered call - the wiki-cite demo Gemini Flash
  validation judge, $0.000653 (542/196 tok, role=validation, source=pay-as-you-go). Everything
  else ($0): BM25/retrieval (local), pdf_extract (deterministic), health/stats (local). Budget
  (~$5 paid Anthropic) untouched; LiteLLM proxy is reachable on /v1/chat/completions (but
  /health + /v1/models 404 - probe the chat endpoint, not /health).

## Open structural proposals
- [reference-conflicts.md](reference-conflicts.md) — conflicts between the reference architectures
  and our v0.2 plan (path remapping, concepts<->entities swap to flag, RBAC/cost greenfield,
  research_deep + autoresearch RBAC rewrites, no cron in either repo).
- **Health score formula (ADR-style, awaiting user):** `vault_health.render_report` uses
  `score = max(0, 100 - 2*total_issues)`, so any vault with >50 issues floors to 0 -
  uninformative for a growing wiki whose dead-links are mostly legitimate forward-references
  (links to pages the wiki intends to create). Proposal: cap the per-category penalty (esp.
  dead_links), and/or split dead-links into "broken link to existing-then-removed page" (real)
  vs "link to never-created page" (backlog signal, lower weight). NOT implemented (scope guard
  #6). Decision needed before the score is used as a nightly gate.
- **Vault content findings for the wiki agent (not backend's to fix):** wiki/concepts/
  programming-latency.md has a Unicode "approximately equal" glyph (write-rules ASCII
  violation); stepfun-mfa.md (relocated) is missing type/created/updated/sources frontmatter +
  is a stub; 170 forward-reference pages are wanted but uncreated. Route to wiki-lint/wiki-cite.
- **docs/vault-schema.md needs user update (not backend's to write):** Two lines to drop +
  one convention to add: (1) drop the `templates/` folder entry from the vault tree (it is
  gone); (2) drop the `_CLAUDE.md` entry from the vault root (it is gone; agent rules are
  in the code repo; PURPOSE is in vault.yaml); (3) add a note that each producer folder
  carries a colocated `_template.md` (meta/health_report, meta/nightly_report, research/deep,
  research/query). docs/ is frozen for agents; route to user.
- _(append proposed structural updates awaiting user confirmation)_
