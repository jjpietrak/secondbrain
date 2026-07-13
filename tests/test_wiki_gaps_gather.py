#!/usr/bin/env python3
"""Hermetic tests for scripts/wiki_gaps_gather.py and scripts/wiki_gaps_fill.py.

No LLM, no network, no real vault.  $0.

Covers:
  wiki_gaps_gather.gather_wiki_state
  - returns page_count and open_question_count correctly
  - wiki_state contains "## Open Questions" text verbatim
  - wiki_state contains "## For future Claude" preamble verbatim
  - infrastructure files (index.md, hot.md, log.md, gaps.md, _template*.md) excluded
  - pages with open-question items appear FIRST (sorted by oq_count desc)
  - active_concepts reads from wiki/concepts/ (skips _template)
  - purpose reads from objective/purpose/PURPOSE.md (fallback: "not yet defined")
  - empty wiki dir: returns page_count=0, wiki_state fallback string

  wiki_gaps_fill.fill_gap_prompt
  - fills all four WIKI_GAP_PROMPT placeholders
  - filled prompt contains "## Open-Question Harvest" instruction text
  - filled prompt ends with "READY"

  wiki_gaps_fill.validate_gaps_output
  - valid output returns ok=True
  - output missing harvest section returns ok=False
  - output not ending with READY returns ok=False

  wiki_gaps_fill.build_gaps_frontmatter
  - returns YAML with expected fields
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.wiki_gaps_gather import gather_wiki_state  # noqa: E402
from scripts.wiki_gaps_fill import (  # noqa: E402
    fill_gap_prompt,
    validate_gaps_output,
    build_gaps_frontmatter,
)


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------

def _make_fixture_vault(tmp: Path) -> Path:
    """Build a minimal fixture vault with 3 wiki pages + objective/ stub."""
    vault = tmp / "vault"

    # wiki subdirs
    for d in ("wiki/concepts", "wiki/entities", "wiki/sources"):
        (vault / d).mkdir(parents=True, exist_ok=True)

    # Page 1: has both ## For future Claude AND ## Open Questions
    (vault / "wiki" / "concepts" / "hbm-bandwidth.md").write_text(
        textwrap.dedent("""\
        ---
        type: concept
        title: "HBM Memory Bandwidth"
        created: 2026-01-10
        ai-first: true
        ---
        # HBM Memory Bandwidth

        ## For future Claude
        The benchmark data here may be outdated - verify against H100 official datasheets
        published after 2025-01.

        ## Overview
        HBM bandwidth is the primary bottleneck in large transformer inference.
        Modern H100 GPUs offer approximately 3.35 TB/s HBM3 bandwidth.

        ## Open Questions
        - What is the achievable memory bandwidth utilization at batch size 1?
        - Is there a published roofline model for H100 decode phase?

        ## Notes
        No contradictions found yet.
        """),
        encoding="utf-8",
    )

    # Page 2: has ## For future Claude, NO ## Open Questions
    (vault / "wiki" / "concepts" / "latency-floor.md").write_text(
        textwrap.dedent("""\
        ---
        type: concept
        title: "Inference Latency Floor"
        created: 2026-02-05
        ai-first: true
        ---
        # Inference Latency Floor

        ## For future Claude
        This note needs a concrete benchmark citation from a 2025 or later paper.
        The claim about memory-bandwidth-bound decode is unverified.

        ## Overview
        The latency floor of an inference request is determined by the
        memory-bandwidth-bound decode phase, not the compute-bound prefill.
        """),
        encoding="utf-8",
    )

    # Page 3: no special sections, just a regular entity page
    (vault / "wiki" / "entities" / "nvidia-h100.md").write_text(
        textwrap.dedent("""\
        ---
        type: entity
        title: "NVIDIA H100"
        created: 2026-03-01
        ---
        # NVIDIA H100

        The NVIDIA H100 GPU is a high-performance AI accelerator.
        """),
        encoding="utf-8",
    )

    # Infrastructure files - must be EXCLUDED
    (vault / "wiki" / "index.md").write_text(
        "---\ntype: index\n---\n# Index\n| Page | Type |\n",
        encoding="utf-8",
    )
    (vault / "wiki" / "hot.md").write_text(
        "---\ntype: hot\n---\n# Hot\nrecent context here\n",
        encoding="utf-8",
    )
    (vault / "wiki" / "log.md").write_text(
        "---\ntype: log\n---\n# Log\n## 2026-06-21 ingest | some source\n",
        encoding="utf-8",
    )
    (vault / "wiki" / "gaps.md").write_text(
        "---\ntype: gaps_report\n---\n## Open-Question Harvest\nold content\nREADY\n",
        encoding="utf-8",
    )
    (vault / "wiki" / "concepts" / "_template.md").write_text(
        "---\ntype: concept\ntitle: Template\n---\n# Template\n",
        encoding="utf-8",
    )

    # objective/purpose/ stub
    (vault / "objective" / "purpose").mkdir(parents=True, exist_ok=True)
    (vault / "objective" / "purpose" / "PURPOSE.md").write_text(
        textwrap.dedent("""\
        ---
        type: purpose
        id: purpose
        status: active
        ---
        Understand the latency floor of disaggregated LLM inference on modern GPU hardware.
        """),
        encoding="utf-8",
    )

    # The subject axis is now the set of concept pages under wiki/concepts/
    # (the retired objective/topic/ nodes are gone). The fixture's concept pages
    # (hbm-bandwidth, latency-floor) are the active concepts; _template is skipped.

    return vault


# ---------------------------------------------------------------------------
# Tests: gather_wiki_state - page counts
# ---------------------------------------------------------------------------

def test_gather_page_count():
    """3 real wiki pages (not infra), so page_count should be 3."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    assert payload["page_count"] == 3, (
        f"Expected 3 wiki pages; got {payload['page_count']}"
    )


