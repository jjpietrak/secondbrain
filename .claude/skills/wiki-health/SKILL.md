---
name: wiki-health
description: >
  Audit the active vault's wiki/ for structural health and write a dated report.
  Flags orphaned pages, dead wikilinks, missing required frontmatter, stale pages,
  empty/stub pages, frontmatter trapped in a leading code fence (unwrap, do not add),
  unfilled template syntax, and probable duplicate pages. Pure Python, no LLM, $0.
  Triggers on: "vault health", "health check", "/wiki-health", "audit the wiki",
  "check the vault", "find orphans", "find dead links", "is the vault healthy".
allowed-tools: Read Bash Glob Grep
---

**Ownership: `backend` agent.** If you are NOT the `backend` subagent (e.g. the main orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: backend`, pass the user's full request, let the backend agent run the steps below, and relay its result. Do NOT run the steps yourself - running as the `backend` agent is what activates the RBAC/write-scope boundary. If you ARE the `backend` agent, proceed.

# wiki-health: structural audit of the wiki (wiki-scoped subset)

Owner: **backend**. This skill is a thin wrapper over `agents/vault_health.py` scoped to
`--area wiki`. It runs no model and costs nothing - it is a deterministic file walk that
reports structural problems for a human (or the wiki agent) to fix. It NEVER edits vault
knowledge content; it only writes its own report under the backend-owned
`meta/health_report/` folder.

> **Full-vault scan:** use the `vault-health` skill (runs all areas: wiki, objective,
> research). This skill (`wiki-health`) is the wiki-only subset and is equivalent to
> running `python -m agents.vault_health --area wiki`.

## What it checks

| Check | Severity intent | Why it matters |
|-------|-----------------|----------------|
| `code_fence_wrapped` | error | Frontmatter saved INSIDE a leading ``` fence. Fix is to UNWRAP, never to add a second frontmatter block (that double-corrupts the page). |
| `unfilled_template` | error | `<% ... %>` Templater syntax left behind in a real page. |
| `duplicate` | warning | Two+ pages whose normalized stem collides (likely the same concept twice). |
| `missing_frontmatter` | warning | Page lacks one of `type`, `created`, `updated`, `sources`. |
| `dead_links` | warning | `[[wikilink]]` to a page that does not exist. |
| `orphaned` | info | Page has no inbound wikilinks. |
| `stale` | info | `updated` is more than 90 days old. |
| `empty` | info | Body under 50 chars (stub). |

`code_fence_wrapped` short-circuits `missing_frontmatter` for the same page, so a
fence-wrapped page is reported once with the correct fix.

## Steps

1. Resolve the active vault (never hard-code a path):
   ```bash
   eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
   ```
2. Run the auditor (wiki-scoped). It writes `meta/health_report/health-<date>.md` (a
   folder, so dated reports accumulate):
   ```bash
   python -m agents.vault_health --area wiki                 # writes the dated report
   python -m agents.vault_health --area wiki --print-only    # preview without writing
   python -m agents.vault_health --area wiki --json          # machine-readable for tooling
   python -m agents.vault_health --area wiki --vault <name>  # target a specific vault
   ```
3. Summarize the score and the top issues for the user. Point at the report path.
4. Do NOT fix anything here. Hand fixes to the wiki agent (wiki-lint / wiki-reconcile)
   or the user.

## Boundaries

- Backend-owned. Writes ONLY `meta/health_report/`. Reads `wiki/` read-only.
- $0: no model call, no network, no API key.
- Does not take vault locks (read-only over content; its own report dir is uncontended).
- Runs `--area wiki` only. For a full-vault scan use the `vault-health` skill.
