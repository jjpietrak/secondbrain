#!/bin/bash
# Second Brain — Tier-2 LOCAL nightly run (2 AM via Windows Task Scheduler).
#
# Multi-vault: processes the ACTIVE vault (VAULT env), or every enabled vault when
# VAULT=all. Each vault is fully encapsulated — its own config/vaults/<v>/{vault,budget}.yaml,
# its own $VAULT_ROOT, its own catch-up guard. Vaults are processed sequentially here;
# they are independent, so they could also be run in parallel from separate invocations.
#
# Cost model: every `claude -p` call goes through scripts/claude_agent.sh, which uses
# CLAUDE_CODE_OAUTH_TOKEN (Agent SDK credit, $0 marginal) and unsets ANTHROPIC_API_KEY.
#
# Set DRY_RUN=1 to skip claude calls, commits, and pushes (prints what it would do).
set -uo pipefail

CODE_PATH="${CODE_PATH:-/home/jpietrak/second_brain}"
export CODE_PATH
DRY_RUN="${DRY_RUN:-0}"
PY="$CODE_PATH/.venv/bin/python"
AGENT="$CODE_PATH/scripts/claude_agent.sh"

mkdir -p "$CODE_PATH/logs"
LOG="$CODE_PATH/logs/nightly-$(date +%Y%m%d).log"
exec > >(tee -a "$LOG") 2>&1
echo "=== Nightly run started: $(date) (DRY_RUN=$DRY_RUN, VAULT=${VAULT:-default}) ==="

# Resolve the list of vaults to process. VAULT=all -> every enabled vault.
if [ "${VAULT:-}" = "all" ]; then
  mapfile -t VAULTS < <(cd "$CODE_PATH" && "$PY" -m agents.vault_config list)
else
  VAULTS=("$(cd "$CODE_PATH" && VAULT="${VAULT:-}" "$PY" -m agents.vault_config name)")
fi
echo "Vaults to process: ${VAULTS[*]}"

