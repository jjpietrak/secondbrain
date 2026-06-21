#!/usr/bin/env python3
"""Hermetic tests for scripts/research_synthesis.py.

No LLM, no network, no real vault.  $0.

Covers:
  - gather_local_context: returns hits for matching keywords, misses for
    non-matching, includes objective node hits, excludes templates + index files.
  - excerpts_to_wiki_baseline: formats correctly; fallback string when empty.
  - fill_analysis_prompt: rendered string contains all injected values and the
    READY terminator line is present (from the template).
  - fill_synthesis_prompt: same checks.
  - parse_directions (round-trip): sample synthesis output -> non-empty list
    with expected field keys.
  - parse_proposals (round-trip): sample synthesis output -> proposal dicts.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

# Ensure repo root is importable regardless of cwd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.research_synthesis import (  # noqa: E402
    gather_local_context,
    excerpts_to_wiki_baseline,
    fill_analysis_prompt,
    fill_synthesis_prompt,
    parse_directions,
    parse_proposals,
)

# ---------------------------------------------------------------------------
# Fixture: minimal vault
# ---------------------------------------------------------------------------

def _make_fixture_vault(tmp: Path) -> Path:
    """Build a minimal vault under tmp/vault/ with 3 wiki pages."""
    vault = tmp / "vault"

    # Wiki dirs
    for d in ("wiki/concepts", "wiki/entities", "wiki/sources"):
        (vault / d).mkdir(parents=True, exist_ok=True)

    # Page 1 -- HBM bandwidth (matches 'hbm bandwidth' query)
    (vault / "wiki" / "concepts" / "hbm-bandwidth.md").write_text(
        textwrap.dedent("""\
        ---
        type: concept
        title: "HBM Memory Bandwidth"
        created: 2026-01-10
        ai-first: true
        ---
        # HBM Memory Bandwidth

        HBM bandwidth is the primary bottleneck in large transformer inference.
        Modern H100 GPUs offer ~3.35 TB/s HBM3 bandwidth.

        ## Open Questions
        - What is the achievable memory bandwidth utilization at batch size 1?
        """),
        encoding="utf-8",
    )

    # Page 2 -- latency floor (matches 'latency floor inference' query)
    (vault / "wiki" / "concepts" / "latency-floor.md").write_text(
        textwrap.dedent("""\
        ---
        type: concept
        title: "Inference Latency Floor"
        created: 2026-02-05
        ai-first: true
        ---
        # Inference Latency Floor

        The latency floor of an inference request is determined by the
        memory-bandwidth-bound decode phase, not the compute-bound prefill.

        ## For future Claude
        This note needs a concrete benchmark citation from a 2025 or later paper.
        """),
        encoding="utf-8",
    )

    # Page 3 -- unrelated topic (should NOT match 'hbm bandwidth' query)
    (vault / "wiki" / "entities" / "unrelated-company.md").write_text(
        textwrap.dedent("""\
        ---
        type: entity
        title: "Acme Corp"
        created: 2026-03-01
        ---
        # Acme Corp

        A company that makes widgets and gadgets. Nothing to do with ML or GPUs.
        """),
        encoding="utf-8",
    )

    # Template (must be excluded)
    (vault / "wiki" / "concepts" / "_template.md").write_text(
        "---\ntype: concept\ntitle: Template\n---\n# Template\n",
        encoding="utf-8",
    )

    # index.md (must be excluded)
    (vault / "wiki" / "index.md").write_text(
        "---\ntype: index\n---\n# Index\n",
        encoding="utf-8",
    )

    return vault


# ---------------------------------------------------------------------------
# Test: gather_local_context -- wiki scan
# ---------------------------------------------------------------------------

def test_gather_local_context_returns_relevant_pages():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        hits = gather_local_context("hbm bandwidth", wiki_root=vault)
    # Should find hbm-bandwidth.md; latency-floor.md has no 'hbm' keyword
    paths = [h["path"] for h in hits]
    assert any("hbm-bandwidth" in p for p in paths), (
        f"Expected hbm-bandwidth.md in results; got: {paths}"
    )
    # All hits must have required keys
    for h in hits:
        for key in ("path", "abs_path", "score", "excerpt", "source"):
            assert key in h, f"Missing key '{key}' in hit: {h}"


def test_gather_local_context_excludes_templates_and_index():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        # Query broad enough to match anything
        hits = gather_local_context("claude template index", wiki_root=vault)
    paths = [h["path"] for h in hits]
    for p in paths:
        assert "_template" not in p, f"Template file leaked into results: {p}"
        assert "index.md" not in p.split("/")[-1], (
            f"index.md leaked into results: {p}"
        )


def test_gather_local_context_unrelated_query_returns_empty():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        hits = gather_local_context("zxqwerty foobar", wiki_root=vault)
    assert hits == [], f"Expected no hits for nonsense query; got: {hits}"


def test_gather_local_context_empty_vault():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "empty_vault"
        vault.mkdir()
        hits = gather_local_context("hbm bandwidth", wiki_root=vault)
    assert hits == []


# ---------------------------------------------------------------------------
# Test: gather_local_context -- objective_nodes
# ---------------------------------------------------------------------------

def test_gather_local_context_includes_objective_nodes():
    """Objective node with matching text should appear in results."""
    nodes = [
        {
            "type": "research_question",
            "id": "Q-0001",
            "path": "objective/research_question/Q-0001-latency-floor.md",
            "abs_path": "objective/research_question/Q-0001-latency-floor.md",
            "body": "What is the latency floor for single-request inference on H100?",
            "solved": "no",
        },
        {
            "type": "direction",
            "id": "DIR-0001",
            "path": "objective/direction/DIR-0001-hbm.md",
            "abs_path": "objective/direction/DIR-0001-hbm.md",
            "body": "HBM bandwidth utilization at low batch sizes determines latency floor.",
            "status": "open",
        },
        {
            "type": "agent_todo",  # NOT in OBJECTIVE_CONTEXT_TYPES -> excluded
            "id": "TODO-0001",
            "path": "objective/agent_todo/TODO-0001.md",
            "body": "HBM HBM HBM latency floor latency floor",
        },
    ]
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        hits = gather_local_context(
            "latency floor hbm bandwidth",
            wiki_root=vault,
            objective_nodes=nodes,
        )

    sources = {h["source"] for h in hits}
    objective_hits = [h for h in hits if h["source"] == "objective"]
    assert "objective" in sources, "Expected objective-sourced hits"
    # agent_todo must be excluded (not in OBJECTIVE_CONTEXT_TYPES)
    todo_paths = [h["path"] for h in objective_hits]
    assert not any("TODO" in p for p in todo_paths), (
        f"agent_todo should be excluded; got: {todo_paths}"
    )


def test_gather_local_context_sorts_by_score_descending():
    """Higher-scoring pages appear first."""
    nodes = [
        {
            "type": "research_question",
            "id": "Q-0002",
            "path": "objective/research_question/Q-0002.md",
            "abs_path": "objective/research_question/Q-0002.md",
            # Very high keyword density -> high score
            "body": "hbm bandwidth hbm bandwidth hbm bandwidth hbm bandwidth",
            "solved": "no",
        },
    ]
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        hits = gather_local_context("hbm bandwidth", wiki_root=vault, objective_nodes=nodes)

    assert len(hits) >= 2
    # Scores should be non-increasing
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True), (
        f"Hits not sorted by score descending: {scores}"
    )


# ---------------------------------------------------------------------------
# Test: excerpts_to_wiki_baseline
# ---------------------------------------------------------------------------

def test_excerpts_to_wiki_baseline_formats_correctly():
    excerpts = [
        {"path": "wiki/concepts/hbm-bandwidth.md", "score": 12, "excerpt": "HBM is fast."},
        {"path": "wiki/concepts/latency-floor.md", "score": 7, "excerpt": "Latency floor details."},
    ]
    result = excerpts_to_wiki_baseline(excerpts)
    assert "[[wiki/concepts/hbm-bandwidth.md]]" in result
    assert "score=12" in result
    assert "HBM is fast." in result
    assert "---" in result  # separator between chunks


def test_excerpts_to_wiki_baseline_empty_returns_fallback():
    result = excerpts_to_wiki_baseline([])
    assert "no existing notes" in result.lower() or "nothing" in result.lower() or "vault" in result.lower()


# ---------------------------------------------------------------------------
# Test: fill_analysis_prompt
# ---------------------------------------------------------------------------

SAMPLE_PURPOSE = "Understand the latency floor of disaggregated inference systems."
SAMPLE_TODAY = "2026-06-21"
SAMPLE_OPEN_QUESTIONS = "- Q-0001: What is the HBM bandwidth utilization at batch size 1?"
SAMPLE_ACTIVE_TOPICS = "- T-0001: Inference disaggregation"
SAMPLE_WIKI_BASELINE = "### [[wiki/concepts/hbm-bandwidth.md]] (score=12)\n\nHBM bandwidth is 3.35 TB/s on H100."
SAMPLE_WIKI_GAPS = "- Gap: no concrete benchmark for batch-size-1 utilization"


def test_fill_analysis_prompt_contains_all_inputs():
    result = fill_analysis_prompt(
        purpose=SAMPLE_PURPOSE,
        today=SAMPLE_TODAY,
        open_questions=SAMPLE_OPEN_QUESTIONS,
        active_topics=SAMPLE_ACTIVE_TOPICS,
        wiki_baseline=SAMPLE_WIKI_BASELINE,
        wiki_gaps=SAMPLE_WIKI_GAPS,
    )
    assert SAMPLE_PURPOSE in result
    assert SAMPLE_TODAY in result
    assert "Q-0001" in result
    assert "T-0001" in result
    assert "hbm-bandwidth.md" in result
    assert "batch-size-1" in result
    # Template must end with READY
    assert "READY" in result


def test_fill_analysis_prompt_default_wiki_gaps():
    """wiki_gaps defaults to '(none)' -- no KeyError."""
    result = fill_analysis_prompt(
        purpose=SAMPLE_PURPOSE,
        today=SAMPLE_TODAY,
        open_questions=SAMPLE_OPEN_QUESTIONS,
        active_topics=SAMPLE_ACTIVE_TOPICS,
        wiki_baseline=SAMPLE_WIKI_BASELINE,
    )
    assert "(none)" in result


# ---------------------------------------------------------------------------
# Test: fill_synthesis_prompt
# ---------------------------------------------------------------------------

SAMPLE_GAP_ANALYSIS = "- Q-0001 Gap: missing batch-size-1 HBM utilization measurement"
SAMPLE_EXISTING_DIRECTIONS = "(none)"


def test_fill_synthesis_prompt_contains_all_inputs():
    result = fill_synthesis_prompt(
        purpose=SAMPLE_PURPOSE,
        today=SAMPLE_TODAY,
        open_questions=SAMPLE_OPEN_QUESTIONS,
        active_topics=SAMPLE_ACTIVE_TOPICS,
        gap_analysis=SAMPLE_GAP_ANALYSIS,
        existing_directions=SAMPLE_EXISTING_DIRECTIONS,
    )
    assert SAMPLE_PURPOSE in result
    assert SAMPLE_TODAY in result
    assert "Q-0001" in result
    assert "T-0001" in result
    assert "batch-size-1" in result
    assert "(none)" in result
    assert "READY" in result


def test_fill_synthesis_prompt_default_existing_directions():
    """existing_directions defaults to '(none)' -- no KeyError."""
    result = fill_synthesis_prompt(
        purpose=SAMPLE_PURPOSE,
        today=SAMPLE_TODAY,
        open_questions=SAMPLE_OPEN_QUESTIONS,
        active_topics=SAMPLE_ACTIVE_TOPICS,
        gap_analysis=SAMPLE_GAP_ANALYSIS,
    )
    assert "(none)" in result


# ---------------------------------------------------------------------------
# Test: parse_directions round-trip
# ---------------------------------------------------------------------------

SAMPLE_SYNTHESIS_OUTPUT = """\
### DIRECTION: Measure HBM utilization at batch size 1
- serves_question: Q-0001
- topics: T-0001
- targets_gap: no concrete benchmark for batch-size-1 utilization
- reasoning_pattern: Vendor benchmarks and academic papers usually report peak throughput, not single-request utilization; targeted search for roofline or memory-bandwidth-bound characterizations at batch 1 will surface the real floor.
- expected_evidence: a benchmarked measurement on real hardware (H100), not a survey
- seed_queries: "H100 inference batch size 1 HBM bandwidth utilization" | arxiv, "memory bandwidth bound LLM decode single request" | web
- solves_when: a concrete utilization percentage (or bytes/token figure) for batch-size-1 decode on H100 or equivalent
- priority: high  -  directly unblocks Q-0001 which is the primary open question