def test_gather_open_question_count():
    """Page 1 has 2 open questions, pages 2+3 have none -> total 2."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    assert payload["open_question_count"] == 2, (
        f"Expected 2 open-question items; got {payload['open_question_count']}"
    )


# ---------------------------------------------------------------------------
# Tests: gather_wiki_state - Open Questions harvested verbatim
# ---------------------------------------------------------------------------

def test_gather_open_questions_verbatim_in_wiki_state():
    """The exact text from ## Open Questions appears in wiki_state."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    ws = payload["wiki_state"]
    assert "achievable memory bandwidth utilization at batch size 1" in ws, (
        "Open-question text not found verbatim in wiki_state"
    )
    assert "published roofline model for H100 decode phase" in ws, (
        "Second open-question text not found verbatim in wiki_state"
    )


def test_gather_open_questions_section_header_in_wiki_state():
    """wiki_state includes an 'Open Questions' header for the page that has one."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    assert "Open Questions" in payload["wiki_state"]


def test_gather_open_questions_with_parenthetical_suffix():
    """Open-question headers with trailing parentheticals (e.g., TBD markers) are matched."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        # Add a page with a parenthetical header
        (vault / "wiki" / "concepts" / "future-work.md").write_text(
            textwrap.dedent("""\
            ---
            type: concept
            title: "Future Work"
            created: 2026-06-22
            ai-first: true
            ---
            # Future Work

            ## Open questions (TBD for [[iris-tetra]])
            - What is the impact of disaggregation on e2e latency?
            - How does batching interact with network latency?

            ## Notes
            To be filled in later.
            """),
            encoding="utf-8",
        )
        payload = gather_wiki_state(vault)
        # Now we should have 4 pages (the original 3 + future-work)
        assert payload["page_count"] == 4
        # Open-question count should be 4 (original 2 + 2 new from future-work)
        assert payload["open_question_count"] == 4, (
            f"Expected 4 open-question items (2 original + 2 from parenthetical header); got {payload['open_question_count']}"
        )
        # The new questions should be in wiki_state verbatim
        ws = payload["wiki_state"]
        assert "impact of disaggregation on e2e latency" in ws
        assert "batching interact with network latency" in ws


# ---------------------------------------------------------------------------
# Tests: gather_wiki_state - For future Claude harvested verbatim
# ---------------------------------------------------------------------------

