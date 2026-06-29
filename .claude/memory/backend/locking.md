---
type: reference/architecture
---
# Fact: two-layer write-safety model

Related: [[retrieval-pipeline]], [[rbac]], [[cost-ledger]]

The vault uses two complementary layers to prevent multi-writer corruption without ever
blocking human edits.

## Layer-2: per-file advisory locks (wiki-lock.sh)

Script: `scripts/wiki-lock.sh`. Guards concurrent sub-agents on the same host from
trampling the same vault page.

Design: age-based lockfiles (not flock), because acquire and release are separate bash
invocations from the same skill -- flock releases on process exit, which is too short-lived.
Lock files live at `<vault>/.vault-meta/locks/<sha1(path)>.lock` with atomic noclobber
creation plus epoch-timestamp staleness detection.

Semantics:
- `acquire <vault-rel-path>` -- atomically creates the lockfile; returns 0 (ok) or 75
  (EX_TEMPFAIL, held-fresh). Auto-reaps locks older than STALE_AFTER_SEC (default 60s).
- `release <vault-rel-path>` -- idempotent rm. Cross-process release is intentional:
  acquire and release are separate bash invocations from the same skill.
- `list` -- currently-held lock records.
- `clear-stale [--max-age N]` -- admin reaper (default max-age 3600s, distinct from
  per-acquire 60s threshold). Returns count removed.
- `peek <vault-rel-path>` -- prints holder info or "unheld"; never mutates.

Vault root resolution order: `$WIKI_LOCK_VAULT` (test override) > `$VAULT_ROOT` >
`agents.vault_config path` > script-parent fallback.

Skill wiring: every skill that writes to a shared append target (`wiki/index.md`,
`wiki/log.md`, `wiki/hot/hot.md`, `meta/ingest_index*`) wraps the write in the canonical
acquire -> write -> release pattern documented in `skills/references/locking.md`. For
multi-file writes, acquire in sorted-path order (deadlock-free). On rc=75: retry once
after 2s then log+skip.

## Layer-1: cross-host git lease (vault_lease.sh)

Script: `scripts/vault_lease.sh`. Guards automated writers running on different machines
(e.g. nightly box vs laptop) from running concurrent write batches against the vault git
repo.

Mechanism: a committed+pushed JSON file `<vault>/.vault-meta/vault-lease` (fields:
holder, host, pid, acquired_at, expires_at ISO-8601 UTC). Contention resolves via git's
non-fast-forward push rejection -- the lease is only "held" if it is committed to the
remote.

Verbs:
- `acquire --holder <id> --ttl <sec> [--mode auto]` -- git pull --rebase --autostash;
  read lease; if absent or expired: write, commit, push. Non-fast-forward push rejection
  = lost the race, exit 75. If held by another valid holder: exit 75. Default ttl=1800.
- `release --holder <id>` -- clear lease if we hold it, commit+push. Idempotent.
- `status` -- pull and print holder/expiry state.

Policy: **humans never block**. Only automated writers (the nightly orchestrator) call
acquire. With `--mode auto` a held lease causes a harmless skip (exit 75), never a wait.
Human edits in Obsidian never call acquire and are never blocked.

Nightly wiring pattern:
```
vault_lease.sh acquire --holder nightly-$(hostname) --ttl 3600 --mode auto || exit 0
...batch writes...
vault_lease.sh release --holder nightly-$(hostname)
```