run_vault() {  # $1 = vault name
  local V="$1"
  export VAULT="$V"
  local VAULT_ROOT
  VAULT_ROOT="$(cd "$CODE_PATH" && "$PY" -m agents.vault_config path)"
  export VAULT_ROOT VAULT_PATH="$VAULT_ROOT"
  local BUDGET_FILE="$CODE_PATH/config/vaults/$V/budget.yaml"
  local CLAUDE_FLAGS=(--permission-mode acceptEdits --add-dir "$VAULT_ROOT")

  echo ""
  echo "########## VAULT: $V ($VAULT_ROOT) ##########"

  # Per-vault catch-up guard: skip if this vault ran in the last 20h.
  local STAMP="$CODE_PATH/logs/last_run_$V"
  local LAST_RUN HOURS_SINCE
  LAST_RUN=$(stat -c %Y "$STAMP" 2>/dev/null || echo 0)
  HOURS_SINCE=$(( ( $(date +%s) - LAST_RUN ) / 3600 ))
  if [ "$HOURS_SINCE" -lt 20 ]; then
    echo "[$V] ran ${HOURS_SINCE}h ago (<20h). Skipping."
    return 0
  fi

  run_claude() {  # $1 = prompt
    if [ "$DRY_RUN" = "1" ]; then echo "[dry-run] claude_agent.sh ${CLAUDE_FLAGS[*]} \"$1\""; return 0; fi
    bash "$AGENT" "${CLAUDE_FLAGS[@]}" "$1" || echo "WARN: claude step failed"
  }

  # 0. Budget gate (per-vault) — stop before paid work if today's cap is already hit.
  echo "--- [0/5] Budget check ($V) ---"
  local SKIP_PAID=0
  "$PY" "$CODE_PATH/agents/cost_tracker.py" check
  if [ $? -ne 0 ] && [ "$(grep -E '^on_cap:' "$BUDGET_FILE" 2>/dev/null | awk '{print $2}')" = "stop" ]; then
    echo "[$V] budget cap reached — aborting paid steps."; SKIP_PAID=1
  fi

  # 1. Pull what the cloud Routines committed overnight.
  echo "--- [1/5] Pull vault ($V) ---"
  [ "$DRY_RUN" = "1" ] || git -C "$VAULT_ROOT" pull --rebase --autostash 2>&1 || echo "WARN: pull failed"

  # 2. Ingest raw sources not yet ingested (content-hash index, idempotent).
  echo "--- [2/5] Ingest new raw sources ($V) ---"
  if [ "$SKIP_PAID" = "0" ]; then
    local PENDING
    PENDING=$(cd "$CODE_PATH" && "$PY" -m agents.ingest_index pending)
    if [ -n "$PENDING" ]; then
      echo "$PENDING" | while IFS= read -r src; do
        [ -z "$src" ] && continue
        echo "Ingesting: $src"
        run_claude "Run the /obsidian-ingest command on the file: $VAULT_ROOT/$src"
      done
    else
      echo "No new raw sources (ingest index up to date)."
    fi
  fi

  # 2b. Research topics due today — ALWAYS the claude engine ($0, subscription / Agent SDK
  # credit; never Perplexity in unattended runs). Capped by NIGHTLY_RESEARCH_MAX (default 3).
  echo "--- [2b] Research due topics ($V, claude engine) ---"
  local DUE_MAX="${NIGHTLY_RESEARCH_MAX:-3}"
  local DUE=() topic n=0
  mapfile -t DUE < <(cd "$CODE_PATH" && "$PY" -m agents.vault_config topics-due)
  if [ "${#DUE[@]}" -eq 0 ]; then
    echo "No topics due today."
  else
    for topic in "${DUE[@]}"; do
      [ -z "$topic" ] && continue
      n=$((n + 1))
      if [ "$n" -gt "$DUE_MAX" ]; then
        echo "Deferring $(( ${#DUE[@]} - DUE_MAX )) more due topic(s) (NIGHTLY_RESEARCH_MAX=$DUE_MAX)."
        break
      fi
      echo "Researching ($n/$DUE_MAX): $topic"
      run_claude "Run the /obsidian-research-deep command with arguments: \"$topic\" --claude"
    done
  fi

  # 3. Lint pass.
  echo "--- [3/5] Lint ($V) ---"
  [ "$SKIP_PAID" = "0" ] && run_claude "Run the /obsidian-lint command on the vault."

  # 4. Structural health audit + cost report (pure Python, free; both honour VAULT).
  echo "--- [4/5] Vault health ($V) ---"
  "$PY" "$CODE_PATH/agents/vault_health.py"
  "$PY" "$CODE_PATH/agents/cost_tracker.py" report

  # 4b. NotebookLM sync (opt-in, $0, non-fatal). Notebook resolved per-vault from
  # vault.yaml (notebooklm_notebook), falling back to the global NLM_SYNC_NOTEBOOK env.
  local NOTEBOOK
  NOTEBOOK="$(cd "$CODE_PATH" && "$PY" -m agents.vault_config notebook)"
  if [ -n "$NOTEBOOK" ]; then
    echo "--- [4b] NotebookLM sync ($V → $NOTEBOOK) ---"
    if [ "$DRY_RUN" = "1" ]; then
      echo "[dry-run] would sync NotebookLM notebook $NOTEBOOK"
    elif nlm login --check >/dev/null 2>&1; then
      ( cd "$CODE_PATH" && "$PY" -m scripts.research.notebooklm_sync \
        --notebook "$NOTEBOOK" ) || echo "WARN: NotebookLM sync failed"
    else
      echo "nlm not authenticated — skipping NotebookLM sync (run 'nlm login')."
    fi
  fi

  # 5. Commit + push vault.
  echo "--- [5/5] Sync vault ($V) ---"
  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] would: git add/commit/push $VAULT_ROOT"
  else
    git -C "$VAULT_ROOT" add -A
    git -C "$VAULT_ROOT" commit -m "nightly sync $(date +%Y-%m-%d)" --allow-empty
    git -C "$VAULT_ROOT" remote get-url origin >/dev/null 2>&1 \
      && git -C "$VAULT_ROOT" push 2>&1 || echo "no remote — skipping push"
  fi

  [ "$DRY_RUN" = "1" ] || touch "$STAMP"
  echo "########## VAULT $V complete ##########"
}

for v in "${VAULTS[@]}"; do
  run_vault "$v"
done

echo "=== Nightly run complete: $(date) ==="