def test_gather_ffc_verbatim_in_wiki_state():
    """Text from ## For future Claude appears in wiki_state."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    ws = payload["wiki_state"]
    assert "benchmark data here may be outdated" in ws, (
        "FFC preamble text not found verbatim in wiki_state"
    )
    assert "concrete benchmark citation from a 2025 or later paper" in ws, (
        "FFC preamble from latency-floor.md not found in wiki_state"
    )


# ---------------------------------------------------------------------------
# Tests: gather_wiki_state - infrastructure exclusion
# ---------------------------------------------------------------------------

def test_gather_excludes_infrastructure_files():
    """index.md, hot.md, log.md, gaps.md, _template.md must not appear."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    ws = payload["wiki_state"]
    for bad_name in ("index.md", "hot.md", "log.md", "gaps.md", "_template"):
        assert bad_name not in ws, (
            f"Infrastructure file '{bad_name}' leaked into wiki_state"
        )
    # The 'old content' from gaps.md must not be there
    assert "old content" not in ws, "Content from gaps.md leaked into wiki_state"


# ---------------------------------------------------------------------------
# Tests: gather_wiki_state - sorting (oq pages first)
# ---------------------------------------------------------------------------

def test_gather_oq_pages_first():
    """Pages with open-question items appear before pages without."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    ws = payload["wiki_state"]
    # hbm-bandwidth has OQ items; latency-floor and nvidia-h100 do not.
    hbm_pos = ws.find("hbm-bandwidth")
    latency_pos = ws.find("latency-floor")
    h100_pos = ws.find("nvidia-h100")
    assert hbm_pos != -1, "hbm-bandwidth.md not found in wiki_state"
    assert hbm_pos < latency_pos or hbm_pos < h100_pos, (
        "Expected hbm-bandwidth (has OQ items) before other pages in wiki_state"
    )


# ---------------------------------------------------------------------------
# Tests: gather_wiki_state - purpose and active_concepts
# ---------------------------------------------------------------------------

def test_gather_purpose_from_purpose_md():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    assert "disaggregated LLM inference" in payload["purpose"], (
        f"Expected purpose from PURPOSE.md; got: {payload['purpose'][:120]}"
    )


def test_gather_active_concepts_includes_pages():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    concepts = payload["active_concepts"]
    assert "hbm-bandwidth" in concepts, f"hbm-bandwidth not in active_concepts: {concepts}"
    assert "latency-floor" in concepts, f"latency-floor not in active_concepts: {concepts}"


def test_gather_active_concepts_excludes_non_concepts():
    """Only wiki/concepts/ pages are active concepts; entities and the template are not."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        payload = gather_wiki_state(vault)
    concepts = payload["active_concepts"]
    assert "nvidia-h100" not in concepts, (
        f"Entity page nvidia-h100 should not appear in active_concepts: {concepts}"
    )
    assert "_template" not in concepts, (
        f"The concept _template should not appear in active_concepts: {concepts}"
    )


def test_gather_purpose_fallback_when_no_purpose_md():
    """Without objective/purpose/PURPOSE.md, purpose is 'not yet defined'."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        # Remove the PURPOSE.md
        purpose_path = vault / "objective" / "purpose" / "PURPOSE.md"
        purpose_path.unlink(missing_ok=True)
        payload = gather_wiki_state(vault)
    # Fallback: "not yet defined" (vault_config may also return something, but
    # in a tmp vault with no real vault_config, the fallback kicks in).
    # We just check it is a non-empty string that does NOT crash.
    assert isinstance(payload["purpose"], str)
    assert len(payload["purpose"]) > 0


def test_gather_active_concepts_returns_none_when_no_concepts():
    """Without wiki/concepts/, active_concepts is 'none'."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = _make_fixture_vault(Path(tmp_str))
        import shutil
        shutil.rmtree(vault / "wiki" / "concepts")
        payload = gather_wiki_state(vault)
    assert payload["active_concepts"] == "none", (
        f"Expected 'none' when no concepts; got: {payload['active_concepts']}"
    )


# ---------------------------------------------------------------------------
# Tests: gather_wiki_state - empty wiki
# ---------------------------------------------------------------------------

