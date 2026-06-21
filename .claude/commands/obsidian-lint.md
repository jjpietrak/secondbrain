---
description: "[DEPRECATED] Use the wiki-lint or wiki-health skill instead."
category: maintenance
---

Deprecated - use the `wiki-lint` or `wiki-health` skill.

For structural lint (orphans, dead links, missing frontmatter, stale pages):
```
/wiki-lint
```

For a full health report with scoring:
```
/wiki-health
```

This command is retired as of v0.2. The `wiki-lint` skill (`skills/wiki-lint/SKILL.md`, owned
by the `wiki` agent) and the `wiki-health` skill (`skills/wiki-health/SKILL.md`, owned by the
`backend` agent, backed by `agents/vault_health.py`) supersede it with the v0.2 vault schema,
reports written to `meta/health_report/`, and proper RBAC.
