#!/bin/bash
# Tier-2 Claude invocation: subscription / Agent SDK credit, NEVER the paid API key.
#
# Usage: scripts/claude_agent.sh [--agent <id>] [claude -p flags] "prompt"
#   e.g. scripts/claude_agent.sh --agent backend "say hi"
#        scripts/claude_agent.sh --agent wiki --permission-mode acceptEdits "Run /obsidian-lint"
#
# Loads CLAUDE_CODE_OAUTH_TOKEN from .env and unsets ANTHROPIC_API_KEY so the call
# draws Agent SDK credit ($0 marginal) instead of pay-as-you-go billing. Do NOT pass
# --bare (it forces API-key auth and ignores OAuth).
#
# USAGE CAPTURE (P0.1): instead of `exec claude -p`, this wrapper runs
#   claude -p --output-format json "$@"
# captures stdout, parses the usage with the venv python (no jq dependency), records
# a cost row via `agents.cost_tracker record ... --source agent-sdk-credit`, then
# re-emits ONLY the `.result` text to stdout so callers see the same text as before.
# The original exit code is preserved.
#
#   --agent <id>   attributes the spend to a role (the cost_tracker --action tag).
#                  Stripped from the args forwarded to claude. Defaults to "agent-sdk".
#
# Guards:
#   - If the caller already passed --output-format, we DO NOT wrap (pass through raw,
#     no double-wrap, no parsing); cost is not captured in that mode by design.
#   - On parse failure we still emit `.result` best-effort and record a zero-cost row
#     with a parse_error note (a call is never silently dropped from the ledger).
set -euo pipefail

CODE_PATH="${CODE_PATH:-/home/jpietrak/second_brain}"
ENV_FILE="$CODE_PATH/.env"
PY="${PY:-$CODE_PATH/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

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

# ---- arg parsing: strip --agent; detect a caller-supplied --output-format -------
AGENT="agent-sdk"
CALLER_OUTPUT_FORMAT=0
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --agent)
      AGENT="${2:-agent-sdk}"; shift 2 ;;
    --agent=*)
      AGENT="${1#--agent=}"; shift ;;
    --output-format|--output-format=*)
      CALLER_OUTPUT_FORMAT=1; ARGS+=("$1"); shift ;;
    *)
      ARGS+=("$1"); shift ;;
  esac
done

# ---- pass-through mode: caller asked for a specific output format ---------------
# Do not double-wrap or parse; just run with their format and preserve exit code.
if [ "$CALLER_OUTPUT_FORMAT" -eq 1 ]; then
  exec claude -p "${ARGS[@]}"
fi

# ---- capture mode: wrap in JSON, parse usage, record, re-emit .result -----------
set +e
RAW="$(claude -p --output-format json "${ARGS[@]}")"
CLAUDE_RC=$?
set -e

# Parse + record with the venv python (no jq). The parser prints the .result text
# to stdout and records the cost row as a side effect. On any parse failure it emits
# the raw payload best-effort and records a zero-cost parse_error row.
RAW="$RAW" AGENT="$AGENT" "$PY" - <<'PYEOF'
import os, sys, json

raw = os.environ.get("RAW", "")
agent = os.environ.get("AGENT", "agent-sdk")

sys.path.insert(0, os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
try:
    from agents import cost_tracker as ct
except Exception:
    ct = None

def record(in_tok, out_tok, cost, note="", model=""):
    if ct is None:
        return
    try:
        ct.record(action=agent, role=note, provider="anthropic",
                  input_tokens=in_tok or 0, output_tokens=out_tok or 0,
                  cost_usd=cost or 0.0, source="agent-sdk-credit",
                  model=model)
    except Exception:
        pass

try:
    data = json.loads(raw)
    usage = data.get("usage") or {}
    in_tok = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    # Cache tokens fold into the input side for accounting purposes.
    in_tok += (usage.get("cache_creation_input_tokens", 0) or 0)
    in_tok += (usage.get("cache_read_input_tokens", 0) or 0)
    cost = data.get("total_cost_usd", 0.0) or 0.0
    model = data.get("model", "") or ""
    record(in_tok, out_tok, cost, note="", model=model)
    result = data.get("result", "")
    sys.stdout.write(result if isinstance(result, str) else json.dumps(result))
    if not (isinstance(result, str) and result.endswith("\n")):
        sys.stdout.write("\n")
except Exception:
    # Parse failure: never silently drop the call. Record a zero-cost parse_error
    # row and emit the raw payload best-effort so the caller still gets text.
    record(0, 0, 0.0, note="parse_error")
    sys.stdout.write(raw)
    if not raw.endswith("\n"):
        sys.stdout.write("\n")
PYEOF

exit "$CLAUDE_RC"
