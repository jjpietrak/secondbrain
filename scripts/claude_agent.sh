#!/bin/bash
# Tier-2 Claude invocation: subscription / Agent SDK credit, NEVER the paid API key.
#
# Usage: scripts/claude_agent.sh [claude -p flags] "prompt"
#   e.g. scripts/claude_agent.sh --permission-mode acceptEdits "Run /obsidian-lint"
#
# Loads CLAUDE_CODE_OAUTH_TOKEN from .env and unsets ANTHROPIC_API_KEY so the call
# draws Agent SDK credit ($0 marginal) instead of pay-as-you-go billing. Do NOT pass
# --bare (it forces API-key auth and ignores OAuth).
set -euo pipefail

CODE_PATH="${CODE_PATH:-/home/jpietrak/second_brain}"
ENV_FILE="$CODE_PATH/.env"

if [ -f "$ENV_FILE" ]; then
  token="$(grep -E '^CLAUDE_CODE_OAUTH_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  if [ -n "$token" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$token"
  fi
fi

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "claude_agent.sh: CLAUDE_CODE_OAUTH_TOKEN not set (run 'claude setup-token')." >&2
  exit 1
fi

# Critical: keep the metered API key out of this process.
unset ANTHROPIC_API_KEY

exec claude -p "$@"
