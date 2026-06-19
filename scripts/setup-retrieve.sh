#!/usr/bin/env bash
# setup-retrieve.sh — opt-in bootstrap for wiki-retrieve (Second Brain v0.2).
#
# Provisions the contextual-prefix + BM25 + rerank pipeline. Idempotent;
# safe to re-run after schema changes or full vault re-ingest.
#
# What this does (in order):
#   1. Sanity-check that scripts/contextual-prefix.py, bm25-index.py,
#      rerank.py, retrieve.py are present and executable.
#   2. Create <vault>/.vault-meta/chunks/ and <vault>/.vault-meta/bm25/ dirs.
#   3. Check for ollama + nomic-embed-text (informational; not required for the
#      contextual-prefix tier, but required for the rerank cosine stage).
#   4. Data-egress posture (Second Brain default = LOCAL/synthetic, $0). The
#      contextual-prefix run stays on tier-3 (synthetic, on-machine) UNLESS the
#      caller passes --allow-egress AND grants explicit y/N consent. Pass
#      --no-llm to skip the prompt entirely and stay on tier-3.
#   5. Run contextual-prefix.py --all to chunk + contextualize every wiki page.
#      Tier picker (synthetic by default; non-synthetic only with --allow-egress
#      + consent):
#        tier 1: Anthropic API   (--allow-egress + ANTHROPIC_API_KEY set)
#        tier 2: claude CLI -p   (--allow-egress + `claude` on PATH)
#        tier 3: synthetic       (default; --no-llm; or no consent)
#      Stage 1 exit code is captured; non-zero aborts with a recovery hint (rc=5).
#   6. Run bm25-index.py build to build the inverted index.
#
# After completion the wiki-retrieve skill is "feature-detected" by other skills
# (wiki-query checks for scripts/retrieve.py + <vault>/.vault-meta/chunks/).
#
# Second Brain adaptation vs the CO original: VAULT_ROOT (where .vault-meta lives)
# resolves to the ACTIVE VAULT via agents.vault_config; the scripts themselves are
# read from the code repo's scripts/ dir (this script's own dir). Egress defaults
# to OFF (LOCAL/synthetic) per the $0/free-route discipline.
#
# Usage:
#   bash scripts/setup-retrieve.sh
#   bash scripts/setup-retrieve.sh --no-llm       # force tier-3 synthetic-only
#   bash scripts/setup-retrieve.sh --allow-egress # opt into non-synthetic tier (consented)
#   bash scripts/setup-retrieve.sh --rebuild      # rebuild all chunks
#   bash scripts/setup-retrieve.sh --check        # diagnostics only; no provisioning

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve the active vault root (.vault-meta lives there, NOT in the code repo).
# Mirrors scripts/wiki-lock.sh resolution order.
#   1. $VAULT_ROOT        - exported by `agents.vault_config env`
#   2. `agents.vault_config path` - resolve the active vault on demand
#   3. script-parent fallback (last resort; matches the CO default)
if [ -n "${VAULT_ROOT:-}" ]; then
  VAULT="$VAULT_ROOT"
else
  _code_path="${CODE_PATH:-$(cd "$SCRIPT_DIR/.." && pwd)}"
  _py="${PY:-$_code_path/.venv/bin/python}"
  [ -x "$_py" ] || _py="python3"
  VAULT="$(cd "$_code_path" 2>/dev/null && "$_py" -m agents.vault_config path 2>/dev/null || true)"
  [ -n "$VAULT" ] || VAULT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
META="$VAULT/.vault-meta"

NO_LLM=false
REBUILD=false
CHECK_ONLY=false
ALLOW_EGRESS_FLAG=false

while [ $# -gt 0 ]; do
  case "$1" in
    --no-llm)        NO_LLM=true ;;
    --rebuild)       REBUILD=true ;;
    --check)         CHECK_ONLY=true ;;
    --allow-egress)  ALLOW_EGRESS_FLAG=true ;;
    -h|--help)
      sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ERR: unknown flag: $1" >&2
      exit 2
      ;;
  esac
  shift
