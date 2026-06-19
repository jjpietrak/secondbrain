#!/usr/bin/env bash
# vault_lease.sh - Layer-1 cross-host write lease for the vault git repo.
#
# WHY (two layers of write safety):
#   Layer-2 (wiki-lock.sh) = per-FILE advisory locks INSIDE one checkout, guarding
#     parallel sub-agents on the same host from trampling the same page.
#   Layer-1 (this script)  = a single CROSS-HOST mutual-exclusion lease over the
#     WHOLE vault, committed into the vault's own git repo so a second machine
#     (e.g. the nightly box AND your laptop) cannot both run an automated write
#     batch at once. The lease is a committed+pushed file, so contention resolves
#     through git's non-fast-forward push rejection (atomic at the remote).
#
# POLICY - HUMANS NEVER BLOCK:
#   The lease is consulted ONLY by AUTOMATED writers (nightly/orchestrator). A
#   human editing the vault in Obsidian NEVER calls acquire and is NEVER blocked
#   or waited on. Automated writers call `acquire --holder <id> --ttl <sec> --mode auto`;
#   on `--mode auto` a held/valid lease causes a HARMLESS SKIP (exit 75), not a wait.
#
# Lease file: <vault>/.vault-meta/vault-lease  (committed)
#   JSON: {"holder","host","pid","acquired_at","expires_at"}  (ISO-8601 UTC times)
#
# Verbs:
#   acquire --holder <id> --ttl <sec> [--mode auto]
#       git pull --rebase --autostash; read lease; if absent OR expired (now >
#       expires_at, reaping a crashed holder) -> write lease, git add/commit/push.
#       A non-fast-forward push rejection = LOST THE RACE -> exit 75 (back off).
#       If held by someone else and still valid -> exit 75 (`--mode auto` = skip).
#       Default --ttl 1800.
#   release --holder <id>
#       Clear the lease (ONLY if we currently hold it), commit + push. Idempotent.
#   status
#       Print holder / expiry / now (and whether expired).
#
# Exit codes:
#   0  - success (acquired / released / status printed)
#   75 - lease held by a valid holder, or lost the push race -> automated writer skips
#   2  - usage error
#   1  - git/environment error
#
# WIRING NOTE (RESOLVED decision #8): BUILD + TEST in P0, but DEFER live wiring to
# Phase 4 (the nightly orchestrator does not exist yet). Nothing in the P1 runtime
# path calls this script. The nightly batch will wrap its writes in:
#   vault_lease.sh acquire --holder nightly-$(hostname) --ttl 3600 --mode auto || exit 0
#   ... do the batch ...
#   vault_lease.sh release --holder nightly-$(hostname)
#
# Resolution of the vault repo (the git repo the lease is committed into):
#   1. $WIKI_LOCK_VAULT  - test/override hook (kept consistent with wiki-lock.sh)
#   2. $VAULT_ROOT       - exported by `agents.vault_config env`
#   3. `agents.vault_config path`
set -uo pipefail

# ---- resolve the vault repo --------------------------------------------------
resolve_vault() {
  if [ -n "${WIKI_LOCK_VAULT:-}" ]; then
    echo "$WIKI_LOCK_VAULT"; return 0
  fi
  if [ -n "${VAULT_ROOT:-}" ]; then
    echo "$VAULT_ROOT"; return 0
  fi
  local code_path py
  code_path="${CODE_PATH:-/home/jpietrak/second_brain}"
  py="${PY:-$code_path/.venv/bin/python}"
  [ -x "$py" ] || py="python3"
  (cd "$code_path" 2>/dev/null && "$py" -m agents.vault_config path 2>/dev/null) || true
}

VAULT="$(resolve_vault)"
[ -n "$VAULT" ] || { echo "vault_lease: cannot resolve vault root" >&2; exit 1; }
META_DIR="$VAULT/.vault-meta"
LEASE_FILE="$META_DIR/vault-lease"

die()  { echo "ERR: $*" >&2; exit "${2:-2}"; }
log()  { echo "$*" >&2; }
now_epoch() { date -u +%s; }
iso_utc()   { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ; }

git_v() { git -C "$VAULT" "$@"; }

# Read a field from the (JSON) lease file with the venv python (no jq dependency).
lease_field() {
  local field="$1"
  [ -f "$LEASE_FILE" ] || return 0
  local py="${PY:-${CODE_PATH:-/home/jpietrak/second_brain}/.venv/bin/python}"
  [ -x "$py" ] || py="python3"
  LEASE_FILE="$LEASE_FILE" FIELD="$field" "$py" - <<'PY' 2>/dev/null || true
import os, json
try:
    with open(os.environ["LEASE_FILE"]) as f:
        d = json.load(f)
    v = d.get(os.environ["FIELD"], "")
    print(v if v is not None else "")
except Exception:
    pass
PY
}

