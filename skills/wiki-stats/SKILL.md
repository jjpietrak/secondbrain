---
name: wiki-stats
description: >
  Aggregate the active vault's wiki/ pages and write a dated stats report: counts by
  `type` and `status`, tag frequency, and the most-recently-edited pages. Pure Python,
  no LLM, $0. Triggers on: "vault stats", "wiki stats", "/wiki-stats", "how many pages",
  "count pages by type", "what types of pages do we have", "tag stats", "vault overview".
allowed-tools: Read Bash Glob Grep
---

**Ownership: `backend` agent.** If you are NOT the `backend` subagent (e.g. the main orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: backend`, pass the user's full request, let the backend agent run the steps below, and relay its result. Do NOT run the steps yourself - running as the `backend` agent is what activates the RBAC/write-scope boundary. If you ARE the `backend` agent, proceed.

# wiki-stats: aggregate the wiki

Owner: **backend**. Thin wrapper over `agents/vault_stats.py`. Deterministic file walk,
no model, $0. Writes its report under the backend-owned `meta/health_report/` folder.
It NEVER edits vault knowledge content.

## What it aggregates

- **by type** - count of pages per `type` frontmatter value (entity, concept, source,
  synthesis, ... ; `untyped` for pages with no `type`).
- **by status** - count per `status` value (seed / developing / mature / evergreen ...;
  `unset` when absent).
- **top tags** - frequency of `tags:` (list or scalar) plus `#inline` tags (top 25).
- **most recently edited** - top 15 pages by `updated` (fallback `created` / `date`).

## Steps

1. Resolve the active vault (never hard-code a path):
   ```bash
   eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
   ```
2. Run the aggregator. It writes `meta/health_report/stats-<date>.md`:
   ```bash
   python -m agents.vault_stats                 # writes the dated report
   python -m agents.vault_stats --print-only    # preview without writing
   python -m agents.vault_stats --json          # machine-readable for tooling
   python -m agents.vault_stats --vault <name>  # target a specific vault
   ```
3. Summarize totals and the type/status distribution for the user; point at the report.

## Boundaries

- Backend-owned. Writes ONLY `meta/health_report/`. Reads `wiki/` read-only.
- $0: no model call, no network, no API key.
- Lightweight frontmatter parser (no PyYAML dependency); aggregates only scalar `type`/
  `status` and tag lists - nested objects are ignored.
