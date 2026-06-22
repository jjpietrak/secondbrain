# Backend vault-ops skills: vault-push + vault-health (2026-06-22, commit 664ef1d)

Two backend-owned skills added on user request ("add vault-push + vault-health, update docs").

## vault-push (`scripts/vault_push.sh` + `.claude/skills/vault-push/SKILL.md`)
- Commit & push the active VAULT git repo (resolves root via `agents.vault_config env`, or `$VAULT_ROOT`
  override for tests). Order: `git pull --rebase --autostash` (if remote) → `add -A` → commit (arg msg
  or `vault sync <UTC ts>`, skip if nothing staged) → push (first-push sets upstream). Reports file
  count / sha / push result; exit 0 on no-op.
- Formalizes the old v0.1 `.claude/commands/obsidian-sync.md`, which is now a deprecation-redirect.
- Layer-1 `vault_lease.sh` lease wiring is still deferred to Phase 4 (vault-push only does pull --rebase).
- Hermetic test `tests/test_vault_push.sh` (bare-remote sandbox, 12 assertions, no network/real vault).
- Vault repo remote: `origin = github.com/jjpietrak/secondbrain-matter.git`. Code repo:
  `github.com/jjpietrak/secondbrain.git`. (Standing rule: I do not push either myself without the user
  asking; vault-push is a capability the user/agents invoke.)

## vault-health (generalized `agents/vault_health.py` + `.claude/skills/vault-health/SKILL.md`)
- `audit(vault, areas=("wiki","objective","research"))`: area-aware walk, unified cross-area page index
  (so an objective node linking `[[wiki/...]]` resolves), per-`type` required-frontmatter map, per-area
  tagged issues + per-area report sections, `--area wiki|objective|research|all` (default all).
  `--area wiki` reproduces the old wiki-only behavior → `wiki-health` is now that subset (updated to call it).
- Score formula (100 - 2*issues) UNCHANGED but flagged advisory in-report (the floor-to-0 ADR is still open
  and now more pressing on the bigger scan). Report leads with per-area counts.
- Did NOT add `written_by` / objective-edge-orphan checks yet — those land WITH Phase 2.5; vault-health is
  then the verifier (post-backfill the objective Orphaned count should drop to ~0).
- LIVE read-only smoke (`--area objective --print-only`): 23 objective pages, 0 frontmatter/template/dup
  issues, **23 orphaned** — confirms the Phase 2.5 edge problem (every objective node lacks inbound links).

## Docs updated (frozen docs, per explicit user instruction)
- `docs/skills-description.md`: +2 rows in the `backend` section (vault-health, vault-push).
- `docs/vault-schema.md`: new "Backend audit & version-control capabilities" note — backend reads ALL
  markdown areas read-only for audits (resolves the matrix gap where backend Read was only `meta/`),
  and vault-push is version-control not content editing. Left the unrelated stale "open notes" (swap, etc.)
  for the user. Tests after: full suite **319 passed**.
