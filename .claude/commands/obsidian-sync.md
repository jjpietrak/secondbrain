---
description: Commit and push the Obsidian vault git repo. Run after any substantive change to the vault.
category: maintenance
triggers_en: ["sync the vault", "commit the vault", "push the vault", "save vault to git"]
---

Deprecated - use the `vault-push` skill.

Run the backend agent with the `vault-push` skill:
```
/vault-push [optional commit message]
```

This command is retired as of v0.2. The `vault-push` skill (owned by the `backend` agent,
`skills/vault-push/SKILL.md`) supersedes it with full RBAC enforcement, cross-host-safe
pull-rebase-autostash before push, explicit staged-file reporting, and clean no-op handling.