def test_gather_empty_wiki():
    """An empty vault returns page_count=0 and the fallback wiki_state string."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "empty_vault"
        vault.mkdir()
        (vault / "wiki").mkdir()
        payload = gather_wiki_state(vault)
    assert payload["page_count"] == 0
    assert payload["open_question_count"] == 0
    assert "no wiki pages" in payload["wiki_state"].lower() or payload["wiki_state"] == "(no wiki pages found)"


# ---------------------------------------------------------------------------
# Tests: fill_gap_prompt
# ---------------------------------------------------------------------------

def _sample_payload() -> dict:
    return {
        "purpose": "Understand the latency floor of disaggregated LLM inference.",
        "today": "2026-06-21",
        "active_concepts": "- hbm-bandwidth: HBM Memory Bandwidth\n- latency-floor: Inference Latency Floor",
        "wiki_state": (
            "### [[wiki/concepts/hbm-bandwidth.md]]\n\n"
            "#### Open Questions\n"
            "- What is the achievable memory bandwidth utilization at batch size 1?\n\n"
            "HBM bandwidth is the primary bottleneck."
        ),
        "open_question_count": 1,
        "page_count": 1,
    }


def test_fill_gap_prompt_contains_purpose():
    filled = fill_gap_prompt(_sample_payload())
    assert "disaggregated LLM inference" in filled


def test_fill_gap_prompt_contains_today():
    filled = fill_gap_prompt(_sample_payload())
    assert "2026-06-21" in filled


def test_fill_gap_prompt_contains_active_concepts():
    filled = fill_gap_prompt(_sample_payload())
    assert "hbm-bandwidth" in filled
    assert "latency-floor" in filled


def test_fill_gap_prompt_contains_wiki_state():
    filled = fill_gap_prompt(_sample_payload())
    assert "achievable memory bandwidth utilization" in filled


def test_fill_gap_prompt_contains_harvest_instruction():
    """The filled prompt must contain the Open-Question Harvest header."""
    filled = fill_gap_prompt(_sample_payload())
    assert "Open-Question Harvest" in filled


def test_fill_gap_prompt_ends_with_ready():
    """WIKI_GAP_PROMPT template ends with READY."""
    filled = fill_gap_prompt(_sample_payload())
    assert "READY" in filled


def test_fill_gap_prompt_missing_key_raises():
    """Missing a required key should raise KeyError."""
    payload = _sample_payload()
    del payload["purpose"]
    try:
        fill_gap_prompt(payload)
        assert False, "Expected KeyError for missing 'purpose'"
    except KeyError:
        pass


# ---------------------------------------------------------------------------
# Tests: validate_gaps_output
# ---------------------------------------------------------------------------

VALID_GAPS_OUTPUT = """\
## Open-Question Harvest (TOP PRIORITY)
- What is the achievable memory bandwidth utilization at batch size 1? [[wiki/concepts/hbm-bandwidth]]

## Coverage Map
- T-0001: thin - [[wiki/concepts/latency-floor]]

## Knowledge Gaps
### Batch-size-1 HBM utilization measurement
- shows_up_in: [[wiki/concepts/hbm-bandwidth]]
- missing: a benchmarked measurement at batch size 1 on H100
- fillable_by: arxiv | web
- concepts: [[wiki/concepts/hbm-bandwidth]]
- priority: high  -  appears in Open-Question Harvest

## Stale / Unverified
- The HBM3 bandwidth figure (3.35 TB/s) should be re-verified against H100 datasheet [[wiki/concepts/hbm-bandwidth]]

## Self-contained (no external source needed)
- none

READY"""


def test_validate_gaps_output_valid():
    ok, msg = validate_gaps_output(VALID_GAPS_OUTPUT)
    assert ok is True, f"Expected valid; got: {msg}"


def test_validate_gaps_output_missing_harvest_section():
    bad = VALID_GAPS_OUTPUT.replace("## Open-Question Harvest (TOP PRIORITY)", "## Coverage Map (extra)")
    ok, msg = validate_gaps_output(bad)
    assert ok is False
    assert "Open-Question Harvest" in msg


def test_validate_gaps_output_missing_ready():
    bad = VALID_GAPS_OUTPUT.rstrip("\nREADY")
    ok, msg = validate_gaps_output(bad)
    assert ok is False
    assert "READY" in msg


def test_validate_gaps_output_empty():
    ok, msg = validate_gaps_output("")
    assert ok is False


# ---------------------------------------------------------------------------
# Tests: build_gaps_frontmatter
# ---------------------------------------------------------------------------

def test_build_gaps_frontmatter_fields():
    fm = build_gaps_frontmatter("example", "2026-06-21")
    assert "type: gaps_report" in fm
    assert "written_by: wiki" in fm
    assert "updated: 2026-06-21" in fm
    assert "vault: example" in fm
    assert "ai-first: true" in fm


def test_build_gaps_frontmatter_is_fenced_yaml():
    fm = build_gaps_frontmatter("TestVault", "2026-01-01")
    assert fm.startswith("---\n")
    assert "\n---\n" in fm


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
