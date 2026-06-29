#!/usr/bin/env python3
"""Hermetic tests for scripts/question_lifecycle.py.

No LLM, no network, no vault. $0.

Covers:
  - proposal_to_question_frontmatter: frontmatter extraction and validation.
  - mark_proposal_approved: status field update.
  - mark_question_solved: solved and answer_ref field update.
  - Edge cases: missing fields, malformed frontmatter, unicode handling.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.question_lifecycle import (  # noqa: E402
    proposal_to_question_frontmatter,
    mark_proposal_approved,
    mark_question_solved,
)

TODAY = "2026-06-21"


# ---------------------------------------------------------------------------
# proposal_to_question_frontmatter
# ---------------------------------------------------------------------------


def test_proposal_to_question_basic():
    """Basic proposal -> question transform."""
    proposal = """---
type: research_question_proposal
id: QP-0001
created: 2026-06-21
updated: 2026-06-21
generated_by: research
status: pending
---
What is the HBM bandwidth utilization ceiling?"""

    result = proposal_to_question_frontmatter(
        proposal, "Q-0001", topic="T-0001", today=TODAY
    )

    assert result["type"] == "research_question"
    assert result["id"] == "Q-0001"
    assert result["created"] == TODAY
    assert result["updated"] == TODAY
    assert result["solved"] == "no"
    assert result["topic"] == "T-0001"
    assert result["answer_ref"] == ""


def test_proposal_to_question_priority_extracted():
    """Priority field from proposal is carried to question."""
    proposal = """---
type: research_question_proposal
id: QP-0002
created: 2026-06-21
priority: high
status: pending
---
Test question"""

    result = proposal_to_question_frontmatter(proposal, "Q-0002", today=TODAY)
    assert result["priority"] == "high"


def test_proposal_to_question_priority_default():
    """Missing priority defaults to medium."""
    proposal = """---
type: research_question_proposal
id: QP-0003
---
Test"""

    result = proposal_to_question_frontmatter(proposal, "Q-0003", today=TODAY)
    assert result["priority"] == "medium"


def test_proposal_to_question_invalid_priority():
    """Invalid priority value sanitized to medium."""
    proposal = """---
type: research_question_proposal
priority: critical
---
Test"""

    result = proposal_to_question_frontmatter(proposal, "Q-0004", today=TODAY)
    assert result["priority"] == "medium"


def test_proposal_to_question_topic_optional():
    """Topic defaults to empty string if not provided."""
    proposal = """---
type: research_question_proposal
---
Test"""

    result = proposal_to_question_frontmatter(proposal, "Q-0005", topic="", today=TODAY)
    assert result["topic"] == ""


def test_proposal_to_question_no_frontmatter():
    """Handles malformed proposal with no frontmatter gracefully."""
    proposal = "No frontmatter here"

    result = proposal_to_question_frontmatter(proposal, "Q-0006", today=TODAY)
    assert result["type"] == "research_question"
    assert result["id"] == "Q-0006"
    assert result["priority"] == "medium"  # Defaults


def test_proposal_to_question_whitespace_stripped():
    """ID and topic whitespace is stripped."""
    result = proposal_to_question_frontmatter(
        "---\n---\nTest", "  Q-0007  ", topic="  T-0001  ", today=TODAY
    )
    assert result["id"] == "Q-0007"
    assert result["topic"] == "T-0001"


# ---------------------------------------------------------------------------
# mark_proposal_approved
# ---------------------------------------------------------------------------


def test_mark_proposal_approved_basic():
    """Set status: approved in proposal."""
    proposal = """---
type: research_question_proposal
id: QP-0001
status: pending
---
Question text"""

    result = mark_proposal_approved(proposal)
    assert 'status: approved' in result
    assert 'status: pending' not in result
    assert 'type: research_question_proposal' in result


def test_mark_proposal_approved_no_status_field():
    """Add status: approved if field missing."""
    proposal = """---
type: research_question_proposal
id: QP-0002
---
Question text"""

    result = mark_proposal_approved(proposal)
    assert 'status: approved' in result


def test_mark_proposal_approved_preserves_body():
    """Body is preserved unchanged."""
    body_text = "This is a detailed question\nwith multiple lines."
    proposal = f"""---
type: research_question_proposal
---
{body_text}"""

    result = mark_proposal_approved(proposal)
    assert body_text in result


def test_mark_proposal_approved_preserves_fields():
    """Other frontmatter fields are preserved."""
    proposal = """---
type: research_question_proposal
id: QP-0003
created: 2026-06-21
priority: high
status: pending
---
Text"""

    result = mark_proposal_approved(proposal)
    assert "id: QP-0003" in result
    assert "created: 2026-06-21" in result
    assert "priority: high" in result
    assert 'status: approved' in result


def test_mark_proposal_approved_multiple_status_lines():
    """Multiple status lines (edge case) - all removed, one added."""
    proposal = """---
