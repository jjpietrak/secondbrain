#!/bin/bash
# Start the LiteLLM proxy for Second Brain on port 4000 (foreground).
# LiteLLM is the ONLY component allowed to see ANTHROPIC_API_KEY (pay-as-you-go).
set -euo pipefail

CODE_PATH="${CODE_PATH:-/home/jpietrak/second_brain}"
cd "$CODE_PATH"

# Load env (LITELLM_MASTER_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, ...)
set -a
# shellcheck disable=SC1091
source "$CODE_PATH/.env"
set +a

# Make custom cost callback (services/litellm/cost_callback.py) importable.
export PYTHONPATH="$CODE_PATH:${PYTHONPATH:-}"

exec "$CODE_PATH/.venv/bin/litellm" --config "$CODE_PATH/config/litellm.yaml" --port "${LITELLM_PORT:-4000}"
