#!/usr/bin/env python3
"""Helpers for question-promote and question-solve state transitions.

Deterministic frontmatter transforms for research_question_proposal -> research_question
promotion and solved-state setting. Hermetically testable - no LLM, no imports from agents/,
just frontmatter parsing and YAML scaffolding.

Public functions:

  proposal_to_question_frontmatter(proposal_path, next_id, topic=None)
    -> dict: frontmatter for the new research_question node
    Read QP-NNNN proposal file, extract question text + id, return frontmatter
    for the new Q-NNNN node.

  mark_proposal_approved(proposal_path)
    -> str: new content with status: approved set
    Take proposal text, set status: approved, return updated content.

  mark_question_solved(question_path, answer_ref, today=None)
    -> str: new content with solved: yes + answer_ref set
    Take question text, set solved: yes and answer_ref field, return updated content.

Frontmatter field references (from phase-2-objectives.md):
  research_question_proposal:
    type, id, created, updated, generated_by, from_gap, status: pending|approved|rejected
  research_question:
    type, id, created, updated, solved: yes|no, topic, priority, answer_ref
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path


FM_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)


def _parse_fm(text: str) -> dict[str, str]:
    """Parse YAML-ish frontmatter into a flat str->str dict.

    Handles simple scalar values and quoted strings. Does not parse nested blocks.
    """
    m = FM_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        raw = v.strip()
        if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
            raw = raw[1:-1]
        fm[k.strip().lower()] = raw
    return fm


def _body(text: str) -> str:
    """Return the body (everything after the closing ---)."""
    m = FM_RE.match(text)
    if not m:
        return text
    return text[m.end() :].lstrip("\n")


def proposal_to_question_frontmatter(
    proposal_text: str, next_id: str, topic: str = "", today: str | None = None
) -> dict[str, str]:
    """Parse a proposal node and return frontmatter dict for the new research_question.

    Args:
        proposal_text: Full proposal file content (with frontmatter + body).
        next_id: The new Q-NNNN id allocated by agents.objectives.next_id().
        topic: The topic T-NNNN this question belongs to (from user or proposal).
               If empty, defaults to "".
        today: YYYY-MM-DD date string. Defaults to today.

    Returns:
        dict with keys: type, id, created, updated, solved, topic, priority, answer_ref
    """
    if today is None:
        today = datetime.date.today().isoformat()

    fm = _parse_fm(proposal_text)

    # Extract priority from proposal if present; default to "medium".
    priority = fm.get("priority", "medium").strip()
    if priority not in ("high", "medium", "low"):
        priority = "medium"

    return {
        "type": "research_question",
        "id": next_id.strip(),
        "created": today,
        "updated": today,
        "solved": "no",
        "topic": topic.strip(),
        "priority": priority,
        "answer_ref": "",
    }


def mark_proposal_approved(proposal_text: str) -> str:
    """Update proposal frontmatter to status: approved; return new content.

    Preserves all other fields; only sets/replaces the status field.
    """
    m = FM_RE.match(proposal_text)
    if not m:
        return proposal_text

    fm_block = m.group(1)
    body = proposal_text[m.end() :].lstrip("\n")

    # Remove existing status line if present.
    lines = fm_block.splitlines()
    new_lines = [
        line for line in lines if not line.strip().startswith("status:")
    ]
    new_lines.append("status: approved")

    return f"---\n{chr(10).join(new_lines)}\n---\n{body}"


def mark_question_solved(
    question_text: str, answer_ref: str, today: str | None = None
) -> str:
    """Update research_question to solved: yes + answer_ref. Return new content.

    Args:
        question_text: Full question file content.
        answer_ref: The reference path (e.g. "research/Q-0001.md").
        today: YYYY-MM-DD date for the updated field. Defaults to today.

    Returns:
        Updated content with solved: yes and answer_ref set.
    """
    if today is None:
        today = datetime.date.today().isoformat()

    m = FM_RE.match(question_text)
    if not m:
        return question_text

    fm_block = m.group(1)
    body = question_text[m.end() :].lstrip("\n")

    # Parse and update the frontmatter.
    lines = fm_block.splitlines()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("solved:") or stripped.startswith("answer_ref:"):
            continue  # Skip existing solved and answer_ref lines.
        new_lines.append(line)

    # Append solved and answer_ref.
    new_lines.append('solved: "yes"')
    new_lines.append(f"answer_ref: {answer_ref}")

    # Update the updated field if present.
    final_lines = []
    for line in new_lines:
        if line.strip().startswith("updated:"):
            final_lines.append(f"updated: {today}")
        else:
            final_lines.append(line)

    return f"---\n{chr(10).join(final_lines)}\n---\n{body}"
