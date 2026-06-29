---
name: vault-push
description: >
  Commit and push all vault items to the vault's git remote. Stages everything
  under VAULT_ROOT, commits (with an optional message), and pushes to origin.
  Triggers on: "push the vault", "sync the vault", "commit and push the vault",
  "save vault to git".
allowed-tools: Bash
---

**Ownership: `backend` agent.** If you are NOT the `backend` subagent (e.g. the main orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: backend`, pass the user's full request, let the backend agent run the steps below, and relay its result. Do NOT run the steps yourself - running as the `backend` agent is what activates the RBAC/write-scope boundary. If you ARE the `backend` agent, proceed.

# vault-push: commit and push the vault git repo

Owner: **backend**. This skill stages, commits, and pushes the active Obsidian
vault repo to its configured git remote (`origin`). It operates on the VAULT
repo (at `$VAULT_ROOT`) -- NOT the code repo.

## Steps

1. Run the push script (the optional message becomes the commit message; omit
   it to use the default UTC timestamp):
   ```bash
   bash scripts/vault_push.sh "<optional message>"
   ```
   The script resolves the vault root automatically: if `$VAULT_ROOT` is set
   it is used directly; otherwise it is exported via:
   ```bash
   eval "$(python -m agents.vault_config env)"
   ```

2. Report the staged file count, whether a commit was made (and its short SHA),
   and the push result to the user.

## What the script does

Cross-host-safe operation order:

1. If `origin` is configured: `git pull --rebase --autostash` so local commits
   replay on top of the remote (avoids diverged-history errors). Skipped cleanly
   if no remote exists.
2. `git add -A` - stage everything under VAULT_ROOT.
3. If nothing is staged: print "nothing to commit" and skip the commit step (but
   still allow pushing any rebased commits).
4. Else commit: use the CLI argument as the message, or fall back to
   `vault sync <UTC timestamp>`.
5. If `origin` is configured: `git push`; else print
   "no remote configured - skipping push".

## Boundaries

- Writes and pushes the VAULT git repo only. Does not touch the code repo.
- Backend-owned; RBAC enforced by dispatch header above.
- Layer-1 `vault_lease.sh` lease integration is deferred to Phase 4. Until then
  the script does not acquire a cross-host lease before pushing.
- Exit 0 on success or clean no-op; non-zero on real git failure.
