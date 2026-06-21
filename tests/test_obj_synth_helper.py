"""tests/test_obj_synth_helper.py -- hermetic tests for scripts/obj_synth_helper.py.

All tests use temp dirs; no vault reads; no LLM calls; no network.
Tests cover:
  1. gather_local_context scores wiki pages by keyword relevance.
  2. excerpts_to_wiki_baseline formats the block correctly.
  3. format_open_questions formats the frontier JSON correctly.
  4. format_existing_directions formats directions correctly.
  5. fill_analysis_prompt fills the prompt without KeyError.
  6. fill_synthesis_prompt fills the prompt without KeyError.
  7. parse_directions extracts direction dicts from synthesis output.
  8. parse_proposals extracts proposal dicts from synthesis output.
  9. CLI gather-context command runs against a temp vault (exit 0, non-empty output).
  10. CLI format-questions command runs with frontier JSON (exit 0).
  11. CLI fill-analysis command runs with explicit args (exit 0).
  12. CLI fill-synthesis command runs with explicit args (exit 0).
  13. CLI parse-directions command parses synthesis output file (exit 0, JSON output).
  14. CLI parse-proposals command parses synthesis output file (exit 0, JSON output).
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup: ensure repo root is importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.research_synthesis import (
    gather_local_context,
    excerpts_to_wiki_baseline,
    fill_analysis_prompt,
    fill_synthesis_prompt,
    parse_directions,
    parse_proposals,
)
from scripts import obj_synth_helper as helper


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def temp_vault(tmp_path: Path) -> Path:
    """Create a minimal temp vault with wiki/ pages for context scoring."""
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True)

    # Page 1: highly relevant to "inference disaggregation latency"
    (wiki / "inference-latency.md").write_text(
        textwrap.dedent("""\
            ---
            type: concept
            id: inference-latency
            ---
            # Inference Latency

            ## For future Claude
            Inference latency is the primary concern for disaggregated inference systems.

            Latency floor for inference disaggregation is determined by memory bandwidth.
            Disaggregated systems separate prefill and decode stages to reduce latency.

            ## Open Questions
            - What is the exact latency floor for a 70B model on HBM3?
        """),
        encoding="utf-8",
    )

    # Page 2: moderately relevant
    (wiki / "memory-bandwidth.md").write_text(
        textwrap.dedent("""\
            ---
            type: concept
            id: memory-bandwidth
            ---
            # Memory Bandwidth

            HBM bandwidth is the bottleneck for inference throughput.
            Memory bandwidth wall limits disaggregation efficiency.
        """),
        encoding="utf-8",
    )

    # Page 3: unrelated (should score 0)
    (wiki / "unrelated-topic.md").write_text(
        textwrap.dedent("""\
            ---
            type: concept
            id: unrelated-topic
            ---
            # Web Protocols

            TCP/IP stack implementation details.
        """),
        encoding="utf-8",
    )

    # objective/ structure
    obj_dir = tmp_path / "objective"
    (obj_dir / "purpose").mkdir(parents=True)
    (obj_dir / "topic").mkdir(parents=True)
    (obj_dir / "research_question").mkdir(parents=True)
    (obj_dir / "direction").mkdir(parents=True)
    (obj_dir / "research_question_proposal").mkdir(parents=True)

    (obj_dir / "purpose" / "PURPOSE.md").write_text(
        textwrap.dedent("""\
            ---
            type: purpose
            id: purpose
            created: 2026-06-21
            updated: 2026-06-21
            status: active
            vault: Inference-Disagg
            ---
            Understand the design space of inference disaggregation systems with a focus
            on latency, memory bandwidth, and scheduling trade-offs.
        """),
        encoding="utf-8",
    )

    (obj_dir / "topic" / "T-0001-inference-disagg.md").write_text(
        textwrap.dedent("""\
            ---
            type: topic
            id: T-0001
            created: 2026-06-21
            updated: 2026-06-21
            status: active
            related_questions: [Q-0001, Q-0002]
            ---
            # Inference Disaggregation Architecture
        """),
        encoding="utf-8",
    )

    (obj_dir / "research_question" / "Q-0001-latency-floor.md").write_text(
        textwrap.dedent("""\
            ---
            type: research_question
            id: Q-0001
            created: 2026-06-21
            updated: 2026-06-21
            solved: "no"
            topic: T-0001
            priority: high
            answer_ref: ""
            ---
            # What is the latency floor for inference disaggregation?
        """),
        encoding="utf-8",
    )

    (obj_dir / "research_question" / "Q-0002-memory-bandwidth-wall.md").write_text(
        textwrap.dedent("""\
            ---
            type: research_question
            id: Q-0002
            created: 2026-06-21
            updated: 2026-06-21
            solved: "no"
            topic: T-0001
            priority: medium
            answer_ref: ""
            ---
            # How does HBM bandwidth constrain disaggregated inference throughput?
        """),
        encoding="utf-8",
    )

    # Build objective/index.md with seed counters
    (obj_dir / "index.md").write_text(
        textwrap.dedent("""\
            ---
            type: index
            updated: 2026-06-21
            next_id:
              topic: 2
              research_question: 3
              decision: 1
              research_question_proposal: 1
              direction: 1
              agent_todo: 1
            ai-first: true
            ---
            ## Operation log

            | Operation | Node | Agent | Date | Notes |
            |-----------|------|-------|------|-------|
        """),
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def sample_frontier(temp_vault: Path) -> dict:
    """Build a frontier dict that mirrors the agents.objectives output format."""
    return {
        "research_questions": [
            {
                "type": "research_question",
                "id": "Q-0001",
                "created": "2026-06-21",
                "updated": "2026-06-21",
                "solved": "no",
                "topic": "T-0001",
                "priority": "high",
                "answer_ref": "",
                "path": "objective/research_question/Q-0001-latency-floor.md",
                "body": "# What is the latency floor for inference disaggregation?\n",
            },
            {
                "type": "research_question",
                "id": "Q-0002",
                "created": "2026-06-21",
                "updated": "2026-06-21",
                "solved": "no",
                "topic": "T-0001",
                "priority": "medium",
                "answer_ref": "",
                "path": "objective/research_question/Q-0002-memory-bandwidth-wall.md",
                "body": "# How does HBM bandwidth constrain disaggregated inference throughput?\n",
            },
        ],
        "directions": [],
        "combined_ranked": [],
    }


@pytest.fixture()
def sample_synthesis_output() -> str:
    """Minimal RESEARCH_SYNTHESIS_PROMPT output with 2 directions + 1 proposal."""
    return textwrap.dedent("""\
        ### DIRECTION: Measure HBM bandwidth ceiling for 70B decode
        - serves_question: Q-0001
        - topics: T-0001
        - targets_gap: Exact latency floor for 70B inference on HBM3
        - reasoning_pattern: The latency floor is set by arithmetic intensity vs memory bandwidth; find measured hardware benchmarks on A100/H100 for 70B decode batch=1.
        - expected_evidence: A benchmarked measurement on real hardware (not a survey or whitepaper estimate)
        - seed_queries: "70B LLM decode latency HBM A100 benchmark" (web), "LLM inference arithmetic intensity" (arxiv)
        - solves_when: A peer-reviewed or vendor benchmark shows concrete tokens/sec and ms/token for batch=1 70B decode
        - priority: high - directly answers Q-0001 which is the highest-priority open question

        ### DIRECTION: Survey disaggregation scheduling latency overhead
        - serves_question: Q-0001, Q-0002
        - topics: T-0001
        - targets_gap: Scheduling overhead added by prefill/decode disaggregation
        - reasoning_pattern: Disaggregated systems add cross-node coordination latency; find empirical studies comparing monolithic vs disaggregated inference latency.
        - expected_evidence: An empirical comparison paper or system report (e.g. OSDI/SOSP/MLSys)
        - seed_queries: "disaggregated LLM inference latency overhead" (arxiv), "prefill decode disaggregation scheduling" (arxiv)
        - solves_when: A paper shows the scheduling overhead in ms and conditions under which disaggregation helps vs hurts
        - priority: medium - serves both open questions but secondary to direct measurement

        ## Proposed New Research Questions
        - proposal: What scheduling policies minimize tail latency in disaggregated inference? | rationale: Current open questions focus on throughput ceiling but not tail-latency policies | from_gap: Scheduling overhead gap identified in analysis

        ## Already Answerable
        - none

        READY
    """)


# ---------------------------------------------------------------------------
# 1-2. gather_local_context + excerpts_to_wiki_baseline
# ---------------------------------------------------------------------------

class TestGatherLocalContext:
    def test_relevant_pages_score_higher(self, temp_vault: Path) -> None:
        excerpts = gather_local_context(
            topic_or_question="inference latency disaggregation",
            wiki_root=temp_vault,
        )
        # Should return at least 2 relevant pages
        assert len(excerpts) >= 2
        paths = [e["path"] for e in excerpts]
        # The unrelated page should not appear (score=0)
        assert not any("unrelated-topic" in p for p in paths)

    def test_top_result_is_most_relevant(self, temp_vault: Path) -> None:
        excerpts = gather_local_context(
            topic_or_question="inference latency disaggregation",
            wiki_root=temp_vault,
        )
        assert excerpts[0]["score"] >= excerpts[-1]["score"]

    def test_with_objective_nodes(self, temp_vault: Path, sample_frontier: dict) -> None:
        obj_nodes = sample_frontier["research_questions"]
        excerpts = gather_local_context(
            topic_or_question="latency floor inference disaggregation",
            wiki_root=temp_vault,
            objective_nodes=obj_nodes,
        )
        assert len(excerpts) >= 1

    def test_empty_text_returns_empty(self, temp_vault: Path) -> None:
        excerpts = gather_local_context(
            topic_or_question="",
            wiki_root=temp_vault,
        )
        assert excerpts == []

    def test_baseline_format(self, temp_vault: Path) -> None:
        excerpts = gather_local_context(
            topic_or_question="inference latency memory bandwidth",
            wiki_root=temp_vault,
        )
        baseline = excerpts_to_wiki_baseline(excerpts)
        assert "###" in baseline
        assert "score=" in baseline
        assert "wiki/" in baseline

    def test_empty_excerpts_fallback(self) -> None:
        baseline = excerpts_to_wiki_baseline([])
        assert "no existing notes" in baseline.lower()


# ---------------------------------------------------------------------------
# 3-4. format_open_questions + format_existing_directions
# ---------------------------------------------------------------------------

class TestFormatFunctions:
    def test_format_open_questions_basic(self, sample_frontier: dict) -> None:
        result = helper._format_open_questions(sample_frontier)
        assert "Q-0001" in result
        assert "Q-0002" in result
        assert "high" in result
        assert "medium" in result

    def test_format_open_questions_empty(self) -> None:
        result = helper._format_open_questions({"research_questions": [], "directions": []})
        assert "no open" in result.lower()

    def test_format_existing_directions_none(self, sample_frontier: dict) -> None:
        # frontier with no directions
        result = helper._format_existing_directions(sample_frontier)
        assert result == "(none)"

    def test_format_existing_directions_populated(self, sample_frontier: dict) -> None:
        sample_frontier["directions"] = [
            {
                "id": "DIR-0001",
                "serves_question": "Q-0001",
                "priority": "high",
                "body": "# Measure HBM ceiling\n",
            }
        ]
        result = helper._format_existing_directions(sample_frontier)
        assert "DIR-0001" in result
        assert "Q-0001" in result

    def test_read_purpose(self, temp_vault: Path) -> None:
        purpose = helper._read_purpose(str(temp_vault))
        assert "inference disaggregation" in purpose.lower()

    def test_format_active_topics(self, temp_vault: Path) -> None:
        result = helper._format_active_topics_from_vault(str(temp_vault))
        assert "T-0001" in result
        assert "active" in result


# ---------------------------------------------------------------------------
# 5-6. fill_analysis_prompt + fill_synthesis_prompt (no KeyError)
# ---------------------------------------------------------------------------

class TestPromptFill:
    def test_fill_analysis_no_key_error(self) -> None:
        prompt = fill_analysis_prompt(
            purpose="Test purpose",
            today="2026-06-21",
            open_questions="Q-0001: test question [priority: high]",
            active_topics="T-0001: test topic [status: active]",
            wiki_baseline="### [[wiki/test.md]] (score=5)\n\nTest content.",
            wiki_gaps="(none)",
        )
        assert "OPEN / UNANSWERED RESEARCH QUESTIONS" in prompt
        assert "Test purpose" in prompt
        assert "Q-0001" in prompt

    def test_fill_synthesis_no_key_error(self) -> None:
        prompt = fill_synthesis_prompt(
            purpose="Test purpose",
            today="2026-06-21",
            open_questions="Q-0001: test question [priority: high]",
            active_topics="T-0001: test topic [status: active]",
            gap_analysis="## Per-Question Gap Assessment\n### Q-0001\n- Gap: needs measurement",
            existing_directions="(none)",
        )
        assert "RESEARCH DIRECTIONS" in prompt
        assert "Test purpose" in prompt
        assert "READY" in prompt


# ---------------------------------------------------------------------------
# 7-8. parse_directions + parse_proposals
# ---------------------------------------------------------------------------

class TestParsers:
    def test_parse_directions_count(self, sample_synthesis_output: str) -> None:
        dirs = parse_directions(sample_synthesis_output)
        assert len(dirs) == 2

    def test_parse_directions_fields(self, sample_synthesis_output: str) -> None:
        dirs = parse_directions(sample_synthesis_output)
        d0 = dirs[0]
        assert d0["title"] == "Measure HBM bandwidth ceiling for 70B decode"
        fields = d0["fields"]
        assert fields["serves_question"] == "Q-0001"
        assert fields["topics"] == "T-0001"
        assert "targets_gap" in fields
        assert "reasoning_pattern" in fields
        assert "expected_evidence" in fields
        assert "seed_queries" in fields
        assert "solves_when" in fields
        assert "priority" in fields

    def test_parse_directions_second_direction(self, sample_synthesis_output: str) -> None:
        dirs = parse_directions(sample_synthesis_output)
        d1 = dirs[1]
        assert "Survey" in d1["title"]
        assert "Q-0001" in d1["fields"]["serves_question"]
        assert "medium" in d1["fields"]["priority"]

    def test_parse_proposals_count(self, sample_synthesis_output: str) -> None:
        props = parse_proposals(sample_synthesis_output)
        assert len(props) == 1

    def test_parse_proposals_fields(self, sample_synthesis_output: str) -> None:
        props = parse_proposals(sample_synthesis_output)
        p0 = props[0]
        assert "proposal" in p0
        assert "scheduling" in p0["proposal"].lower() or "tail" in p0["proposal"].lower()
        assert "rationale" in p0
        assert "from_gap" in p0

    def test_parse_directions_empty_input(self) -> None:
        dirs = parse_directions("No directions here. READY")
        assert dirs == []

    def test_parse_proposals_none(self) -> None:
        text = "## Proposed New Research Questions\n- none\n\nREADY"
        props = parse_proposals(text)
        assert props == []


# ---------------------------------------------------------------------------
# 9-14. CLI integration tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_gather_context_cli(self, temp_vault: Path) -> None:
        """CLI gather-context runs and produces non-empty output."""
        import subprocess
        py = str(_REPO_ROOT / ".venv" / "bin" / "python")
        result = subprocess.run(
            [
                py, "scripts/obj_synth_helper.py", "gather-context",
                "--text", "inference latency disaggregation",
                "--vault-root", str(temp_vault),
            ],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Should produce a wiki baseline block
        assert result.stdout.strip() != ""

    def test_format_questions_cli(self, temp_vault: Path, sample_frontier: dict, tmp_path: Path) -> None:
        """CLI format-questions with frontier JSON file."""
        import subprocess
        frontier_file = tmp_path / "frontier.json"
        frontier_file.write_text(json.dumps(sample_frontier), encoding="utf-8")
        py = str(_REPO_ROOT / ".venv" / "bin" / "python")
        result = subprocess.run(
            [
                py, "scripts/obj_synth_helper.py", "format-questions",
                "--frontier-json", str(frontier_file),
            ],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "Q-0001" in result.stdout
        assert "Q-0002" in result.stdout

    def test_fill_analysis_cli(self) -> None:
        """CLI fill-analysis with explicit args produces a non-empty prompt."""
        import subprocess
        py = str(_REPO_ROOT / ".venv" / "bin" / "python")
        result = subprocess.run(
            [
                py, "scripts/obj_synth_helper.py", "fill-analysis",
                "--purpose", "Test vault purpose",
                "--open-questions", "Q-0001: test question [priority: high]",
                "--active-topics", "T-0001: test topic [status: active]",
                "--wiki-baseline", "### [[wiki/test.md]] (score=3)\n\nTest.",
            ],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "OPEN / UNANSWERED RESEARCH QUESTIONS" in result.stdout

    def test_fill_synthesis_cli(self) -> None:
        """CLI fill-synthesis with explicit args produces a non-empty prompt."""
        import subprocess
        py = str(_REPO_ROOT / ".venv" / "bin" / "python")
        result = subprocess.run(
            [
                py, "scripts/obj_synth_helper.py", "fill-synthesis",
                "--purpose", "Test vault purpose",
                "--open-questions", "Q-0001: test question [priority: high]",
                "--active-topics", "T-0001: test topic [status: active]",
                "--gap-analysis", "## Per-Question Gap Assessment\n### Q-0001\n- Gap: test",
            ],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "RESEARCH DIRECTIONS" in result.stdout

    def test_parse_directions_cli(self, sample_synthesis_output: str, tmp_path: Path) -> None:
        """CLI parse-directions parses synthesis output file and returns JSON."""
        import subprocess
        synth_file = tmp_path / "synth_output.txt"
        synth_file.write_text(sample_synthesis_output, encoding="utf-8")
        py = str(_REPO_ROOT / ".venv" / "bin" / "python")
        result = subprocess.run(
            [
                py, "scripts/obj_synth_helper.py", "parse-directions",
                "--input-file", str(synth_file),
            ],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        parsed = json.loads(result.stdout)
        assert len(parsed) == 2
        assert parsed[0]["title"] == "Measure HBM bandwidth ceiling for 70B decode"

    def test_parse_proposals_cli(self, sample_synthesis_output: str, tmp_path: Path) -> None:
        """CLI parse-proposals parses synthesis output file and returns JSON."""
        import subprocess
        synth_file = tmp_path / "synth_output.txt"
        synth_file.write_text(sample_synthesis_output, encoding="utf-8")
        py = str(_REPO_ROOT / ".venv" / "bin" / "python")
        result = subprocess.run(
            [
                py, "scripts/obj_synth_helper.py", "parse-proposals",
                "--input-file", str(synth_file),
            ],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        parsed = json.loads(result.stdout)
        assert len(parsed) == 1
        assert "proposal" in parsed[0]