### DIRECTION: Compare disaggregated vs co-located prefill latency
- serves_question: Q-0001
- topics: T-0001
- targets_gap: no comparison of disaggregated vs co-located prefill impact on latency floor
- reasoning_pattern: Disaggregation separates prefill and decode compute; find papers measuring end-to-end latency under both configurations to isolate the disaggregation overhead.
- expected_evidence: a paper or benchmark with latency breakdown (prefill vs decode) under disaggregated setup
- seed_queries: "disaggregated inference prefill decode latency comparison" | arxiv, "PD disaggregation latency overhead" | web
- solves_when: latency floor comparison table between disaggregated and non-disaggregated setup
- priority: medium  -  secondary to direct HBM utilization measurement

## Proposed New Research Questions
- proposal: What is the minimum achievable TTFT for a 70B model on disaggregated H100s? | rationale: TTFT is not covered by any current question but is central to the vault purpose | from_gap: no TTFT floor measurement
- [none]

## Already Answerable
- none

READY
"""


def test_parse_directions_returns_non_empty():
    dirs = parse_directions(SAMPLE_SYNTHESIS_OUTPUT)
    assert len(dirs) >= 2, f"Expected >= 2 directions; got {len(dirs)}"


def test_parse_directions_field_keys():
    dirs = parse_directions(SAMPLE_SYNTHESIS_OUTPUT)
    required_fields = {
        "serves_question", "topics", "targets_gap", "reasoning_pattern",
        "expected_evidence", "seed_queries", "solves_when", "priority",
    }
    for d in dirs:
        assert "title" in d, f"Direction missing 'title': {d}"
        assert "fields" in d, f"Direction missing 'fields': {d}"
        for field in required_fields:
            assert field in d["fields"], (
                f"Field '{field}' missing from direction '{d['title']}'; "
                f"got: {list(d['fields'].keys())}"
            )


def test_parse_directions_titles():
    dirs = parse_directions(SAMPLE_SYNTHESIS_OUTPUT)
    titles = [d["title"] for d in dirs]
    assert any("HBM" in t for t in titles), f"Expected HBM direction; got: {titles}"
    assert any("disaggregat" in t.lower() for t in titles), (
        f"Expected disaggregated direction; got: {titles}"
    )


# ---------------------------------------------------------------------------
# Test: parse_proposals round-trip
# ---------------------------------------------------------------------------

def test_parse_proposals_returns_non_empty():
    proposals = parse_proposals(SAMPLE_SYNTHESIS_OUTPUT)
    assert len(proposals) >= 1, f"Expected >= 1 proposal; got {proposals}"


def test_parse_proposals_fields():
    proposals = parse_proposals(SAMPLE_SYNTHESIS_OUTPUT)
    for p in proposals:
        assert "proposal" in p, f"Proposal missing 'proposal' key: {p}"
        assert "rationale" in p, f"Proposal missing 'rationale' key: {p}"
        assert "from_gap" in p, f"Proposal missing 'from_gap' key: {p}"


def test_parse_proposals_no_proposals_marker():
    """Output that explicitly lists '[none]' should yield no proposals."""
    no_proposal_output = """\
### DIRECTION: Some direction
- serves_question: Q-0001
- topics: T-0001
- targets_gap: some gap
- reasoning_pattern: some pattern
- expected_evidence: some evidence
- seed_queries: "query" | web
- solves_when: when answered
- priority: low  -  low priority

## Proposed New Research Questions
- none

## Already Answerable
- none

READY
"""
    proposals = parse_proposals(no_proposal_output)
    # "none" is not a valid proposal line (no '|' separator with proposal: field)
    assert proposals == [], f"Expected no proposals; got {proposals}"


# ---------------------------------------------------------------------------
# Test: decoupling -- no agents.objectives import
# ---------------------------------------------------------------------------

def test_no_agents_objectives_import():
    """research_synthesis must NOT import agents.objectives."""
    import ast
    src = (ROOT / "scripts" / "research_synthesis.py").read_text()
    tree = ast.parse(src)
    # Collect all import module names
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.append(node.module)
    assert not any("agents.objectives" in m for m in imported_modules), (
        "research_synthesis.py must NOT import agents.objectives -- "
        f"objective nodes are passed as args, not imported. Imports found: {imported_modules}"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
