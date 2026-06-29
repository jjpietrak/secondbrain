---
name: wiki-init
description: >
  Scaffold a fresh vault or reconcile an existing live vault to the v0.2 schema. Use when
  setting up a new vault, or when the live vault has drifted from the schema (missing folders,
  stale templates, stray root pages, the wiki/hot/ folder, leftover daily/output dirs).
  Triggers: "init the vault", "scaffold the wiki", "reconcile the vault to the schema",
  "bring the vault up to v0.2", "dry-run the schema migration". RECONCILE IS DRY-RUN BY
  DEFAULT and NEVER touches the live vault until you explicitly approve --apply.
allowed-tools: Read, Bash, Grep, Glob
---

**Ownership: `wiki` agent.** If you are NOT the `wiki` subagent (e.g. the main orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: wiki`, pass the user's full request, let the wiki agent run the steps below, and relay its result. Do NOT run the steps yourself - running as the `wiki` agent is what activates the RBAC/write-scope boundary. If you ARE the `wiki` agent, proceed.

# wiki-init

Deterministic schema tool (no LLM cost). Two modes, both driven by `scripts/wiki_init.py`.

## When to use
- `scaffold` - a fresh/empty vault directory needs the v0.2 skeleton.
- `reconcile` - an existing live vault needs to be brought in line with the v0.2 schema.

## Critical safety rule
`reconcile` is **dry-run-on-copy by default**. It snapshots the live vault to a `/tmp` copy,
applies the target schema to the COPY only, diffs copy-vs-live, writes a human-readable report
to `meta/health_report/wiki-init-dryrun-<ts>.md`, prints a summary, and **STOPS**. The ONLY
write it makes to the live vault is that single report file in the backend-owned
`meta/health_report/` folder. Applying the schema to the live vault is a separate, explicit
step that requires user approval: `--apply`.

## Procedure (reconcile)
1. Resolve the active vault root via `python -m agents.vault_config path` (never hard-code).
2. Run the dry-run:
   ```bash
   # run from the repo root (where agents/ lives)
   python scripts/wiki_init.py reconcile --vault-root "$(python -m agents.vault_config path)"
   ```
3. Read the printed summary AND the report at
   `<vault>/meta/health_report/wiki-init-dryrun-<ts>.md`.
4. **Present the summary to the user. STOP.** Do not apply until the user approves and any
   open conflicts (concepts<->entities mapping, frontmatter status enum) are resolved.
5. On approval only:
   ```bash
   .venv/bin/python scripts/wiki_init.py reconcile --apply --vault-root "$(python -m agents.vault_config path)"
   ```
   Apply is additive and idempotent (a second run is a no-op).
6. After --apply completes, rebuild `wiki/index.md` deterministically:
   ```bash
   VAULT_ROOT="$(.venv/bin/python -m agents.vault_config path)"
   .venv/bin/python scripts/wiki_index.py --vault-root "$VAULT_ROOT"
   ```
   This replaces any ad-hoc index append with the canonical table (real Title + Ingest date
   columns). `wiki_index.py` handles the Layer-2 lock on `wiki/index.md` internally.

## What reconcile changes (honoring the RESOLVED Phase-1 decisions)
- **Folders created:** `raw/{opinions,code,notebooklm}`, `research/{deep,query}`,
  `wiki/{entities,concepts,sources,synthesis}` (if absent), `meta/health_report/` and
  `meta/nightly_report/` as FOLDERS. `objective/` is Phase 2 -> skipped.
- **Meta single-artifact stores stay FILES** (`ingest_index.json`/`.md`, `cost_report.md`,
  `health_report.md`); only accumulating series become folders.
- **Templates:** (re)writes `wiki/<type>/_template.md` to the MERGED SUPERSET frontmatter -
  all live home-grown fields kept, plus bi-temporal `timeline:` and the frozen type tags
  (entity `company`/`last_interaction`; source `source_url`/`content_hash`; concept
  `related_projects`). No field dropped. Each template carries the `## For future Claude`
  preamble and `ai-first: true`.
- **wiki/hot:** collapses `wiki/hot/hot.md` -> `wiki/hot.md` (file); drops the `wiki/hot/` dir.
- **Stray page:** relocates root `stepfun-mfa.md` -> `wiki/concepts/`.
- **Dropped:** `daily/` and `output/` (output/ returns in Phase 5).

## Locking
Reconcile dry-run and apply are single-process schema operations. When `--apply` writes shared
targets it should run inside the nightly path under the Layer-1 lease (P4); the dry-run report
write to `meta/health_report/` is backend-owned and not a shared wiki append target.

## Frontmatter migration is additive
Templates are rewritten, but existing pages are NOT rewritten by reconcile. Page frontmatter is
migrated additively at ingest time (`.get()`-safe, never drops fields). This avoids a bulk
rewrite of the live vault's hand-curated pages.
