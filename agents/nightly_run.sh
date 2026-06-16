#!/bin/bash
# Second Brain — Tier-2 LOCAL nightly run (2 AM via Windows Task Scheduler).
#
# Cost model: every `claude -p` call goes through scripts/claude_agent.sh, which uses
# CLAUDE_CODE_OAUTH_TOKEN (Agent SDK credit, $0 marginal) and unsets ANTHROPIC_API_KEY.
# Research crawls run as cloud Routines (Tier-1) and commit to the vault repo; this
# script just pulls what they wrote, then does the local-only work (ingest/lint/health/sync).
#
# Set DRY_RUN=1 to skip claude calls, commits, and pushes (prints what it would do).
set -uo pipefail

CODE_PATH="${CODE_PATH:-/home/jpietrak/second_brain}"
VAULT_PATH="${VAULT_PATH:-/mnt/c/Obsidian/Inference-Disagg}"
export CODE_PATH VAULT_PATH
DRY_RUN="${DRY_RUN:-0}"
AGENT="$CODE_PATH/scripts/claude_agent.sh"
CLAUDE_FLAGS=(--permission-mode acceptEdits --add-dir "$VAULT_PATH")

mkdir -p "$CODE_PATH/logs"
LOG="$CODE_PATH/logs/nightly-$(date +%Y%m%d).log"
exec > >(tee -a "$LOG") 2>&1
echo "=== Nightly run started: $(date) (DRY_RUN=$DRY_RUN) ==="

# Catch-up guard: skip if a run completed in the last 20h (WSL may have been asleep).
LAST_RUN=$(stat -c %Y "$CODE_PATH/logs/last_run" 2>/dev/null || echo 0)
HOURS_SINCE=$(( ( $(date +%s) - LAST_RUN ) / 3600 ))
if [ "$HOURS_SINCE" -lt 20 ]; then
  echo "Run already done ${HOURS_SINCE}h ago (<20h). Skipping."
  exit 0
fi

run_claude() {  # $1 = prompt
  if [ "$DRY_RUN" = "1" ]; then echo "[dry-run] claude_agent.sh ${CLAUDE_FLAGS[*]} \"$1\""; return 0; fi
  bash "$AGENT" "${CLAUDE_FLAGS[@]}" "$1" || echo "WARN: claude step failed"
}

# 0. Budget gate — stop before doing paid work if today's cap is already hit.
echo "--- [0/5] Budget check ---"
"$CODE_PATH/.venv/bin/python" "$CODE_PATH/agents/cost_tracker.py" check
if [ $? -ne 0 ] && [ "$(grep -E '^on_cap:' "$CODE_PATH/config/budget.yaml" | awk '{print $2}')" = "stop" ]; then
  echo "Budget cap reached — aborting paid steps."; SKIP_PAID=1
else
  SKIP_PAID=0
fi

# 1. Pull what the cloud Routines committed overnight.
echo "--- [1/5] Pull vault ---"
[ "$DRY_RUN" = "1" ] || git -C "$VAULT_PATH" pull --rebase --autostash 2>&1 || echo "WARN: pull failed"

# 2. Ingest new raw/ sources not yet summarised in wiki/sources/.
echo "--- [2/5] Ingest new raw sources ---"
if [ "$SKIP_PAID" = "0" ]; then
  NEW=$(find "$VAULT_PATH/raw" -name '*.md' -newer "$VAULT_PATH/wiki/log.md" 2>/dev/null | head -5)
  if [ -n "$NEW" ]; then
    while IFS= read -r src; do
      echo "Ingesting: $src"
      run_claude "Run the /obsidian-ingest command on the file: $src"
    done <<< "$NEW"
  else
    echo "No new raw sources."
  fi
fi

# 3. Lint pass.
echo "--- [3/5] Lint ---"
[ "$SKIP_PAID" = "0" ] && run_claude "Run the /obsidian-lint command on the vault."

# 4. Structural health audit (pure Python, free).
echo "--- [4/5] Vault health ---"
"$CODE_PATH/.venv/bin/python" "$CODE_PATH/agents/vault_health.py"
"$CODE_PATH/.venv/bin/python" "$CODE_PATH/agents/cost_tracker.py" report

# 4b. NotebookLM sync (opt-in, $0, non-fatal). Runs only if NLM_SYNC_NOTEBOOK is set
# and `nlm` auth is still valid (cookies last ~20 min — unattended runs often skip).
if [ -n "${NLM_SYNC_NOTEBOOK:-}" ]; then
  echo "--- [4b] NotebookLM sync ---"
  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] would sync NotebookLM notebook $NLM_SYNC_NOTEBOOK"
  elif nlm login --check >/dev/null 2>&1; then
    ( cd "$CODE_PATH" && "$CODE_PATH/.venv/bin/python" -m scripts.research.notebooklm_sync \
      --notebook "$NLM_SYNC_NOTEBOOK" ) || echo "WARN: NotebookLM sync failed"
  else
    echo "nlm not authenticated — skipping NotebookLM sync (run 'nlm login')."
  fi
fi

# 5. Commit + push vault.
echo "--- [5/5] Sync vault ---"
if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] would: git add/commit/push $VAULT_PATH"
else
  git -C "$VAULT_PATH" add -A
  git -C "$VAULT_PATH" commit -m "nightly sync $(date +%Y-%m-%d)" --allow-empty
  git -C "$VAULT_PATH" remote get-url origin >/dev/null 2>&1 \
    && git -C "$VAULT_PATH" push 2>&1 || echo "no remote — skipping push"
fi

[ "$DRY_RUN" = "1" ] || touch "$CODE_PATH/logs/last_run"
echo "=== Nightly run complete: $(date) ==="
