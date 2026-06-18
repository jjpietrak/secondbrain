#!/bin/bash
# Dry-run the Tier-2 nightly orchestrator: no claude calls, no commits, no paid spend.
# Usage: bash tests/test_nightly.sh
set -uo pipefail
CODE_PATH="${CODE_PATH:-/home/jpietrak/second_brain}"
DRY_RUN=1 bash "$CODE_PATH/agents/nightly_run.sh"
echo "nightly dry-run exit: $?"
