#!/bin/bash
# Obsidian Local REST API helpers (coddingtonbear plugin, runs inside Windows Obsidian).
#
# Requires: Obsidian running on Windows with the "Local REST API" plugin enabled, and
# OBSIDIAN_API_KEY set in .env. Default transport is self-signed HTTPS on 27124, so we
# pass -k. Switch OBSIDIAN_API_URL to http://127.0.0.1:27123 if you enable the plugin's
# non-encrypted mode.
#
# Usage:  source scripts/obsidian_api.sh   then call the functions below.
#         obsidian_ping ; obsidian_list ; obsidian_get_file "wiki/index.md"
#         echo "# Hi" | obsidian_put_file "wiki/scratch.md"
#         echo "- note" | obsidian_patch_heading "wiki/index.md" "Concepts"
#         obsidian_search "neural networks"
#
# NOTE: loads ONLY the OBSIDIAN_* vars (not the whole .env) so ANTHROPIC_API_KEY never
# leaks into a shell that may later run `claude -p` (see CLAUDE.md auth-isolation rule).

CODE_PATH="${CODE_PATH:-/home/jpietrak/second_brain}"
OBSIDIAN_API_KEY="$(grep -E '^OBSIDIAN_API_KEY=' "$CODE_PATH/.env" | head -1 | cut -d= -f2-)"
OBSIDIAN_API_URL="$(grep -E '^OBSIDIAN_API_URL=' "$CODE_PATH/.env" | head -1 | cut -d= -f2-)"
: "${OBSIDIAN_API_URL:=https://127.0.0.1:27124}"

_obs_curl() { curl -sk -H "Authorization: Bearer $OBSIDIAN_API_KEY" "$@"; }

# Liveness check — returns plugin status JSON if Obsidian + plugin are up.
obsidian_ping() { _obs_curl --max-time 5 "$OBSIDIAN_API_URL/"; }

# List vault files (top level).
obsidian_list() { _obs_curl "$OBSIDIAN_API_URL/vault/"; }

# Read a vault file. $1 = vault-relative path (e.g. "wiki/index.md").
obsidian_get_file() { _obs_curl "$OBSIDIAN_API_URL/vault/$1"; }

# Create/overwrite a vault file. $1 = path, content on stdin.
obsidian_put_file() {
  _obs_curl -X PUT -H "Content-Type: text/markdown" --data-binary @- "$OBSIDIAN_API_URL/vault/$1"
}

# Append content under a heading (surgical edit). $1 = path, $2 = heading text, content on stdin.
obsidian_patch_heading() {
  _obs_curl -X PATCH \
    -H "Content-Type: text/markdown" \
    -H "Operation: append" -H "Target-Type: heading" -H "Target: $2" \
    --data-binary @- "$OBSIDIAN_API_URL/vault/$1"
}

# Simple full-text search. $1 = query string.
obsidian_search() {
  _obs_curl -X POST -H "Content-Type: application/json" \
    -d "{\"query\": \"$1\"}" "$OBSIDIAN_API_URL/search/simple/?query=$1"
}