done

say() { printf '%s\n' "$@"; }
warn() { printf 'WARN: %s\n' "$@" >&2; }

say "=== wiki-retrieve setup (Second Brain v0.2) ==="
say "Vault: $VAULT"
say "Scripts: $SCRIPT_DIR"
say ""

# -- 1. Sanity check ----------------------------------------------------------
REQUIRED=(
  "$SCRIPT_DIR/contextual-prefix.py"
  "$SCRIPT_DIR/bm25-index.py"
  "$SCRIPT_DIR/rerank.py"
  "$SCRIPT_DIR/retrieve.py"
)
missing=0
for f in "${REQUIRED[@]}"; do
  if [ ! -f "$f" ]; then
    warn "missing: $f"
    missing=$((missing+1))
  fi
done
if [ $missing -gt 0 ]; then
  say "FAIL: $missing required script(s) missing."
  exit 3
fi
say "OK: All 4 retrieval scripts present"

# Pick a python: prefer the repo venv, fall back to python3.
PY="${PY:-$(cd "$SCRIPT_DIR/.." && pwd)/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

# -- 2. Provision .vault-meta state directories -------------------------------
mkdir -p "$META/chunks" "$META/bm25"
say "OK: State directories: $META/chunks/, $META/bm25/"

# -- 3. Check ollama (informational) ------------------------------------------
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
# If OLLAMA_URL was overridden to point off-machine, refuse to probe unless the
# caller passes --allow-remote-ollama (mirrors rerank.py's localhost gate).
case "$OLLAMA_URL" in
  "http://127.0.0.1:"*|"http://localhost:"*|"http://[::1]:"*) ;;
  *)
    if ! printf '%s ' "$@" | grep -q -- '--allow-remote-ollama'; then
      warn "OLLAMA_URL points off-localhost: $OLLAMA_URL"
      warn "Refusing to probe remote ollama without explicit consent."
      warn "Pass --allow-remote-ollama to opt in, or unset OLLAMA_URL."
      OLLAMA_URL=""
    fi
    ;;
esac
OLLAMA_ALIVE=false
MODEL_PRESENT=false
if [ -n "$OLLAMA_URL" ] && command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    OLLAMA_ALIVE=true
    if curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" 2>/dev/null \
       | grep -q '"nomic-embed-text'; then
      MODEL_PRESENT=true
    fi
  fi
fi
if $OLLAMA_ALIVE && $MODEL_PRESENT; then
  say "OK: ollama reachable at $OLLAMA_URL with nomic-embed-text (rerank will use cosine)"
elif $OLLAMA_ALIVE; then
  warn "ollama reachable but nomic-embed-text is not pulled. Run: ollama pull nomic-embed-text"
  warn "rerank stage will no-op until the model is available."
else
  warn "ollama not reachable at $OLLAMA_URL"
  warn "rerank stage will no-op until ollama is running. BM25 retrieval still works."
  warn "Install: https://ollama.com/download; then: ollama pull nomic-embed-text"
fi

# -- 4. Prefix-tier picker (informational) ------------------------------------
# Tier reflects what WOULD run if --allow-egress were passed. Without the flag +
# consent, the actual run forces tier-3 synthetic (the $0/local default).
if $NO_LLM; then
  PREFIX_TIER="synthetic (forced via --no-llm)"
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  PREFIX_TIER="anthropic-api (ANTHROPIC_API_KEY detected; ~\$12/1000 docs)"
elif command -v claude >/dev/null 2>&1; then
  PREFIX_TIER="claude-cli subprocess (no API key needed; uses CC subscription)"
else
  PREFIX_TIER="synthetic (no API key, no claude CLI; reduced retrieval quality)"
