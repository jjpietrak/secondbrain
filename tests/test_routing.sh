#!/bin/bash
# Smoke-test LiteLLM routing to all three backends.
# Usage: bash tests/test_routing.sh
CODE_PATH="${CODE_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BASE="http://localhost:${LITELLM_PORT:-4000}"
MK="$(grep -E '^LITELLM_MASTER_KEY=' "$CODE_PATH/.env" | head -1 | cut -d= -f2-)"

# Wait for readiness (up to ~40s)
ready=0
for i in $(seq 1 20); do
  if curl -s --max-time 3 "$BASE/health/readiness" >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
echo "readiness_reachable=$ready"
curl -s --max-time 5 "$BASE/health/readiness"; echo

test_route() {
  local role="$1" timeout="$2"
  local resp
  resp="$(curl -s --max-time "$timeout" -X POST "$BASE/v1/chat/completions" \
    -H "Authorization: Bearer $MK" -H "Content-Type: application/json" \
    -d "{\"model\":\"$role\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly the word: pong\"}],\"max_tokens\":20}")"
  echo "$resp" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print('  [$role] PARSE-ERROR', e); sys.exit()
if 'choices' in d:
    print('  [$role] OK ->', repr((d['choices'][0]['message'].get('content') or '').strip()))
else:
    print('  [$role] ERROR ->', json.dumps(d)[:300])
"
}

echo "--- bulk (ollama, may load model on first call) ---"; test_route bulk 120
echo "--- synthesis (anthropic haiku, metered ~\$0.0001) ---"; test_route synthesis 60
echo "--- validation (gemini flash) ---"; test_route validation 60
