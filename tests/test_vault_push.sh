#!/usr/bin/env bash
# test_vault_push.sh - hermetic tests for scripts/vault_push.sh.
#
# Creates a temp working git repo + a temp BARE remote under mktemp.
# Wires origin, exercises commit+push and the nothing-to-commit no-op.
# No network. Never touches the real vault.
#
# Asserts:
#   1. A new file is committed with the supplied message and pushed to the bare remote.
#   2. Running vault_push.sh a second time (no new changes) exits 0 and creates no new commit.
#   3. The commit is visible in the bare remote log.
#
# Usage: bash tests/test_vault_push.sh

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUSH_SH="$REPO/scripts/vault_push.sh"

PASS=0
FAIL=0

assert_eq() {
  if [ "$2" = "$3" ]; then
    echo "OK   $1"
    PASS=$((PASS + 1))
  else
    echo "FAIL $1: expected '$2', got '$3'"
    FAIL=$((FAIL + 1))
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  case "$haystack" in
    *"$needle"*) echo "OK   $label"; PASS=$((PASS + 1)) ;;
    *)           echo "FAIL $label: '$needle' not found in output"; FAIL=$((FAIL + 1)) ;;
  esac
}

# ── sandbox setup ─────────────────────────────────────────────────────────────
SANDBOX=$(mktemp -d /tmp/vault-push-test-XXXXXX)
trap 'rm -rf "$SANDBOX"' EXIT

export GIT_AUTHOR_NAME=test     GIT_AUTHOR_EMAIL=test@t
export GIT_COMMITTER_NAME=test  GIT_COMMITTER_EMAIL=test@t

BARE="$SANDBOX/remote.git"
WORK="$SANDBOX/vault"

# Create bare remote.
git init -q --bare "$BARE"

# Create working repo, wire origin, seed with an initial commit so the branch exists.
git init -q "$WORK"
git -C "$WORK" remote add origin "$BARE"
echo "seed" > "$WORK/seed.md"
git -C "$WORK" add seed.md
git -C "$WORK" commit -q -m "seed"
DEFBRANCH=$(git -C "$WORK" rev-parse --abbrev-ref HEAD)
git -C "$WORK" push -q origin "$DEFBRANCH"

echo "=== test_vault_push.sh ==="
echo "sandbox : $SANDBOX"
echo "branch  : $DEFBRANCH"
echo ""

# ── test 1: new file committed with supplied message and pushed ────────────────
echo "--- test 1: commit + push with explicit message ---"
echo "new content" > "$WORK/note.md"

OUTPUT=$(VAULT_ROOT="$WORK" bash "$PUSH_SH" "test msg" 2>&1)
RC=$?
echo "$OUTPUT"
echo ""

assert_eq "exit 0 on success" "0" "$RC"
assert_contains "output mentions staged files" "Staged files:" "$OUTPUT"
assert_contains "output mentions commit sha"   "Committed:"    "$OUTPUT"
assert_contains "output mentions push OK"      "Push: OK"      "$OUTPUT"

# Verify commit message appears in working repo log.
LOG_WORK=$(git -C "$WORK" log --oneline)
assert_contains "commit message in working log" "test msg" "$LOG_WORK"

# Verify commit is visible in the bare remote log.
LOG_BARE=$(git -C "$BARE" log --oneline)
assert_contains "commit visible in bare remote" "test msg" "$LOG_BARE"

# ── test 2: nothing-to-commit no-op exits 0 and adds no new commit ────────────
echo "--- test 2: nothing-to-commit no-op ---"
COMMITS_BEFORE=$(git -C "$WORK" rev-list --count HEAD)

OUTPUT2=$(VAULT_ROOT="$WORK" bash "$PUSH_SH" 2>&1)
RC2=$?
echo "$OUTPUT2"
echo ""

assert_eq "exit 0 on no-op" "0" "$RC2"
assert_contains "no-op reports nothing to commit" "Nothing to commit" "$OUTPUT2"

COMMITS_AFTER=$(git -C "$WORK" rev-list --count HEAD)
assert_eq "no new commit created" "$COMMITS_BEFORE" "$COMMITS_AFTER"

# ── test 3: default timestamp message used when no arg supplied ───────────────
echo "--- test 3: default timestamp commit message ---"
echo "another change" > "$WORK/note2.md"

OUTPUT3=$(VAULT_ROOT="$WORK" bash "$PUSH_SH" 2>&1)
RC3=$?
echo "$OUTPUT3"
echo ""

assert_eq "exit 0 for timestamp commit" "0" "$RC3"
LOG3=$(git -C "$WORK" log -1 --format="%s")
# Default message starts with "vault sync "
case "$LOG3" in
  "vault sync "*) echo "OK   default commit message starts with 'vault sync '"; PASS=$((PASS + 1)) ;;
  *)              echo "FAIL default commit message: got '$LOG3'";               FAIL=$((FAIL + 1)) ;;
esac

# Verify the timestamp commit is also in the bare remote.
LOG_BARE3=$(git -C "$BARE" log --oneline)
assert_contains "timestamp commit pushed to bare remote" "vault sync" "$LOG_BARE3"

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Pass: $PASS  Fail: $FAIL"
if [ $FAIL -gt 0 ]; then
  exit 1
fi
echo "All vault-push tests passed."
