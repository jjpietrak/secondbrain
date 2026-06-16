---
description: Lint the vault for structural health - orphans, dead wikilinks, contradictions, stale pages, missing frontmatter - and write a report to meta/health_report.md.
category: maintenance
triggers_en: ["lint the vault", "vault health", "check the wiki", "find broken links"]
---

Execute a structural health pass over the vault at `/mnt/c/Obsidian/Inference-Disagg/`. Read
`/mnt/c/Obsidian/Inference-Disagg/_CLAUDE.md` first for vault rules.

Scope: `/mnt/c/Obsidian/Inference-Disagg/wiki/**` (skip `_template.md` files and `index.md`, `log.md`,
`hot.md`). If `/home/jpietrak/second_brain/agents/vault_health.py` exists, you MAY run it
(`uv run python agents/vault_health.py` from the code repo) and fold its output in;
otherwise perform the checks directly.

Checks:
1. **Orphaned pages** — wiki pages with no inbound `[[wikilink]]` from any other page.
2. **Dead wikilinks** — `[[targets]]` that resolve to no existing page (by filename/alias).
3. **Missing frontmatter** — pages missing any of the required fields `type, created, updated, sources`.
4. **Stale pages** — `updated` older than 90 days while the cited source is unchanged.
5. **Empty / stub pages** — under ~50 words of body content.
6. **Contradictions** — scan for pages making conflicting claims about the same entity/concept. Flag, do not auto-resolve here (use `/obsidian-reconcile` for that).

Output:
- Write a report to `/mnt/c/Obsidian/Inference-Disagg/meta/health_report.md` with: generation timestamp,
  total wiki page count, a **health score** (start at 100, −2 per issue, floor 0), and one
  section per check listing the affected files (cap each list at 20, note "...and N more").
- Append one row to `/mnt/c/Obsidian/Inference-Disagg/wiki/log.md`:
  `## [YYYY-MM-DD] lint | - | score N/100 | O orphans, D dead links, S stale`
- Do NOT modify the linted pages — this command only reports. Fixes are a separate pass.

Report the health score and the top issues back to the user.
