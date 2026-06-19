# Fact: OSB Phase-1 pieces to lift (vault_health, vault_stats, reconcile/synth/connect/challenge)

Read 2026-06-19 from `.osb_reference`. The narrow lift.

## scripts/vault_health.py (-> backend `wiki-health`)
Pure-Python vault auditor, `--path <vault> [--json]`. Checks: duplicate notes, orphans (no inbound
links), stale tasks, missing frontmatter, **frontmatter trapped in a leading ```markdown code fence**
(unwrap not add - subtle bug guard worth keeping), empty folders, broken internal `[[links]]`,
unfilled Templater `<%...%>`. `EXCLUDE_DIRS={.obsidian,.trash,_trash,.git,Templates}`. Regexes for
FRONTMATTER, CODE_FENCE_WRAP, LINK, due-date, TEMPLATE, aliases. `--json` for Claude consumption.
NOTE: WE ALREADY HAVE `agents/vault_health.py` (our own port: orphans/dead-links/missing-fm/stale/
stub, writes `meta/health_report.md`, no PyYAML). Phase-1 decision: keep OURS as the base, MERGE in
OSB's code-fence-wrap + unfilled-template + duplicate-note checks; output to `meta/health_report/`
(folder per schema, not single file). $0 (no LLM).

## scripts/vault_stats.py (-> backend `wiki-stats`, NEW skill wrapper)
Aggregates notes by `type`/`status`, rewrites a marked block in `index.md`, no PyYAML dep. PORT the
script into our `scripts/`; ADAPT to write `meta/health_report/` stats + a marked block in
`wiki/index.md` (Wiki agent owns wiki/index.md write -> backend emits, wiki applies; or backend writes
its own stats file and wiki-stats SKILL reads it). $0.

## Thinking/reconcile commands (`.osb_reference/commands/`)
- **obsidian-reconcile.md** -> `wiki-reconcile` (wiki): resolve contradictions across pages. ADAPT to
  bi-temporal rule (append to `timeline:`, never overwrite role/status) + `[[wikilink]]` citations.
- **obsidian-synthesize.md** -> `wiki-synth` (wiki): auto-find unnamed cross-source patterns -> new
  `wiki/synthesis/` page. ADAPT path (OSB has no synthesis/).
- **obsidian-connect.md** -> `connect` (research per docs catalog, wiki per plan map - SEE RISK): bridge
  two unrelated domains.
- **obsidian-challenge.md** -> `challenge` (research/wiki): red-team an idea vs vault history of past
  failures/reversed decisions. CO has NO challenge command (correction confirmed).
- **obsidian-export.md** + `scripts/export_okf.py` -> `export` (P5).
- **obsidian-health.md** -> the SKILL wrapper over vault_health.py (backend `wiki-health`).

## hooks/validate-ai-first.sh (OSB)
PostToolUse: warns if a vault write lacks AI-first frontmatter / `## For future Claude` / contains
substitution-Unicode (em-dash/curly/Unicode-math). PORT check-5 (ASCII gate) as our write-time guard
for `write-rules.md`. Already mirrored conceptually in our skills/references/.

## Owner conflict to resolve (catalog vs plan)
docs/skills-description.md puts challenge/connect/think under agent=research; the plan's P1 skills-map
puts challenge/connect/think under wiki (P1). The docs are authoritative. Likely both: think+challenge+
connect are shared thinking tools usable by wiki AND research. FLAG for user (see risks memory).