fi
say "OK: Contextual-prefix tier (if --allow-egress): $PREFIX_TIER"

if $CHECK_ONLY; then
  say ""
  say "-- --check passed; not provisioning."
  exit 0
fi

# -- 4b. Egress consent -------------------------------------------------------
# Default is LOCAL/synthetic ($0). Egress (tiers 1/2) requires BOTH --allow-egress
# AND explicit y/N consent before letting contextual-prefix.py send page bodies
# off-machine. Mirrors the --allow-remote-ollama precedent.
ALLOW_EGRESS=false
if ! $NO_LLM && $ALLOW_EGRESS_FLAG; then
  case "$PREFIX_TIER" in
    anthropic-api*|claude-cli*)
      say ""
      say "WARNING: Stage 1 will send wiki page BODIES off-machine via the '$PREFIX_TIER' tier."
      say "    Estimated cost: ~\$0 (claude-cli, free) to ~\$12 per 1,000 pages (Anthropic API)."
      say "    Per-page bodies are POSTed to the provider; review their privacy policy first."
      say "    Default is NO. Tier-3 (synthetic, on-machine) is the safe alternative."
      printf "    Continue with egress? [y/N]: "
      read -r reply || reply=""
      case "$reply" in
        [yY]|[yY][eE][sS])
          say "-> Proceeding with egress."
          ALLOW_EGRESS=true
          ;;
        *)
          say "-> Aborted. Re-run without --allow-egress for the synthetic-only path."
          exit 0
          ;;
      esac
      ;;
  esac
fi

# -- 5. Chunk + contextualize every wiki page ---------------------------------
say ""
say "=== Stage 1/2: chunking + contextual-prefix generation ==="
ARGS=("--all")
$NO_LLM && ARGS+=("--no-llm")
$ALLOW_EGRESS && ARGS+=("--allow-egress")
$REBUILD && ARGS+=("--rebuild")
# Disable set -e for the call so we can inspect the exit code and offer a
# concrete recovery hint instead of aborting with a bare trace.
set +e
VAULT_ROOT="$VAULT" "$PY" "$SCRIPT_DIR/contextual-prefix.py" "${ARGS[@]}"
STAGE1_RC=$?
set -e
if [ "$STAGE1_RC" -ne 0 ]; then
  warn "Stage 1 failed (rc=$STAGE1_RC). Partial chunks may exist at:"
  warn "  $META/chunks/"
  warn "Recovery options:"
  warn "  1. Re-run setup-retrieve.sh — body_hash skips already-processed chunks."
  warn "  2. Wipe and start over:  rm -rf $META/chunks/ && bash scripts/setup-retrieve.sh"
  warn "  3. Re-process one page:  python3 scripts/contextual-prefix.py wiki/<page>.md --rebuild"
  exit 5
fi

# -- 6. Build BM25 index ------------------------------------------------------
say ""
say "=== Stage 2/2: BM25 index build ==="
VAULT_ROOT="$VAULT" "$PY" "$SCRIPT_DIR/bm25-index.py" build

# -- 7. Smoke-test retrieve.py ------------------------------------------------
say ""
say "=== Smoke test ==="
SMOKE_OUT="$(VAULT_ROOT="$VAULT" "$PY" "$SCRIPT_DIR/retrieve.py" "wiki" --top 1 2>/dev/null || echo '{}')"
if echo "$SMOKE_OUT" | grep -q '"candidates":'; then
  say "OK: retrieve.py returns valid JSON"
else
  warn "retrieve.py smoke test produced unexpected output. Run manually for details."
fi

say ""
say "=== wiki-retrieve is provisioned. ==="
say ""
say "Usage from the command line:"
say "  VAULT_ROOT=$VAULT python3 scripts/retrieve.py \"your question here\" --top 5"
say ""
say "Other skills (wiki-query) will now automatically use the hybrid pipeline"
say "when answering questions. See skills/wiki-retrieve/SKILL.md."
