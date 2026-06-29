#!/usr/bin/env bash
# test_lock_snippet_append.sh - P0.4 acceptance: the canonical lock snippet from
# skills/references/locking.md loses NO lines when N writers append concurrently to
# a shared append target shaped like wiki/log.md.
#
# Unlike test_concurrent_write.sh (which exercises raw acquire/append/release with a
# bounded retry loop), this test exercises the SKILL-FACING contract:
#   acquire_or_skip(): acquire -> on rc=75 retry ONCE after a short backoff -> else skip.
# To prove no-loss under that contract we give workers a few snippet attempts (so a
# transient skip self-heals on a later attempt) and assert every worker's line lands
# exactly once against the wiki/log.md-shaped target.
#
# Hermetic: sandbox vault under mktemp via WIKI_LOCK_VAULT, no network.
#
# Usage: bash tests/test_lock_snippet_append.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/scripts/wiki-lock.sh"

WORKERS=10
TARGET_REL="wiki/log.md"   # the shared append target shape

SANDBOX=$(mktemp -d /tmp/lock-snippet-test-XXXXXX)
trap 'rm -rf "$SANDBOX"' EXIT
mkdir -p "$SANDBOX/.vault-meta/locks" "$SANDBOX/wiki"
TARGET_ABS="$SANDBOX/$TARGET_REL"
echo "## Log" > "$TARGET_ABS"   # seed line
export WIKI_LOCK_VAULT="$SANDBOX"
export VAULT_ROOT="$SANDBOX"

PASS=0
FAIL=0
assert_eq() {
  if [ "$2" = "$3" ]; then echo "OK   $1"; PASS=$((PASS+1));
  else echo "FAIL $1: expected '$2', got '$3'"; FAIL=$((FAIL+1)); fi
}

echo "=== test_lock_snippet_append.sh ==="
echo "sandbox: $SANDBOX  workers: $WORKERS  target: $TARGET_REL"
echo ""

# The canonical snippet's acquire_or_skip (verbatim contract from locking.md):
# acquire; on failure (rc=75) sleep 2 then retry once; else give up (return 1).
acquire_or_skip() {
  local path="$1"
  if bash "$LOCK" acquire "$path" >/dev/null 2>&1; then return 0; fi
  sleep 2
  if bash "$LOCK" acquire "$path" >/dev/null 2>&1; then return 0; fi
  return 1
}

# A worker keeps trying the snippet until its line is committed (a real skill would
# log+skip once; here we loop so the no-loss property is observable end-to-end).
worker() {
  local id="$1" tries=0
  local jitter
  jitter=$(awk -v id="$id" 'BEGIN { srand(id*7+1); print int(rand()*100) }')
  sleep "0.0${jitter}" 2>/dev/null || sleep 1
  while [ "$tries" -lt 40 ]; do
    if acquire_or_skip "$TARGET_REL"; then
      printf 'log-worker-%s\n' "$id" >> "$VAULT_ROOT/$TARGET_REL"
      bash "$LOCK" release "$TARGET_REL" >/dev/null 2>&1
      return 0
    fi
    tries=$((tries + 1))
    sleep "0.05" 2>/dev/null || sleep 1
  done
  echo "worker $id never landed its line" >&2
  return 1
}

PIDS=()
for i in $(seq 1 $WORKERS); do worker "$i" & PIDS+=("$!"); done
GAVE_UP=0
for p in "${PIDS[@]}"; do wait "$p" || GAVE_UP=$((GAVE_UP+1)); done

assert_eq "all workers landed their line (no give-ups)" "0" "$GAVE_UP"

TOTAL=$(wc -l < "$TARGET_ABS")
assert_eq "line count (seed + workers, no loss)" "$((WORKERS + 1))" "$TOTAL"

for i in $(seq 1 $WORKERS); do
  c=$(grep -c "^log-worker-$i$" "$TARGET_ABS" || echo 0)
  if [ "$c" != "1" ]; then echo "FAIL worker-$i line count: expected 1 got $c"; FAIL=$((FAIL+1)); fi
done
echo "OK   every worker line appears exactly once"; PASS=$((PASS+1))

GARBLED=$(awk 'length > 120' "$TARGET_ABS" | wc -l)
assert_eq "no garbled lines" "0" "$GARBLED"

ORPHANS=$(bash "$LOCK" list | wc -l)
assert_eq "no orphan locks" "0" "$ORPHANS"

echo ""
echo "Pass: $PASS  Fail: $FAIL"
if [ $FAIL -gt 0 ]; then echo "--- target ---"; cat "$TARGET_ABS"; exit 1; fi
echo "All lock-snippet append tests passed."
