---
description: Commit and push the Obsidian vault git repo. Run after any substantive change to the vault.
category: maintenance
triggers_en: ["sync the vault", "commit the vault", "push the vault", "save vault to git"]
---

Sync the vault git repo at `$VAULT_ROOT/`. Run these from the code repo or anywhere
(commands use `-C`):

1. Stage everything:
   ```bash
   git -C $VAULT_ROOT add -A
   ```
2. Show what changed (for the commit summary):
   ```bash
   git -C $VAULT_ROOT status --short
   ```
3. Commit (skip if nothing staged). Use a message summarising the operation that triggered
   the sync, falling back to a timestamp:
   ```bash
   git -C $VAULT_ROOT commit -m "vault sync $(date +%Y-%m-%d-%H%M)" || echo "nothing to commit"
   ```
4. Push if a remote is configured:
   ```bash
   git -C $VAULT_ROOT remote get-url origin >/dev/null 2>&1 \
     && git -C $VAULT_ROOT push 2>&1 \
     || echo "no remote configured — skipping push"
   ```

If `$ARGUMENTS` is provided, use it as the commit message instead of the default.
Report what was committed (file count) and whether the push succeeded.
