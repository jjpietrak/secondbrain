# Backend Agent — Memory index

One line per memory. Fact files live beside this index (one fact per file). Keep this index
current; never store fact bodies here.

## Project
- (seed) Second Brain v0.2 build is underway on branch `claude/v2-prototype` in
  `/home/jpietrak/second_brain`. Plan: `C:\Users\kubap\.claude\plans\elegant-juggling-sparrow.md`.
  Source-of-truth docs (frozen, read-only for agents): `docs/{requirements,agents,vault-schema,skills-description}.md`.

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
- _(append architecture decisions as they are made)_

## Audits
- _(append cost/health/window-budget baselines and trends)_

## Open structural proposals
- [reference-conflicts.md](reference-conflicts.md) — conflicts between the reference architectures
  and our v0.2 plan (path remapping, concepts<->entities swap to flag, RBAC/cost greenfield,
  research_deep + autoresearch RBAC rewrites, no cron in either repo).
- _(append proposed structural updates awaiting user confirmation)_
