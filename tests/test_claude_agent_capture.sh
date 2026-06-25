#!/usr/bin/env bash
# test_claude_agent_capture.sh - hermetic test for scripts/claude_agent.sh usage capture (P0.1).
#
# Mocks `claude` on PATH with a fixture JSON payload, runs the wrapper, and asserts:
#   - only the `.result` text reached stdout (NOT the raw JSON wrapper)
#   - a cost_ledger.jsonl row was appended with source=agent-sdk-credit and
#     non-null input/output tokens parsed from the fixture
#   - the row's action == the --agent id passed in
#
# Hermetic: a sandbox CODE_PATH (symlinks the real agents/ + config/ so imports
# resolve, but logs/ lands in the sandbox), a fake .env with a dummy OAuth token,
# and a mock `claude` binary first on PATH. No network, no real claude, no paid key.
#
# Usage: bash tests/test_claude_agent_capture.sh

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="$REPO/scripts/claude_agent.sh"

PASS=0
FAIL=0
assert_eq() {
  if [ "$2" = "$3" ]; then echo "OK   $1"; PASS=$((PASS+1));
  else echo "FAIL $1: expected '$2', got '$3'"; FAIL=$((FAIL+1)); fi
}
assert_contains() {
  case "$3" in
    *"$2"*) echo "OK   $1"; PASS=$((PASS+1)) ;;
    *) echo "FAIL $1: '$3' does not contain '$2'"; FAIL=$((FAIL+1)) ;;
  esac
}
assert_not_contains() {
  case "$3" in
    *"$2"*) echo "FAIL $1: '$3' should NOT contain '$2'"; FAIL=$((FAIL+1)) ;;
    *) echo "OK   $1"; PASS=$((PASS+1)) ;;
  esac
}

SANDBOX=$(mktemp -d /tmp/claude-agent-test-XXXXXX)
trap 'rm -rf "$SANDBOX"' EXIT

# Sandbox CODE_PATH: symlink the package + config so imports work; logs/ is local.
mkdir -p "$SANDBOX/logs" "$SANDBOX/bin"
ln -s "$REPO/agents" "$SANDBOX/agents"
ln -s "$REPO/config" "$SANDBOX/config"
# Fake .env with a dummy OAuth token so the wrapper does not bail.
echo 'CLAUDE_CODE_OAUTH_TOKEN=dummy-test-token' > "$SANDBOX/.env"

# Mock `claude`: ignore args, print a fixed JSON payload (the shape claude -p
# --output-format json emits). The .result text is "hello from mock".
# model field is included so estimate_cost can pick the right rate (FU4).
cat > "$SANDBOX/bin/claude" <<'MOCK'
#!/usr/bin/env bash
cat <<'JSON'
{"type":"result","subtype":"success","is_error":false,"result":"hello from mock","session_id":"sess-abc123","total_cost_usd":0.0123,"num_turns":2,"model":"claude-haiku-4-5-20251001","usage":{"input_tokens":1500,"output_tokens":320,"cache_creation_input_tokens":40,"cache_read_input_tokens":10}}
JSON
MOCK
chmod +x "$SANDBOX/bin/claude"

echo "=== test_claude_agent_capture.sh ==="
echo "sandbox: $SANDBOX"
echo ""

LEDGER="$SANDBOX/logs/cost_ledger.jsonl"

# Run the wrapper with the mock claude first on PATH and the sandbox CODE_PATH.
OUT=$(PATH="$SANDBOX/bin:$PATH" CODE_PATH="$SANDBOX" PY="$REPO/.venv/bin/python" \
      VAULT="${VAULT:-example}" \
      bash "$WRAPPER" --agent backend "say hi" 2>/dev/null)
RC=$?

assert_eq "wrapper exit code" "0" "$RC"
assert_contains "stdout contains .result text" "hello from mock" "$OUT"
assert_not_contains "stdout does NOT contain raw JSON wrapper" "total_cost_usd" "$OUT"
assert_not_contains "stdout does NOT contain session_id" "session_id" "$OUT"

# Ledger row assertions.
if [ ! -f "$LEDGER" ]; then
  echo "FAIL ledger file created"; FAIL=$((FAIL+1))
else
  echo "OK   ledger file created"; PASS=$((PASS+1))
  ROW=$(tail -1 "$LEDGER")
  # Parse the row with python for robust field checks.
  CHECK=$(ROW="$ROW" "$REPO/.venv/bin/python" - <<'PY'
import os, json
r = json.loads(os.environ["ROW"])
ok = (
    r.get("source") == "agent-sdk-credit"
    and r.get("provider") == "anthropic"
    and r.get("action") == "backend"
    and isinstance(r.get("input_tokens"), int) and r["input_tokens"] > 0
    and isinstance(r.get("output_tokens"), int) and r["output_tokens"] > 0
    and r.get("cost_usd") == 0.0123
    # cache tokens fold into input: 1500 + 40 + 10 = 1550
    and r["input_tokens"] == 1550
    and r["output_tokens"] == 320
    # FU4: estimated_cost_usd present and non-negative on every row
    and "estimated_cost_usd" in r
    and isinstance(r.get("estimated_cost_usd"), (int, float))
    and r.get("estimated_cost_usd", -1) >= 0
    # model field recorded from the JSON payload
    and r.get("model") == "claude-haiku-4-5-20251001"
)
print("PASS" if ok else f"FAIL row={r}")
PY
)
  assert_eq "ledger row fields (source/tokens/cost/action)" "PASS" "$CHECK"
fi

echo ""
echo "Pass: $PASS  Fail: $FAIL"
if [ $FAIL -gt 0 ]; then exit 1; fi
echo "All claude_agent capture tests passed."
