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
- _(append cost/health/window-budget baselines and trends)_

## Open structural proposals
- [reference-conflicts.md](reference-conflicts.md) — conflicts between the reference architectures
  and our v0.2 plan (path remapping, concepts<->entities swap to flag, RBAC/cost greenfield,
  research_deep + autoresearch RBAC rewrites, no cron in either repo).
- _(append proposed structural updates awaiting user confirmation)_
