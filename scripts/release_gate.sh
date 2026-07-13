#!/usr/bin/env bash
# release_gate.sh — pre-merge / pre-release scrub gate for the v0.2 team release.
#
# Scans tracked files for content that must NEVER ship in a public/team release:
# personal filesystem paths, the maintainer's identity, confidential product and
# company names, private vault names, live secret tokens, and a tracked .env.
#
# HARD failures (exit 1)  -> genuinely sensitive; the release is blocked until zero.
# WARN findings (exit 0)  -> removed-infrastructure mentions worth a human glance
#                            (some legitimately appear in RELEASE.md while documenting
#                            what was stripped).
#
# Usage:
#   scripts/release_gate.sh              # scan the working tree (tracked files)
#   scripts/release_gate.sh <git-ref>    # scan a branch/commit, e.g. a merge candidate
#
# Exit code: 0 = no HARD findings, 1 = one or more HARD findings (or a tracked .env).

set -u

REF="${1:-}"                                   # empty => working tree
LABEL="${REF:-working tree}"

# git grep helpers: search the given ref when provided, else the working tree.
# The gate script itself is excluded — it lists the forbidden literals verbatim.
EXCL=':(exclude)scripts/release_gate.sh'
grep_lit() { if [ -n "$REF" ]; then git grep -nI -F "$1" "$REF" -- . "$EXCL" 2>/dev/null; else git grep -nI -F "$1" -- . "$EXCL" 2>/dev/null; fi; }
grep_re()  { if [ -n "$REF" ]; then git grep -nIE "$1" "$REF" -- . "$EXCL" 2>/dev/null; else git grep -nIE "$1" -- . "$EXCL" 2>/dev/null; fi; }

# ----------------------------------------------------------------------------
# HARD patterns — must be zero. Edit these when the sensitive set changes.
# ----------------------------------------------------------------------------
# Literal strings (regex-special chars taken verbatim).
HARD_LITERAL=(
  "/home/jpietrak"                # maintainer WSL repo path
  "/mnt/c/"                       # maintainer Windows-mount vault paths
  "C:\\Users"                     # maintainer Windows home
  "kubap"                         # maintainer Windows username
  "jakub.pietrak"                 # maintainer email localpart
  "lumai.ai"                      # employer email domain
  "Iris Tetra"                    # confidential product (optical accelerator)
  "Inference-Disagg"              # private vault name
  "Disagg-Exp"                    # private experiment vault name
)
# Regexes.
HARD_REGEX=(
  "Lumai"                                        # employer name
  "(sk|pplx|xai)-[A-Za-z0-9]{16,}"               # OpenAI/Perplexity/xAI live keys
  "ghp_[A-Za-z0-9]{30,}"                         # GitHub PAT
  "AIza[0-9A-Za-z_-]{30,}"                       # Google/Gemini API key
)

# ----------------------------------------------------------------------------
# WARN patterns — non-blocking; removed infra that should not be referenced
# by live code, but is allowed in RELEASE.md while documenting the scrub.
# ----------------------------------------------------------------------------
WARN_REGEX=(
  "litellm|LiteLLM"
  "\\b(web_crawl|web_decision|web_harvest|web_rank|web_query|web_backfill|agent_learn|report_approve|nightly_run)\\b"
)

hard_fail=0

echo "=== release gate: scanning ${LABEL} ==="
echo

echo "-- HARD checks (must be zero) --"

# A tracked real .env is an immediate hard fail.
if [ -n "$REF" ]; then env_tracked=$(git ls-tree -r --name-only "$REF" 2>/dev/null | grep -xE '\.env|.*/\.env' || true)
else env_tracked=$(git ls-files 2>/dev/null | grep -xE '\.env|.*/\.env' || true); fi
if [ -n "$env_tracked" ]; then
  echo "  [FAIL] a real .env file is tracked:"; echo "$env_tracked" | sed 's/^/         /'
  hard_fail=$((hard_fail+1))
fi

for pat in "${HARD_LITERAL[@]}"; do
  hits=$(grep_lit "$pat")
  if [ -n "$hits" ]; then
    n=$(printf '%s\n' "$hits" | grep -c .)
    echo "  [FAIL] '$pat' — $n hit(s):"
    printf '%s\n' "$hits" | sed 's/^/         /'
    hard_fail=$((hard_fail+1))
  fi
done
for pat in "${HARD_REGEX[@]}"; do
  hits=$(grep_re "$pat")
  if [ -n "$hits" ]; then
    n=$(printf '%s\n' "$hits" | grep -c .)
    echo "  [FAIL] /$pat/ — $n hit(s):"
    printf '%s\n' "$hits" | sed 's/^/         /'
    hard_fail=$((hard_fail+1))
  fi
done
[ "$hard_fail" -eq 0 ] && echo "  ok — no HARD findings"

echo
echo "-- WARN checks (review; non-blocking) --"
warn_hits=0
for pat in "${WARN_REGEX[@]}"; do
  # RELEASE.md is allowed to mention removed infra while documenting the scrub.
  hits=$(grep_re "$pat" | grep -v ':RELEASE.md:' | grep -vE '[:/]RELEASE\.md:' || true)
  if [ -n "$hits" ]; then
    n=$(printf '%s\n' "$hits" | grep -c .)
    echo "  [warn] /$pat/ — $n hit(s):"
    printf '%s\n' "$hits" | sed 's/^/         /'
    warn_hits=$((warn_hits+1))
  fi
done
[ "$warn_hits" -eq 0 ] && echo "  ok — no WARN findings"

echo
if [ "$hard_fail" -gt 0 ]; then
  echo "=== GATE FAILED: ${hard_fail} hard category(ies) with findings — do NOT release ==="
  exit 1
fi
echo "=== GATE PASSED: no hard findings in ${LABEL} ==="
exit 0
