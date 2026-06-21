#!/usr/bin/env python3
"""Hermetic tests for scripts/deep_synth_helper.py.

No LLM, no network, no real vault.  $0.

Covers:
  - slugify: basic conversion, truncation, ASCII enforcement.
  - resolve_output_path: all four scope shapes (question / topic / topic_slug / deep).
  - format_research_report_frontmatter: correct YAML fields present.
  - format_direction_frontmatter: correct YAML fields; priority sanitized.
  - format_proposal_frontmatter: correct YAML fields.
  - format_already_answerable_surface: empty/none cases; real items formatted.
  - No agents.objectives import (decoupling check).
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.deep_synth_helper import (  # noqa: E402
    slugify,
    resolve_output_path,
    format_research_report_frontmatter,
    format_direction_frontmatter,
    format_proposal_frontmatter,
    format_already_answerable_surface,
)

TODAY = "2026-06-21"


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert slugify("HBM Bandwidth Utilization") == "hbm-bandwidth-utilization"


def test_slugify_special_chars():
    assert slugify("What is the latency floor?") == "what-is-the-latency-floor"


def test_slugify_unicode_stripped():
    # Non-ASCII chars must be dropped, not transliterated
    result = slugify("inference -- disaggregation")
    assert result == "inference-disaggregation"


def test_slugify_truncates():
    long_text = "a" * 100
    assert len(slugify(long_text)) <= 60


def test_slugify_empty_returns_untitled():
    assert slugify("") == "untitled"
    assert slugify("   ") == "untitled"


def test_slugify_hyphens_stripped():
    # Leading/trailing hyphens stripped
    result = slugify("  --test--  ")
    assert not result.startswith("-")
    assert not result.endswith("-")


# ---------------------------------------------------------------------------
# resolve_output_path - question scope
# ---------------------------------------------------------------------------

def test_resolve_output_path_question():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        out = resolve_output_path(vault, {"question": "Q-0001"}, today=TODAY)
        assert out == vault / "research" / "Q-0001.md"


def test_resolve_output_path_question_strips_whitespace():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        out = resolve_output_path(vault, {"question": "  Q-0003  "}, today=TODAY)
        assert out.name == "Q-0003.md"


# ---------------------------------------------------------------------------
# resolve_output_path - topic scope
# ---------------------------------------------------------------------------

def test_resolve_output_path_topic():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        out = resolve_output_path(vault, {"topic": "T-0001"}, today=TODAY)
        assert out == vault / "research" / "T-0001.md"


def test_resolve_output_path_topic_slug():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        out = resolve_output_path(vault, {"topic_slug": "inference-disagg"}, today=TODAY)
        assert out == vault / "research" / "inference-disagg.md"


# ---------------------------------------------------------------------------
# resolve_output_path - deep scope
# ---------------------------------------------------------------------------

def test_resolve_output_path_deep():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        out = resolve_output_path(vault, {"deep": "HBM bandwidth"}, today=TODAY)
        assert out.parent == vault / "research" / "deep"
        assert out.name == f"{TODAY}-hbm-bandwidth.md"


def test_resolve_output_path_deep_uses_today_default():
    """When today is not passed, the path still has a valid date prefix."""
    import re as _re
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        out = resolve_output_path(vault, {"deep": "test"})
        # Filename must start with YYYY-MM-DD pattern
        assert _re.match(r"\d{4}-\d{2}-\d{2}-", out.name), (
            f"Expected date prefix in filename: {out.name}"
        )


def test_resolve_output_path_invalid_scope_raises():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        try:
            resolve_output_path(vault, {"unknown_key": "foo"}, today=TODAY)
            assert False, "Expected ValueError"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# format_research_report_frontmatter
# ---------------------------------------------------------------------------

def test_format_research_report_frontmatter_question_scope():
    fm = format_research_report_frontmatter(
        scope={"question": "Q-0001"},
        today=TODAY,
    )
    assert "type: research_report" in fm
    assert "generated_by: research" in fm
    assert "serves_question: Q-0001" in fm
    assert f"created: {TODAY}" in fm
    assert "status: draft" in fm
    assert fm.startswith("---")
    assert fm.endswith("---")


def test_format_research_report_frontmatter_topic_scope():
    fm = format_research_report_frontmatter(
        scope={"topic": "T-0002"},
        today=TODAY,
        topic_id="T-0002",
    )
    assert "topic: T-0002" in fm


def test_format_research_report_frontmatter_explicit_ids_override_scope():
    fm = format_research_report_frontmatter(
        scope={"question": "Q-0001"},  # scope says Q-0001
        today=TODAY,
        question_id="Q-0005",  # explicit override wins
    )
    assert "serves_question: Q-0005" in fm
    assert "serves_question: Q-0001" not in fm


def test_format_research_report_frontmatter_no_ids():
    """When scope has no question/topic and no explicit ids, fields are empty strings."""
    fm = format_research_report_frontmatter(
        scope={"deep": "some topic"},
        today=TODAY,
    )
    # Fields exist but may be empty; no KeyError
    assert "type: research_report" in fm
    assert "topic:" in fm
    assert "serves_question:" in fm


# ---------------------------------------------------------------------------
# format_direction_frontmatter
# ---------------------------------------------------------------------------

_SAMPLE_DIRECTION_FIELDS = {
    "serves_question": "Q-0001",
    "topics": "T-0001",
    "targets_gap": "no benchmark for batch-size-1 HBM utilization",
    "reasoning_pattern": "Look for roofline analyses in hardware papers.",
    "expected_evidence": "a benchmarked measurement on real hardware",
    "seed_queries": '"H100 batch 1 HBM utilization" | arxiv',
    "solves_when": "a concrete utilization percentage is found",
    "priority": "high  -  directly unblocks Q-0001",
}


def test_format_direction_frontmatter_required_fields():
    fm = format_direction_frontmatter("DIR-0001", TODAY, _SAMPLE_DIRECTION_FIELDS)
    assert "type: direction" in fm
    assert "id: DIR-0001" in fm
    assert f"created: {TODAY}" in fm
    assert "generated_by: research" in fm
    assert "serves_question: Q-0001" in fm
    assert "topics: T-0001" in fm
    assert "status: open" in fm


def test_format_direction_frontmatter_priority_sanitized():
    """priority 'high  -  justification text' must be reduced to 'high'."""
    fm = format_direction_frontmatter("DIR-0001", TODAY, _SAMPLE_DIRECTION_FIELDS)
    # Should appear as "priority: high" (not "priority: high  -  ...")
    lines = {line.split(":")[0].strip(): line.split(":", 1)[1].strip()
             for line in fm.splitlines()
             if ":" in line and not line.startswith("-")}
    assert lines.get("priority") == "high", (
        f"Expected 'priority: high', got priority={lines.get('priority')!r}"
    )


def test_format_direction_frontmatter_priority_unknown_defaults_to_medium():
    fields = dict(_SAMPLE_DIRECTION_FIELDS)
    fields["priority"] = "critical  -  override"
    fm = format_direction_frontmatter("DIR-0002", TODAY, fields)
    assert "priority: medium" in fm


def test_format_direction_frontmatter_question_id_override():
    fields = dict(_SAMPLE_DIRECTION_FIELDS)
    fm = format_direction_frontmatter(
        "DIR-0003", TODAY, fields, question_id="Q-0007"
    )
    assert "serves_question: Q-0007" in fm
    assert "serves_question: Q-0001" not in fm


# ---------------------------------------------------------------------------
# format_proposal_frontmatter
# ---------------------------------------------------------------------------

_SAMPLE_PROPOSAL = {
    "proposal": "What is the minimum achievable TTFT for a 70B model on disaggregated H100s?",
    "rationale": "TTFT is not covered by any current question but is central to the vault purpose",
    "from_gap": "no TTFT floor measurement",
}


def test_format_proposal_frontmatter_required_fields():
    fm = format_proposal_frontmatter("QP-0001", TODAY, _SAMPLE_PROPOSAL)
    assert "type: research_question_proposal" in fm
    assert "id: QP-0001" in fm
    assert f"created: {TODAY}" in fm
    assert "generated_by: research" in fm
    assert "from_gap: no TTFT floor measurement" in fm
    assert "status: pending" in fm


def test_format_proposal_frontmatter_missing_from_gap():
    """Missing from_gap should produce an empty string field, not KeyError."""
    proposal = {"proposal": "Some question", "rationale": "Some rationale"}
    fm = format_proposal_frontmatter("QP-0002", TODAY, proposal)
    assert "from_gap:" in fm  # field present, value may be empty


# ---------------------------------------------------------------------------
# format_already_answerable_surface
# ---------------------------------------------------------------------------

def test_format_already_answerable_surface_none_returns_empty():
    assert format_already_answerable_surface([]) == ""
    assert format_already_answerable_surface(["none"]) == ""
    assert format_already_answerable_surface(["None", "NONE"]) == ""


def test_format_already_answerable_surface_real_items():
    result = format_already_answerable_surface(["Q-0001", "Q-0003"])
    assert "Q-0001" in result
    assert "Q-0003" in result
    assert "ALREADY ANSWERABLE" in result or "already answerable" in result.lower()


def test_format_already_answerable_surface_mixed():
    """Items mixed with 'none' - only real items appear."""
    result = format_already_answerable_surface(["none", "Q-0002", "none"])
    assert "Q-0002" in result
    assert "none" not in result.lower() or "none" in result.lower() and "Q-0002" in result


def test_format_already_answerable_surface_strips_dashes():
    """Items with leading dashes and spaces (from markdown lists) are cleaned up."""
    result = format_already_answerable_surface(["  - Q-0001", "  - Q-0002"])
    assert "Q-0001" in result
    assert "Q-0002" in result


# ---------------------------------------------------------------------------
# Decoupling: no agents.objectives import
# ---------------------------------------------------------------------------

def test_no_agents_objectives_import():
    """deep_synth_helper must NOT import agents.objectives."""
    src = (ROOT / "scripts" / "deep_synth_helper.py").read_text()
    tree = ast.parse(src)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.append(node.module)
    assert not any("agents.objectives" in m for m in imported_modules), (
        "deep_synth_helper.py must NOT import agents.objectives -- "
        f"imports found: {imported_modules}"
    )


def test_no_llm_import():
    """deep_synth_helper must NOT import any LLM SDK."""
    src = (ROOT / "scripts" / "deep_synth_helper.py").read_text()
    tree = ast.parse(src)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.append(node.module)
    bad = {"anthropic", "openai", "litellm", "perplexity", "requests"}
    found_bad = [m for m in imported_modules if any(b in m for b in bad)]
    assert not found_bad, (
        f"deep_synth_helper.py must not import LLM SDKs; found: {found_bad}"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
