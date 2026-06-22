---
name: vault-health
description: >
  Audit ALL vault areas -- wiki/objective/research -- for structural health and write a
  dated report. Superset of wiki-health. Flags orphaned pages, dead wikilinks (resolved
  cross-area), missing required frontmatter (per type), stale pages, empty/stub pages,
  frontmatter trapped in a leading code fence (unwrap, do not add), unfilled template
  syntax, and probable duplicate pages. Pure Python, no LLM, $0.
  Triggers on: "vault health", "check the whole vault", "audit all items",
  "full vault health", "vault-health", "audit the vault", "health check all areas".
allowed-tools: Read Bash Glob Grep
---

**Ownership: `backend` agent.** If you are NOT the `backend` subagent (e.g. the main orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: backend`, pass the user's full request, let the backend agent run the steps below, and relay its result. Do NOT run the steps yourself - running as the `backend` agent is what activates the RBAC/write-scope boundary. If you ARE the `backend` agent, proceed.

# vault-health: structural audit of ALL vault areas

Owner: **backend**. This skill is a wrapper over `agents/vault_health.py`. It is the
**superset of wiki-health** -- it audits wiki/, objective/, and research/ in a single
pass. It runs no model and costs nothing; it is a deterministic file walk.

Cross-area link resolution: a link like `[[wiki/concepts/foo]]` in an objective node
resolves to the page stem "foo" and will NOT be flagged as dead as long as that wiki
page exists. The page index is built across all scanned areas.

Per-type required frontmatter is enforced: wiki pages require {type, created, updated,
sources}; objective nodes require type-specific keys (id, status, solved, etc.) derived
from the colocated _template.md; research reports require {type, id, created, updated,
status}. Unknown types fall back to {type, created, updated}.

It NEVER edits vault knowledge content; it only writes its own report under the
backend-owned `meta/health_report/` folder.

## What it checks

| Check | Severity intent | Why it matters |
|-------|-----------------|----------------|
| `code_fence_wrapped` | error | Frontmatter saved INSIDE a leading ``` fence. Fix is to UNWRAP, never to add a second frontmatter block. |
| `unfilled_template` | error | `<% ... %>` Templater syntax left behind in a real page. |
| `duplicate` | warning | Two+ pages whose normalized stem collides (likely the same concept twice). |
| `missing_frontmatter` | warning | Page lacks one or more of its type-required frontmatter keys. |
| `dead_links` | warning | `[[wikilink]]` to a page that does not exist in any scanned area. |
| `orphaned` | info | Page has no inbound wikilinks from any scanned area. |
| `stale` | info | `updated` is more than 90 days old. |
| `empty` | info | Body under 50 chars (stub). |

`code_fence_wrapped` short-circuits `missing_frontmatter` for the same page.

## Steps

1. Resolve the active vault (never hard-code a path):
   ```bash
   eval "$(wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.vault_config env')"
   ```
2. Run the auditor. It writes `meta/health_report/health-<date>.md` (a folder, so
   dated reports accumulate):
   ```bash
   # All areas (default):
   python -m agents.vault_health

   # Scope to a single area:
   python -m agents.vault_health --area wiki
   python -m agents.vault_health --area objective
   python -m agents.vault_health --area research

   # Output modes:
   python -m agents.vault_health --print-only    # preview without writing
   python -m agents.vault_health --json          # machine-readable for tooling
   python -m agents.vault_health --vault <name>  # target a specific vault
   ```
3. Summarize the score and the top issues per area for the user. Point at the report
   path. Note the score is advisory (open ADR on formula).
4. Do NOT fix anything here. Hand fixes to the wiki agent (wiki-lint / wiki-reconcile)
   or the user.

## Boundaries

- Backend-owned. Writes ONLY `meta/health_report/`. Reads wiki/, objective/, research/
  read-only.
- $0: no model call, no network, no API key.
- Does not take vault locks (read-only over content; its own report dir is uncontended).
- Use `--area wiki` to reproduce the wiki-only subset (identical to wiki-health).
