#!/usr/bin/env bash
# vault_push.sh — commit and push the active Obsidian vault git repo.
#
# Usage: bash scripts/vault_push.sh ["optional commit message"]
#
# If VAULT_ROOT is already set (e.g. by a test harness), it is used directly.
# Otherwise the active vault is resolved via `python -m agents.vault_config env`.
#
# Cross-host-safe operation order:
#   1. pull --rebase --autostash (replay local commits on top of remote)
#   2. git add -A
#   3. commit (skipped if nothing staged; uses $1 or a UTC timestamp)
#   4. push (skipped if no origin configured)
#
# Exit codes:
#   0 — success (including clean no-op)
#   non-zero — real git failure

set -uo pipefail

# ── resolve vault root ────────────────────────────────────────────────────────
if [ -z "${VAULT_ROOT:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  eval "$( cd "$SCRIPT_DIR" && python -m agents.vault_config env )"
fi

if [ -z "${VAULT_ROOT:-}" ]; then
  echo "ERROR: VAULT_ROOT could not be resolved." >&2
  exit 1
fi

COMMIT_MSG="${1:-}"

# ── helper: does origin exist? ────────────────────────────────────────────────
has_origin() {
  git -C "$VAULT_ROOT" remote get-url origin >/dev/null 2>&1
}

# ── 1. pull --rebase --autostash ─────────────────────────────────────────────
if has_origin; then
  echo "Pulling from origin (--rebase --autostash)..."
  CURRENT_BRANCH=$(git -C "$VAULT_ROOT" rev-parse --abbrev-ref HEAD)
  # If the branch has a configured upstream, a plain pull works.
  # If not (first push scenario), pull against origin/<branch> explicitly and
  # silence the "no tracking info" error gracefully.
  if git -C "$VAULT_ROOT" rev-parse --abbrev-ref --symbolic-full-name "@{u}" >/dev/null 2>&1; then
    git -C "$VAULT_ROOT" pull --rebase --autostash
  else
    # Try fetching and rebasing against origin/<branch> if that ref exists.
    git -C "$VAULT_ROOT" fetch origin "$CURRENT_BRANCH" >/dev/null 2>&1 && \
      git -C "$VAULT_ROOT" rebase "origin/$CURRENT_BRANCH" >/dev/null 2>&1 || \
      echo "No upstream branch yet - skipping pull."
  fi
else
  echo "No remote origin configured - skipping pull."
fi

# ── 2. stage everything ───────────────────────────────────────────────────────
git -C "$VAULT_ROOT" add -A

# count staged files
STAGED_COUNT=$(git -C "$VAULT_ROOT" diff --cached --name-only | wc -l | tr -d ' ')
echo "Staged files: $STAGED_COUNT"

# ── 3. commit (or skip) ───────────────────────────────────────────────────────
COMMITTED=0
COMMIT_SHA=""
if git -C "$VAULT_ROOT" diff --cached --quiet; then
  echo "Nothing to commit - working tree clean."
else
  if [ -z "$COMMIT_MSG" ]; then
    COMMIT_MSG="vault sync $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi
  git -C "$VAULT_ROOT" commit -m "$COMMIT_MSG"
  COMMITTED=1
  COMMIT_SHA=$(git -C "$VAULT_ROOT" rev-parse --short HEAD)
  echo "Committed: $COMMIT_SHA  ($COMMIT_MSG)"
fi

# ── 4. push ───────────────────────────────────────────────────────────────────
if has_origin; then
  # Use --set-upstream so first-push on a branch without tracking info works.
  CURRENT_BRANCH=$(git -C "$VAULT_ROOT" rev-parse --abbrev-ref HEAD)
  git -C "$VAULT_ROOT" push --set-upstream origin "$CURRENT_BRANCH"
  echo "Push: OK"
else
  echo "No remote configured - skipping push."
fi

# ── report ────────────────────────────────────────────────────────────────────
echo ""
echo "vault-push summary:"
echo "  staged files : $STAGED_COUNT"
if [ "$COMMITTED" -eq 1 ]; then
  echo "  commit       : $COMMIT_SHA  ($COMMIT_MSG)"
else
  echo "  commit       : (none - nothing to commit)"
fi
if has_origin; then
  echo "  push         : pushed to origin"
else
  echo "  push         : skipped (no remote)"
fi
