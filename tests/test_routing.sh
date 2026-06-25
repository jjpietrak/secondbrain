#!/bin/bash
# NOTE: The LiteLLM proxy (services/litellm/) was removed in v0.2.
# This script previously smoke-tested routing to Ollama bulk, Anthropic synthesis,
# and Gemini Flash validation through the proxy on localhost:4000.
#
# The shipped v0.2 flow routes directly:
#   - Agent SDK credit pool: claude -p via scripts/claude_agent.sh (no key needed)
#   - Gemini Flash validation: direct API via GEMINI_API_KEY (wiki_cite_check.py)
#   - Anthropic API tier-1 prefix: direct API via ANTHROPIC_API_KEY (contextual-prefix.py, opt-in)
#   - Ollama bulk/embeddings: local HTTP on port 11434 (no auth required)
#
# Routing tests for the live providers are integration-level and require real API keys.
# The hermetic unit tests (test_wiki_cite_check.py, test_contextual_prefix.py, etc.)
# cover the routing logic without network access.
#
# This file is kept as documentation. It is not executed by the pytest suite.
echo "test_routing.sh: LiteLLM proxy removed; routing is now direct. See comment above."
exit 0
