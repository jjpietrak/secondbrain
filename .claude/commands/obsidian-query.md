---
description: "[DEPRECATED] Use the wiki-query skill instead."
category: vault
---

Deprecated - use the `wiki-query` skill.

Run the wiki agent with the `wiki-query` skill:
```
/wiki-query <question>
```

This command is retired as of v0.2. The `wiki-query` skill (owned by the `wiki` agent,
`skills/wiki-query/SKILL.md`) supersedes it with BM25 + contextual-prefix + ollama cosine
rerank retrieval via `wiki-retrieve`, wikilink-based cited answers, and proper RBAC.
