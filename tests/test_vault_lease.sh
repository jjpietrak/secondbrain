#!/usr/bin/env bash
# test_vault_lease.sh - hermetic test for the Layer-1 git lease (scripts/vault_lease.sh, P0.3).
#
# Models two hosts contending over one vault repo via two local `git clone`s of a
# bare repo (the shared "remote"), all under mktemp. No network.
#
# Asserts:
#   1. clone A acquires the lease (exit 0).
#   2. clone B `acquire --mode auto` while A's lease is valid -> exit 75 (mutual
#      exclusion; the automated writer defers, harmless skip).
#   3. after a TTL=0 expiry, B acquires (reaps the crashed/expired holder, exit 0).
#   4. a simulated HUMAN write (a normal commit, NO acquire call) is never blocked.
#
# Usage: bash tests/test_vault_lease.sh

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEASE="$REPO/scripts/vault_lease.sh"

PASS=0
FAIL=0
assert_eq() {
  if [ "$2" = "$3" ]; then echo "OK   $1"; PASS=$((PASS+1));
  else echo "FAIL $1: expected '$2', got '$3'"; FAIL=$((FAIL+1)); fi
}

SANDBOX=$(mktemp -d /tmp/vault-lease-test-XXXXXX)
trap 'rm -rf "$SANDBOX"' EXIT

export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@t
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@t

BARE="$SANDBOX/remote.git"
A="$SANDBOX/cloneA"
B="$SANDBOX/cloneB"

git init -q --bare "$BARE"

# Seed the bare repo with an initial commit (a .vault-meta dir) via a scratch clone.
SEED="$SANDBOX/seed"
git clone -q "$BARE" "$SEED"
mkdir -p "$SEED/.vault-meta"
echo "seed" > "$SEED/.vault-meta/.gitkeep"
git -C "$SEED" add -A
git -C "$SEED" commit -q -m "seed"
# Push to whatever the default branch is named.
DEFBRANCH=$(git -C "$SEED" rev-parse --abbrev-ref HEAD)
git -C "$SEED" push -q origin "$DEFBRANCH"

git clone -q "$BARE" "$A"
git clone -q "$BARE" "$B"

# Run the lease against a given clone (clone IS the vault root via WIKI_LOCK_VAULT).
lease_in() { local dir="$1"; shift; WIKI_LOCK_VAULT="$dir" PY="$REPO/.venv/bin/python" bash "$LEASE" "$@"; }

echo "=== test_vault_lease.sh ==="
echo "sandbox: $SANDBOX  (branch=$DEFBRANCH)"
echo ""

# 1. A acquires (long TTL).
lease_in "$A" acquire --holder hostA --ttl 1800 --mode auto >/dev/null 2>&1
assert_eq "A acquires lease" "0" "$?"

# 2. B acquire --mode auto while A's valid lease is published -> exit 75.
RC_B=$( (lease_in "$B" acquire --holder hostB --ttl 1800 --mode auto >/dev/null 2>&1); echo $? )
assert_eq "B deferred while A holds (exit 75)" "75" "$RC_B"

# status on B should report HELD by hostA after its pull.
STATUS_B=$(lease_in "$B" status 2>/dev/null)
case "$STATUS_B" in
  *"HELD by hostA"*) assert_eq "B status sees A's lease" "yes" "yes" ;;
  *) assert_eq "B status sees A's lease" "yes" "no($STATUS_B)" ;;
esac

# 3. Expiry/reaping: A re-acquires with TTL=0 (lease immediately expired), then B
#    can reap and take it.
lease_in "$A" acquire --holder hostA --ttl 0 --mode auto >/dev/null 2>&1
# tiny pause so now > expires_at deterministically
sleep 1
RC_B2=$( (lease_in "$B" acquire --holder hostB --ttl 1800 --mode auto >/dev/null 2>&1); echo $? )
assert_eq "B reaps expired lease and acquires (exit 0)" "0" "$RC_B2"

STATUS_A=$(lease_in "$A" status 2>/dev/null)
case "$STATUS_A" in
  *"HELD by hostB"*) assert_eq "A status now sees B's lease" "yes" "yes" ;;
  *) assert_eq "A status now sees B's lease" "yes" "no($STATUS_A)" ;;
esac

# 4. Human write: a normal commit with NO acquire call is never blocked.
#    (The lease is consulted only by automated writers; humans never call it.)
echo "human note" > "$A/wiki-note.md"
git -C "$A" add wiki-note.md
git -C "$A" commit -q -m "human edit (no lease)"
HUMAN_RC=$?
assert_eq "human write (no acquire) is never blocked" "0" "$HUMAN_RC"

# 5. Release frees the lease.
lease_in "$B" release --holder hostB >/dev/null 2>&1
STATUS_FREE=$(lease_in "$B" status 2>/dev/null)
case "$STATUS_FREE" in
  *"FREE"*) assert_eq "release frees the lease" "yes" "yes" ;;
  *) assert_eq "release frees the lease" "yes" "no($STATUS_FREE)" ;;
esac

echo ""
echo "Pass: $PASS  Fail: $FAIL"
if [ $FAIL -gt 0 ]; then exit 1; fi
echo "All vault-lease tests passed."
