---
name: wiki-lint
description: >
  Structural lint over the active vault's wiki/. Finds orphan pages, dead wikilinks,
  missing/code-fence frontmatter, unfilled template syntax, stale pages, stub pages,
  probable duplicates, stray root files, empty sections, and widely-mentioned terms
  that lack a page. Reuses agents/vault_health.py for the page-level checks and adds
  the skill-only structural checks via scripts/wiki_lint_extra.py. Pure Python, no LLM,
  $0. Reports only - asks before any fix. Triggers on: "lint", "lint the wiki",
  "wiki lint", "/wiki-lint", "find orphans", "find dead links", "wiki maintenance",
  "wiki audit", "clean up the wiki", "check the wiki".
allowed-tools: Read Bash Glob Grep
---

# wiki-lint: structural lint over wiki/

Owner: **wiki**. Ported from the CO `wiki-lint` skill MINUS DragonScale address
validation, semantic tiling, and canvas maps (those mechanisms do not exist in this
vault's schema). This skill is `$0` and runs no model - it is a deterministic file walk.
It REPORTS structural problems and asks before fixing anything. It NEVER auto-deletes,
never auto-merges, and never resolves contradictions (that is `wiki-reconcile`).

Run lint after every 10-15 ingests, or weekly.

## What it checks (and where each check lives)

This skill does NOT re-implement page-level checks. It REUSES the backend auditor
`agents/vault_health.py` (the `wiki-health` script side) and only adds the structural
checks that have no script-side home.

Reused from `agents/vault_health.py` (call it, do not duplicate):

| Check | Meaning |
|-------|---------|
| `orphaned` | page with no inbound wikilinks |
| `dead_links` | `[[wikilink]]` to a page that does not exist |
| `missing_frontmatter` | page lacks `type` / `created` / `updated` / `sources` |
| `code_fence_wrapped` | frontmatter trapped in a leading ``` fence (UNWRAP, never add a second block) |
| `unfilled_template` | `<% ... %>` Templater syntax left in a real page |
| `stale` | `updated` more than 90 days old |
| `empty` | whole-page stub (body under 50 chars) |
| `duplicate` | two+ pages whose normalized stem collides |

Added by `scripts/wiki_lint_extra.py` (skill-only structural checks):

| Check | Meaning |
|-------|---------|
| `stray_root` | `*.md` sitting at the VAULT ROOT that belongs inside `wiki/` (e.g. the live `stepfun-mfa.md`) |
| `empty_sections` | a heading (`##`/`###`) with no content before the next heading or EOF (finer-grained than the whole-page `empty`) |
| `missing_pages` | a term wikilinked from 2+ distinct pages that has no page of its own (mentioned widely enough to deserve one) |

DROPPED from the CO original (not applicable to this schema): DragonScale address
validation (`address:` / `allocate-address.sh` / `.raw/.manifest.json`), semantic tiling
(`tiling-check.py` / embedding cosine), canvas maps (`*.canvas`), and the Dataview
dashboard. Naming-convention and writing-style flags are folded into the report's notes
section but are advisory only.

## Steps

1. Resolve the active vault (never hard-code a path):
   ```bash
   eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
   ```
2. Run the reused page-level auditor for its JSON:
   ```bash
   python -m agents.vault_health --json > /tmp/wiki-lint-health.json
   ```
   (Plain `python -m agents.vault_health` also writes the dated backend report under
   `meta/health_report/`; use `--json` here so wiki-lint can fold the results into one
   view without a second file walk.)
3. Run the skill-only structural checks:
   ```bash
   python -m scripts.wiki_lint_extra --json   # to stdout, no writes
   ```
   (Default invocation already emits JSON; `--vault NAME` / `--path` target a vault.)
4. Merge both JSON objects into ONE lint report and present it to the user. Tier the
   findings by severity (errors -> warnings -> info), not by ease of fix:
   - errors: `code_fence_wrapped`, `unfilled_template`
   - warnings: `dead_links`, `missing_frontmatter`, `duplicate`, `stray_root`
   - info: `orphaned`, `stale`, `empty`, `empty_sections`, `missing_pages`
5. Relocation flag: when `stray_root` is non-empty, FLAG each file for relocation into
   `wiki/concepts/` or `wiki/entities/` (per its content) - do NOT move it here. The
   move is a `wiki-init` reconcile step, gated on user approval.
6. Ask before fixing: "Should I fix these, or do you want to review each one?" Safe to
   auto-fix only with explicit approval (add missing frontmatter fields with placeholders;
   create stubs for `missing_pages`; add wikilinks for clear unlinked mentions). NEVER
   auto-fix: deleting orphans (may be intentional), resolving contradictions (-> hand to
   `wiki-reconcile`), merging duplicates (human judgement).

## Report shape

A single markdown report, tiered, with `[[wikilinks]]` for every page named:

```markdown
# Wiki lint - YYYY-MM-DD

## Summary
- Pages scanned: N | Errors: E | Warnings: W | Info: I

## Errors
- code_fence_wrapped: [[Page]] - frontmatter trapped in a fence; UNWRAP, do not add.
- unfilled_template: [[Page]] - `<% ... %>` left in a real page.

## Warnings
- dead_links: [[Source]] -> [[Missing]] (create a stub or remove the link)
- stray_root: stepfun-mfa.md -> relocate into wiki/concepts or wiki/entities (wiki-init)

## Info
- orphaned: [[Page]] (no inbound links - may be intentional)
- missing_pages: [[Term]] mentioned in N pages but has no page
- empty_sections: [[Page]] -> 'Heading' has no content
```

Where to write it: this skill normally PRESENTS the report inline. If a file is wanted,
the backend-owned dated report under `meta/health_report/` (written by `wiki-health`) is
the canonical artifact - do not write a parallel `wiki/meta/` report (this schema has no
`wiki/meta/`).

## Locking

Lint is READ-ONLY over `wiki/` and writes no shared target, so it takes no lock. If a
fix is approved and it touches a shared append target (`wiki/index.md`, `wiki/log.md`,
`wiki/hot.md`), apply the canonical lock snippet from
[`skills/references/locking.md`](../references/locking.md) (acquire -> write -> release;
sorted-path order for multi-file; on rc=75 retry once after 2s then log and skip).

## Conventions

- Follow [`skills/references/ai-first-rules.md`](../references/ai-first-rules.md) and
  [`write-rules.md`](../references/write-rules.md) for anything written. ASCII only:
  no em-dashes, curly quotes, or Unicode math.
- Cost: `$0` - no model, no network, no API key. The two helpers are pure Python.
- Anti-fabrication: enumerate exhaustively; never report a clean bill from a partial
  scan. Both helpers walk every `wiki/**/*.md` (excluding `_*` template files).