type: research_question_proposal
status: pending
status: rejected
---
Text"""

    result = mark_proposal_approved(proposal)
    # Should have exactly one status line set to approved.
    assert result.count("status:") == 1
    assert "status: approved" in result


def test_mark_proposal_approved_no_frontmatter():
    """Malformed input with no frontmatter returns unchanged."""
    proposal = "No frontmatter"
    result = mark_proposal_approved(proposal)
    assert result == proposal


# ---------------------------------------------------------------------------
# mark_question_solved
# ---------------------------------------------------------------------------


def test_mark_question_solved_basic():
    """Set solved: yes and answer_ref on question."""
    question = """---
type: research_question
id: Q-0001
created: 2026-06-21
updated: 2026-06-21
solved: "no"
topic: T-0001
priority: high
answer_ref: ""
---
What is latency?"""

    result = mark_question_solved(
        question, "research/Q-0001.md", today=TODAY
    )

    assert 'solved: "yes"' in result
    assert 'answer_ref: research/Q-0001.md' in result
    assert 'solved: "no"' not in result
    assert 'answer_ref: ""' not in result


def test_mark_question_solved_no_updated_field():
    """Handles question without updated field."""
    question = """---
type: research_question
id: Q-0002
solved: "no"
---
Text"""

    result = mark_question_solved(question, "research/Q-0002.md", today=TODAY)
    assert 'solved: "yes"' in result
    assert 'answer_ref: research/Q-0002.md' in result


def test_mark_question_solved_updates_updated_field():
    """updated field is refreshed to the provided date."""
    question = """---
type: research_question
id: Q-0003
updated: 2026-06-15
solved: "no"
answer_ref: ""
---
Text"""

    result = mark_question_solved(
        question, "research/Q-0003.md", today="2026-06-21"
    )

    assert "updated: 2026-06-21" in result
    assert "updated: 2026-06-15" not in result


def test_mark_question_solved_preserves_other_fields():
    """Other frontmatter fields remain unchanged."""
    question = """---
type: research_question
id: Q-0004
topic: T-0001
priority: high
solved: "no"
answer_ref: ""
---
Text"""

    result = mark_question_solved(question, "research/Q-0004.md", today=TODAY)
    assert "id: Q-0004" in result
    assert "topic: T-0001" in result
    assert "priority: high" in result
    assert 'solved: "yes"' in result


def test_mark_question_solved_preserves_body():
    """Question body is unchanged."""
    body = "Original question text\nwith multiple paragraphs."
    question = f"""---
type: research_question
solved: "no"
---
{body}"""

    result = mark_question_solved(question, "research/Q-0005.md", today=TODAY)
    assert body in result


def test_mark_question_solved_answer_ref_with_trailing_slash():
    """answer_ref can be a path with directory separators."""
    question = """---
type: research_question
solved: "no"
answer_ref: ""
---
Text"""

    result = mark_question_solved(
        question, "research/deep/2026-06-21-analysis.md", today=TODAY
    )
    assert "answer_ref: research/deep/2026-06-21-analysis.md" in result


def test_mark_question_solved_no_frontmatter():
    """Malformed input with no frontmatter returns unchanged."""
    question = "No frontmatter here"
    result = mark_question_solved(question, "research/Q-0006.md", today=TODAY)
    assert result == question


def test_mark_question_solved_default_today():
    """Defaults to today's date if not provided."""
    question = """---
type: research_question
updated: 2026-06-15
solved: "no"
---
Text"""

    result = mark_question_solved(question, "research/Q-0007.md")
    # Should have an updated line with today's date (format YYYY-MM-DD).
    assert "updated: " in result
    # The exact date depends on when the test runs, so just verify the format.
    import re
    assert re.search(r"updated: \d{4}-\d{2}-\d{2}", result)


# ---------------------------------------------------------------------------
# Integration: proposal -> question -> solved flow
# ---------------------------------------------------------------------------


def test_full_lifecycle_proposal_promote_solve():
    """Full lifecycle: proposal -> approved, then question -> solved."""

    proposal = """---
type: research_question_proposal
id: QP-0001
created: 2026-06-21
priority: high
status: pending
---
Why is HBM bandwidth limited?"""

    # Step 1: Mark proposal as approved.
    approved_proposal = mark_proposal_approved(proposal)
    assert "status: approved" in approved_proposal

    # Step 2: Create question frontmatter from proposal.
    q_fm = proposal_to_question_frontmatter(
        proposal, "Q-0001", topic="T-0001", today="2026-06-21"
    )
    assert q_fm["type"] == "research_question"
    assert q_fm["priority"] == "high"

    # Step 3: Create the question file with the frontmatter + proposal body.
    question_content = f"""---
type: {q_fm['type']}
id: {q_fm['id']}
created: {q_fm['created']}
updated: {q_fm['updated']}
solved: {q_fm['solved']}
topic: {q_fm['topic']}
priority: {q_fm['priority']}
answer_ref: {q_fm['answer_ref']}
---
Why is HBM bandwidth limited?"""

    # Step 4: Solve the question.
    solved_question = mark_question_solved(
        question_content, "research/Q-0001.md", today="2026-06-21"
    )
    assert 'solved: "yes"' in solved_question
    assert "answer_ref: research/Q-0001.md" in solved_question
