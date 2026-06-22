#!/usr/bin/env python3
"""deep_synth_helper.py -- deterministic helper for the deep-synthesis skill.

NO LLM calls.  No import of agents.objectives.  All vault roots and input data
are passed as arguments so the helper is fully unit-testable and parallel-safe.

Public surface
--------------
resolve_output_path(vault_root, scope, today) -> Path
    Resolve the research/ output path for a deep-synthesis run.

    scope is a dict with ONE of:
      {"question": "Q-0001"}  -> research/Q-0001.md
      {"topic":    "T-0001"}  -> research/<slug>.md  (topic slug from title)
      {"topic_slug": "inference-disagg"} -> research/inference-disagg.md
      {"deep":     "some topic text"} -> research/deep/YYYY-MM-DD-<slug>.md

format_research_report_frontmatter(scope, today) -> str
    Return the YAML frontmatter block (---...---) for a research report file.
    Does NOT write anything.

format_direction_frontmatter(direction_id, today, fields) -> str
    Return the YAML frontmatter block for an objective/direction/ node.
    fields: dict with keys from parse_directions() e.g. serves_question, topics, etc.

format_proposal_frontmatter(proposal_id, today, proposal_dict) -> str
    Return the YAML frontmatter block for an objective/research_question_proposal/ node.
    proposal_dict: one item from parse_proposals(), keys: proposal, rationale, from_gap.

format_already_answerable_surface(already_answerable_list) -> str
    Format the "Already Answerable" list for printing to stdout (not a vault write).
    Returns an empty string if the list is empty or ["none"].

slugify(text) -> str
    Convert free text into a filesystem-safe lowercase slug (ASCII only, hyphens).

Design constraints
------------------
- Pure helper: NO LLM, NO web, NO agents.objectives import.
- vault-relative paths only in frontmatter (never absolute paths).
- All string outputs use ASCII only (no em-dashes, curly quotes, Unicode math).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

__all__ = [
    "resolve_output_path",
    "format_research_report_frontmatter",
    "format_direction_frontmatter",
    "format_proposal_frontmatter",
    "format_already_answerable_surface",
    "slugify",
]

# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert free text into a filesystem-safe lowercase slug.

    - Lowercase
    - Replace non-alphanumeric runs with hyphens
    - Strip leading/trailing hyphens
    - Truncate to 60 chars (avoids overly long filenames)
    - ASCII only
    """
    s = text.lower()
    # Drop non-ASCII by encoding to ASCII with 'ignore'
    s = s.encode("ascii", "ignore").decode("ascii")
    # Replace runs of non-alphanumeric chars with a hyphen
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:60] or "untitled"


# ---------------------------------------------------------------------------
# resolve_output_path
# ---------------------------------------------------------------------------

def resolve_output_path(
    vault_root: Path,
    scope: dict,
    today: Optional[str] = None,
) -> Path:
    """Resolve the research/ output path for a deep-synthesis run.

    scope is a dict with ONE of the following keys:

      {"question": "Q-0001"}
          -> <vault_root>/research/Q-0001.md

      {"topic": "T-0001"}
          -> <vault_root>/research/T-0001.md
          (the id itself becomes the filename; the skill fills the slug from the
          node's title if desired, but the path is deterministic from the id alone)

      {"topic_slug": "inference-disagg"}
          -> <vault_root>/research/inference-disagg.md

      {"deep": "some topic text"}
          -> <vault_root>/research/deep/YYYY-MM-DD-<slug>.md

    vault_root : Path
        Absolute path to the vault root (the folder that contains research/).
    scope : dict
        One of the four shapes above.
    today : str | None
        ISO date string (YYYY-MM-DD). Defaults to date.today().isoformat().

    Returns the absolute Path to the output file (the parent dir is NOT created
    by this function - the skill creates it via mkdir -p before writing).
    """
    if today is None:
        today = date.today().isoformat()

    if "question" in scope:
        qid = scope["question"].strip()
        return vault_root / "research" / f"{qid}.md"

    if "topic" in scope:
        tid = scope["topic"].strip()
        return vault_root / "research" / f"{tid}.md"

    if "topic_slug" in scope:
        slug = slugify(scope["topic_slug"])
        return vault_root / "research" / f"{slug}.md"

    if "deep" in scope:
        slug = slugify(scope["deep"])
        return vault_root / "research" / "deep" / f"{today}-{slug}.md"

    raise ValueError(
        f"scope must have one of 'question', 'topic', 'topic_slug', or 'deep'; got: {scope!r}"
    )


