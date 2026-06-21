#!/usr/bin/env python3
"""wiki_gaps_fill.py -- fill WIKI_GAP_PROMPT from a gather payload.

Companion to wiki_gaps_gather.py.  Takes the JSON payload produced by
gather_wiki_state() and returns the filled prompt string ready to be sent
to the wiki agent's LLM.  No LLM call is made here; this is deterministic.

Public surface
--------------
fill_gap_prompt(payload: dict) -> str
    Fill WIKI_GAP_PROMPT with the payload values.
    Raises KeyError if a required key is missing from the payload.

CLI usage (pipe from wiki_gaps_gather.py or pass a payload file)
---------
    python scripts/wiki_gaps_gather.py <vault_root> --output-json \\
        | python scripts/wiki_gaps_fill.py

    python scripts/wiki_gaps_fill.py payload.json    # from a file
    python scripts/wiki_gaps_fill.py --stdin         # read JSON from stdin
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure repo root on sys.path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.prompts.pipeline_prompts import WIKI_GAP_PROMPT  # noqa: E402


def fill_gap_prompt(payload: dict) -> str:
    """Fill WIKI_GAP_PROMPT with the values from a gather payload.

    Parameters
    ----------
    payload : dict
        As returned by gather_wiki_state().  Required keys:
          purpose, today, active_topics, wiki_state.

    Returns
    -------
    str
        The filled prompt, ready to send to the wiki agent's LLM.

    Raises
    ------
    KeyError
        If a required key is missing from the payload.
    """
    return WIKI_GAP_PROMPT.format(
        purpose=payload["purpose"],
        today=payload["today"],
        active_topics=payload["active_topics"],
        wiki_state=payload["wiki_state"],
    )


# ---------------------------------------------------------------------------
# Validation helper (used by the skill and the test)
# ---------------------------------------------------------------------------

def validate_gaps_output(gaps_text: str) -> tuple[bool, str]:
    """Validate the LLM output from WIKI_GAP_PROMPT.

    Checks:
    1. Contains "## Open-Question Harvest" section (top-priority section present).
    2. Ends with "READY" on its own line.

    Returns (ok: bool, message: str).
    """
    lines = gaps_text.strip().splitlines()
    has_harvest = any(
        line.strip() == "## Open-Question Harvest (TOP PRIORITY)"
        or line.strip().startswith("## Open-Question Harvest")
        for line in lines
    )
    ends_ready = lines[-1].strip() == "READY" if lines else False
    if not has_harvest:
        return False, "Missing '## Open-Question Harvest' section in gaps output"
    if not ends_ready:
        return False, "Gaps output does not end with 'READY'"
    return True, "ok"


# ---------------------------------------------------------------------------
# gaps.md frontmatter builder
# ---------------------------------------------------------------------------

def build_gaps_frontmatter(vault_name: str, today: str) -> str:
    """Return the YAML frontmatter block for wiki/gaps.md."""
    return (
        "---\n"
        "type: gaps_report\n"
        "generated_by: wiki\n"
        f"updated: {today}\n"
        f"vault: {vault_name}\n"
        "ai-first: true\n"
        "---\n\n"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Fill WIKI_GAP_PROMPT from a gather payload JSON."
    )
    parser.add_argument(
        "payload_file",
        nargs="?",
        default=None,
        help="Path to a JSON payload file from wiki_gaps_gather.py (default: stdin).",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON payload from stdin (equivalent to no positional arg).",
    )
    args = parser.parse_args()

    if args.payload_file:
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    else:
        payload = json.loads(sys.stdin.read())

    filled = fill_gap_prompt(payload)
    print(filled)


if __name__ == "__main__":
    main()
