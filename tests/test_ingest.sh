#!/bin/bash
# End-to-end ingest test: drop a source into raw/ and verify /obsidian-ingest creates
# wiki pages. Uses the OAuth wrapper (Agent SDK credit) — no pay-as-you-go spend.
# Run interactively (NOT in a restricted sandbox): bash tests/test_ingest.sh
set -uo pipefail
CODE_PATH="${CODE_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VAULT_PATH="${VAULT_PATH:-$( cd "$CODE_PATH" && python3 -m agents.vault_config path 2>/dev/null || echo "" )}"
if [ -z "$VAULT_PATH" ]; then
  echo "ERROR: VAULT_PATH could not be resolved. Set VAULT_PATH or configure a vault." >&2
  exit 1
fi

ART="$VAULT_PATH/raw/articles/test-ingest-$(date +%s).md"
cat > "$ART" <<'EOF'
---
date: 2026-06-15
tags: [source, article, test]
source_type: article
---
# Test note: retrieval-augmented generation
RAG augments a language model with documents fetched at query time, instead of
baking knowledge into weights. It contrasts with the LLM Wiki pattern, which
pre-compiles knowledge into pages once on ingest.
EOF
echo "created $ART"

before=$(find "$VAULT_PATH/wiki/concepts" "$VAULT_PATH/wiki/entities" -name '*.md' ! -name '_template.md' 2>/dev/null | wc -l)

bash "$CODE_PATH/scripts/claude_agent.sh" --permission-mode acceptEdits --add-dir "$VAULT_PATH" \
  "Run the /obsidian-ingest command on the file: $ART"

after=$(find "$VAULT_PATH/wiki/concepts" "$VAULT_PATH/wiki/entities" -name '*.md' ! -name '_template.md' 2>/dev/null | wc -l)
echo "wiki pages before=$before after=$after"
[ "$after" -gt "$before" ] && echo "PASS: ingest created pages" || echo "FAIL: no new pages"
