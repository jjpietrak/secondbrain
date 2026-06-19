#!/usr/bin/env bash
# run_phase0.sh - run all Phase 0 hermetic tests and summarize. Green = exit 0.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TESTS=(
  test_claude_agent_capture
  test_wiki_lock
  test_concurrent_write
  test_vault_lease
  test_lock_snippet_append
)

fail=0
for t in "${TESTS[@]}"; do
  echo "########## $t ##########"
  if bash "tests/$t.sh" > "/tmp/$t.out" 2>&1; then
    tail -1 "/tmp/$t.out"
    echo "[$t] PASS"
  else
    cat "/tmp/$t.out"
    echo "[$t] FAIL"
    fail=1
  fi
  echo ""
done

if [ "$fail" -eq 0 ]; then
  echo "ALL PHASE 0 TESTS GREEN"
else
  echo "PHASE 0 HAS FAILURES"
fi
exit "$fail"