write_lease() {
  # args: holder ttl
  local holder="$1" ttl="$2" now exp
  now="$(now_epoch)"; exp=$((now + ttl))
  mkdir -p "$META_DIR"
  local py="${PY:-${CODE_PATH:-/home/jpietrak/second_brain}/.venv/bin/python}"
  [ -x "$py" ] || py="python3"
  HOLDER="$holder" HOST="$(hostname)" PID="$$" \
    ACQ="$(iso_utc "$now")" EXP="$(iso_utc "$exp")" \
    LEASE_FILE="$LEASE_FILE" "$py" - <<'PY'
import os, json
d = {
    "holder": os.environ["HOLDER"],
    "host": os.environ["HOST"],
    "pid": int(os.environ["PID"]),
    "acquired_at": os.environ["ACQ"],
    "expires_at": os.environ["EXP"],
}
with open(os.environ["LEASE_FILE"], "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
PY
}

# Returns 0 if the lease is currently held by a VALID (non-expired) holder.
lease_valid_held() {
  [ -f "$LEASE_FILE" ] || return 1
  local exp_iso exp_epoch now
  exp_iso="$(lease_field expires_at)"
  [ -n "$exp_iso" ] || return 1
  exp_epoch="$(date -u -d "$exp_iso" +%s 2>/dev/null || true)"
  [ -n "$exp_epoch" ] || return 1
  now="$(now_epoch)"
  [ "$now" -lt "$exp_epoch" ]   # held only while now < expires_at
}

# ---- verbs -------------------------------------------------------------------
cmd_acquire() {
  local holder="" ttl=1800 mode=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --holder) holder="$2"; shift 2 ;;
      --ttl)    ttl="$2"; shift 2 ;;
      --mode)   mode="$2"; shift 2 ;;
      *) die "acquire: unknown arg: $1" ;;
    esac
  done
  [ -n "$holder" ] || die "acquire needs --holder <id>"

  # Sync with the remote first so we see other hosts' leases (best-effort; a fresh
  # local-only repo with no upstream simply skips the pull).
  if git_v rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    git_v pull --rebase --autostash >/dev/null 2>&1 || true
  fi

  # If a valid lease is held by someone else, defer (humans never block; auto = skip).
  if lease_valid_held; then
    local cur; cur="$(lease_field holder)"
    if [ "$cur" != "$holder" ]; then
      log "vault_lease: held by '$cur' until $(lease_field expires_at) -> defer (mode=${mode:-none})"
      return 75
    fi
    # We already hold it: refresh the TTL (re-acquire is idempotent for the holder).
  fi
  # Absent OR expired (reap a crashed holder) OR ours -> take it.

  write_lease "$holder" "$ttl"
  git_v add .vault-meta/vault-lease >/dev/null 2>&1 || die "git add failed" 1
  if git_v diff --cached --quiet; then
    return 0   # nothing changed (e.g. identical re-write) - still hold it
  fi
  git_v -c user.email=lease@secondbrain -c user.name=vault-lease \
        commit -q -m "lease: acquire by $holder (ttl=${ttl}s)" >/dev/null 2>&1 \
        || die "git commit failed" 1

  # Push only if there is an upstream. A non-fast-forward rejection = lost the race.
  if git_v rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    if ! git_v push >/dev/null 2>&1; then
      log "vault_lease: push rejected (non-fast-forward) -> lost the race, backing off"
      git_v reset --hard 'HEAD~1' >/dev/null 2>&1 || true
      git_v pull --rebase --autostash >/dev/null 2>&1 || true
      return 75
    fi
  fi
  return 0
}

cmd_release() {
  local holder=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --holder) holder="$2"; shift 2 ;;
      *) die "release: unknown arg: $1" ;;
    esac
  done
  [ -n "$holder" ] || die "release needs --holder <id>"

  if git_v rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    git_v pull --rebase --autostash >/dev/null 2>&1 || true
  fi

  [ -f "$LEASE_FILE" ] || return 0   # nothing to release
  local cur; cur="$(lease_field holder)"
  if [ -n "$cur" ] && [ "$cur" != "$holder" ]; then
    log "vault_lease: release skipped - lease held by '$cur', not '$holder'"
    return 0
  fi

  rm -f "$LEASE_FILE"
  git_v add -A .vault-meta/vault-lease >/dev/null 2>&1 || true
  git_v rm --cached --ignore-unmatch .vault-meta/vault-lease >/dev/null 2>&1 || true
  if git_v diff --cached --quiet; then
    return 0
  fi
  git_v -c user.email=lease@secondbrain -c user.name=vault-lease \
        commit -q -m "lease: release by $holder" >/dev/null 2>&1 || die "git commit failed" 1
  if git_v rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    git_v push >/dev/null 2>&1 || log "vault_lease: release push failed (will reconcile next pull)"
  fi
  return 0
}

cmd_status() {
  # Sync first so status reflects other hosts' leases (best-effort).
  if git_v rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    git_v pull --rebase --autostash >/dev/null 2>&1 || true
  fi
  local now; now="$(iso_utc "$(now_epoch)")"
  if [ ! -f "$LEASE_FILE" ]; then
    echo "lease: FREE (no lease file)"
    echo "now:   $now"
    return 0
  fi
  local holder host exp
  holder="$(lease_field holder)"; host="$(lease_field host)"; exp="$(lease_field expires_at)"
  if lease_valid_held; then
    echo "lease: HELD by $holder@$host"
  else
    echo "lease: EXPIRED (reapable) - last holder $holder@$host"
  fi
  echo "expires_at: $exp"
  echo "now:        $now"
  return 0
}

# ---- dispatch ----------------------------------------------------------------
[ $# -ge 1 ] || die "usage: vault_lease.sh {acquire|release|status} [args]"
CMD="$1"; shift
case "$CMD" in
  acquire) cmd_acquire "$@" ;;
  release) cmd_release "$@" ;;
  status)  cmd_status "$@" ;;
  *) die "unknown command: $CMD (try acquire|release|status)" ;;
esac
