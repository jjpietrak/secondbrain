---
description: "[DEPRECATED] Use the wiki-ingest skill instead."
category: vault
---

Deprecated - use the `wiki-ingest` skill.

Run the wiki agent with the `wiki-ingest` skill:
```
/wiki-ingest <file|url|text>
```

This command is retired as of v0.2. The `wiki-ingest` skill (owned by the `wiki` agent,
`skills/wiki-ingest/SKILL.md`) supersedes it with full RBAC enforcement, the v0.2 vault
schema (wiki/entities, wiki/concepts, wiki/synthesis, wiki/sources), the approval queue
(waiting_approval/rejected statuses), and wikilink-based citations.
