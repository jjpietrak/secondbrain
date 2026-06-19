# Locking reference - safe multi-writer vault mutation

> ## For future Claude
> This is the canonical procedure every wiki/research skill MUST follow before it
> writes to a SHARED append target. It is documented procedure embedded in each
> SKILL.md body, not enforced by a hook. Copy the snippet; do not reinvent it.
> ai-first: true

There are two layers of write safety. A skill at runtime uses Layer 2 only.

| Layer | Tool | Scope | Who calls it |
|-------|------|-------|--------------|
| 2 | `scripts/wiki-lock.sh` | per-FILE advisory lock inside one checkout | every skill, before touching a shared target |
| 1 | `scripts/vault_lease.sh` | whole-vault cross-HOST lease (committed to git) | the nightly/orchestrator ONLY (wired in Phase 4) |

Humans never block. The Layer-1 lease is consulted only by automated writers; a
person editing in Obsidian never calls either tool.

## Shared append targets (always lock before writing)

These files are written by more than one agent/skill and MUST be locked:

- `wiki/index.md`
- `wiki/log.md`
- `wiki/hot.md`
- `meta/ingest_index.json` and `meta/ingest_index.md`
- (Phase 2) `objective/index*`

Per-page writes to a single owned note do not strictly need a lock, but locking is
cheap and harmless; when in doubt, lock.

## Canonical lock snippet (acquire -> write -> release)

`wiki-lock.sh acquire` returns exit code 75 (EX_TEMPFAIL) when the path is held by a
fresh lock. The contract on rc=75: retry ONCE after 2s, then log and SKIP (never
spin, never trample).

```bash
# Resolve the vault root once (skills run with $VAULT_ROOT exported by
# `agents.vault_config env`; wiki-lock.sh also resolves it itself if unset).
LOCK="scripts/wiki-lock.sh"
TARGET="wiki/log.md"          # vault-relative path

acquire_or_skip() {
  # $1 = vault-relative path. Returns 0 if acquired, 1 if we should skip.
  local path="$1"
  if bash "$LOCK" acquire "$path"; then return 0; fi
  # rc=75 (held): back off once, then give up gracefully.
  sleep 2
  if bash "$LOCK" acquire "$path"; then return 0; fi
  echo "wiki-lock: $path still held after retry -> skipping this write" >&2
  return 1
}

if acquire_or_skip "$TARGET"; then
  # ... do the append/edit (a few ms to a couple of seconds) ...
  printf '%s\n' "- new entry" >> "$VAULT_ROOT/$TARGET"
  bash "$LOCK" release "$TARGET"
fi
```

## Multi-file writes: sorted-path order (deadlock avoidance)

When one operation touches several shared targets (e.g. ingest updates `index.md`,
`log.md`, and `hot.md` together), acquire ALL locks in sorted-path order, write,
then release in any order. Sorted order means two concurrent multi-writers can never
hold each other's next lock (no cyclic wait, so no deadlock).

```bash
PATHS=$(printf '%s\n' wiki/hot.md wiki/index.md wiki/log.md | sort)
held=()
ok=1
for p in $PATHS; do
  if acquire_or_skip "$p"; then held+=("$p"); else ok=0; break; fi
done
if [ "$ok" -eq 1 ]; then
  # ... all locks held: perform the writes ...
  :
fi
# Release everything we managed to take (safe even on partial acquisition).
for p in "${held[@]}"; do bash "$LOCK" release "$p"; done
```

## Layer-1 lease (Phase-4 wiring only - DO NOT call from a P1 skill)

The nightly orchestrator wraps its whole automated write batch:

```bash
HOLDER="nightly-$(hostname)"
if scripts/vault_lease.sh acquire --holder "$HOLDER" --ttl 3600 --mode auto; then
  # ... run the batch (sub-agents use the Layer-2 snippet above per file) ...
  scripts/vault_lease.sh release --holder "$HOLDER"
else
  # exit 75: another host holds the vault -> harmless skip this run.
  echo "vault lease held elsewhere -> skipping nightly batch" >&2
fi
```

## Admin hygiene

A SessionStart hook (or operator) periodically reaps abandoned per-file locks:

```bash
bash scripts/wiki-lock.sh clear-stale --max-age 3600
```