# ---------------------------------------------------------------------------
# format_research_report_frontmatter
# ---------------------------------------------------------------------------

def format_research_report_frontmatter(
    scope: dict,
    today: Optional[str] = None,
    topic_id: str = "",
    question_id: str = "",
) -> str:
    """Return the YAML frontmatter block for a research report file.

    Fills the schema from plans/phase-2-objectives.md Section 6.2:
      type: research_report
      written_by: research
      topic: T-NNNN   (empty string if not provided)
      serves_question: Q-NNNN (empty string if not provided)
      created: YYYY-MM-DD
      status: draft

    scope is inspected to fill topic/serves_question when topic_id/question_id
    are not explicitly provided.
    """
    if today is None:
        today = date.today().isoformat()

    # Resolve topic and question from explicit args or scope.
    t = topic_id or scope.get("topic", "")
    q = question_id or scope.get("question", "")

    lines = [
        "---",
        "type: research_report",
        "written_by: research",
        f"topic: {t}",
        f"serves_question: {q}",
        f"created: {today}",
        "status: draft",
        "---",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_direction_frontmatter
# ---------------------------------------------------------------------------

def format_direction_frontmatter(
    direction_id: str,
    today: str,
    fields: dict,
    question_id: str = "",
) -> str:
    """Return YAML frontmatter for an objective/direction/ node.

    direction_id : str
        Formatted id string, e.g. "DIR-0001".
    today : str
        ISO date string.
    fields : dict
        As returned by parse_directions() - the 'fields' sub-dict.
        Expected keys: serves_question, topics, targets_gap, reasoning_pattern,
        expected_evidence, seed_queries, solves_when, priority.
    question_id : str
        Optional override for serves_question (if the skill knows the concrete Q-id
        at write time and the field is missing or generic).

    Returns the frontmatter block string only (no body content).
    """
    serves = question_id or fields.get("serves_question", "")
    topics = fields.get("topics", "")
    targets_gap = fields.get("targets_gap", "")
    priority_raw = fields.get("priority", "medium")
    # priority field may be "high  -  justification text" -- take first token
    priority = priority_raw.split()[0].lower() if priority_raw else "medium"
    if priority not in ("high", "medium", "low"):
        priority = "medium"

    lines = [
        "---",
        f"type: direction",
        f"id: {direction_id}",
        f"created: {today}",
        f"updated: {today}",
        "written_by: research",
        f"serves_question: {serves}",
        f"topics: {topics}",
        f"targets_gap: {targets_gap}",
        f"priority: {priority}",
        "status: open",
        "---",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_proposal_frontmatter
# ---------------------------------------------------------------------------

def format_proposal_frontmatter(
    proposal_id: str,
    today: str,
    proposal_dict: dict,
) -> str:
    """Return YAML frontmatter for an objective/research_question_proposal/ node.

    proposal_id : str
        Formatted id string, e.g. "QP-0001".
    today : str
        ISO date string.
    proposal_dict : dict
        As returned by parse_proposals() - keys: proposal, rationale, from_gap.
    """
    from_gap = proposal_dict.get("from_gap", "").strip()

    lines = [
        "---",
        "type: research_question_proposal",
        f"id: {proposal_id}",
        f"created: {today}",
        f"updated: {today}",
        "written_by: research",
        f"from_gap: {from_gap}",
        "status: pending",
        "---",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_already_answerable_surface
# ---------------------------------------------------------------------------

def format_already_answerable_surface(already_answerable: list[str]) -> str:
    """Format the Already Answerable list for printing to stdout.

    Returns an empty string if the list is empty or contains only "none" entries.
    The output is plain text suitable for the research agent to print after synthesis.

    already_answerable : list[str]
        Items extracted from the '## Already Answerable' section of synthesis output.
        Each item is a question id or a free-text description.  Items that are
        literally "none" (case-insensitive) are filtered out.
    """
    real_items = [
        item.strip(" -")
        for item in already_answerable
        if item.strip(" -").lower() not in ("", "none")
    ]
    if not real_items:
        return ""

    lines = [
        "ALREADY ANSWERABLE - the vault can likely answer these (consider question-solve):",
    ]
    for item in real_items:
        lines.append(f"  - {item}")
    return "\n".join(lines)
