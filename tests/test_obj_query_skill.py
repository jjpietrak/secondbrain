#!/usr/bin/env python3
"""Hermetic tests for the obj-query skill data model and procedure assumptions.

The obj-query skill is a SKILL.md (no helper Python script). These tests verify
the underlying data layer (agents/objectives.py) satisfies every behavioral
assumption the skill's Step 2-5 procedure makes:

  - Frontier returns research_questions and directions in priority order.
  - "Answerable now" detection: an open Q with a matching research/ file is surfaced.
  - Decisions are readable and scope-filtered (Step 0a).
  - Topic filtering: questions tagged to T-NNNN can be isolated from the frontier.
  - Direction filtering by serves_question: directions for Q-NNNN are isolatable.
  - Purpose file is readable at objective/purpose/PURPOSE.md.
  - Missing objective/ scaffold exits gracefully (Step 3 fallback).
  - Optional filing produces correct frontmatter (Step 6).

No network, no live vault. All writes happen under a tempdir.

Run:  .venv/bin/python -m pytest tests/test_obj_query_skill.py -v
 or:  .venv/bin/python tests/test_obj_query_skill.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(os.environ.get("CODE_PATH") or Path(__file__).resolve().parent.parent)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Module loader (mirrors test_objectives.py pattern)
# ---------------------------------------------------------------------------

def _load(modname: str, relpath: str):
    spec = importlib.util.spec_from_file_location(modname, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


objectives = _load("objectives", "agents/objectives.py")
obj_init = _load("obj_init", "scripts/obj_init.py")


# ---------------------------------------------------------------------------
# Fixture builder: mock vault for obj-query tests
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_query_fixture(vault: Path) -> None:
    """Build a mock vault with nodes that cover all obj-query query types.

    Nodes:
      Q-0001 (high, unsolved, T-0001) - open question about optical accelerators
      Q-0002 (medium, unsolved, T-0001) - open question about bandwidth
      Q-0003 (low, solved, T-0002)    - solved question (must NOT appear in frontier)
      DIR-0001 (high, open, serves Q-0001) - direction for Q-0001
      DIR-0002 (medium, open, serves Q-0001) - direction for Q-0001
      DIR-0003 (low, open, serves Q-0002)  - direction for Q-0002
      D-0001 (active, scope=all)      - no-web decision
      D-0002 (active, scope=research) - no-wiki-writes decision
      purpose/PURPOSE.md              - vault purpose statement
      research/Q-0003.md              - existing answer for Q-0003 (solved)
    """
    obj_init.run_apply(vault)

    # --- purpose ---
    _write(vault / "objective/purpose/PURPOSE.md", """\
---
type: purpose
id: purpose
created: 2026-01-01
updated: 2026-06-01
status: active
vault: test-vault
---

Research the inference disaggregation problem: memory bandwidth, latency floors,
and optical accelerator viability for disaggregated LLM serving.
""")

    # --- research_question nodes ---
    _write(vault / "objective/research_question/Q-0001-optical-accelerators.md", """\
---
type: research_question
id: Q-0001
created: 2026-01-01
updated: 2026-06-01
solved: "no"
topic: T-0001
priority: high
answer_ref: ""
---

Can optical accelerators replace HBM for KV-cache transfer in disaggregated inference?
""")

    _write(vault / "objective/research_question/Q-0002-bandwidth-wall.md", """\
---
type: research_question
id: Q-0002
created: 2026-02-01
updated: 2026-06-01
solved: "no"
topic: T-0001
priority: medium
answer_ref: ""
---

Does the memory bandwidth wall prevent disaggregation beyond 4 nodes?
""")

    _write(vault / "objective/research_question/Q-0003-hbm-cost.md", """\
---
type: research_question
id: Q-0003
created: 2026-01-15
updated: 2026-06-15
solved: "yes"
topic: T-0002
priority: low
answer_ref: "research/Q-0003.md"
---

What is the per-byte cost of HBM3e vs DRAM for inference workloads?
""")

    # --- direction nodes ---
    _write(vault / "objective/direction/DIR-0001-photonic-interconnect.md", """\
---
type: direction
id: DIR-0001
created: 2026-02-01
updated: 2026-06-01
generated_by: research
serves_question: Q-0001
topics: [T-0001]
targets_gap: optical-interconnect
priority: high
status: open
---

## reasoning_pattern
Survey photonic interconnect literature for latency benchmarks.

## expected_evidence
Latency < 1us for 512GB transfer shown experimentally.

## seed_queries
wiki/concepts/photonics, wiki/sources/optical-paper

## solves_when
Can state whether optical links achieve HBM-class bandwidth at rack scale.
""")

    _write(vault / "objective/direction/DIR-0002-silicon-photonics.md", """\
---
type: direction
id: DIR-0002
created: 2026-03-01
updated: 2026-06-01
generated_by: research
serves_question: Q-0001
topics: [T-0001]
targets_gap: silicon-photonics-maturity
priority: medium
status: open
---

## reasoning_pattern
Assess silicon photonics manufacturing readiness.

## expected_evidence
Yield rate > 90% demonstrated at wafer scale.

## seed_queries
wiki/concepts/silicon-photonics

## solves_when
Manufacturing TRL >= 6 confirmed.
""")

    _write(vault / "objective/direction/DIR-0003-bandwidth-model.md", """\
---
type: direction
id: DIR-0003
created: 2026-04-01
updated: 2026-06-01
generated_by: research
serves_question: Q-0002
topics: [T-0001]
targets_gap: bandwidth-model
priority: low
status: open
---

## reasoning_pattern
Build analytic model of bandwidth requirements for N-node disaggregation.

## expected_evidence
Formula for required bandwidth as function of model size and batch size.

## seed_queries
wiki/concepts/bandwidth

## solves_when
Model predicts bandwidth requirement within 10% of measured.
""")

    # --- decision nodes ---
    _write(vault / "objective/decision/D-0001-no-web.md", """\
---
type: decision
id: D-0001
created: 2026-01-10
updated: 2026-01-10
status: active
scope: all
---

Do not fetch web content from any agent (no WebSearch/WebFetch).
""")

    _write(vault / "objective/decision/D-0002-no-wiki-writes.md", """\
---
type: decision
id: D-0002
created: 2026-01-10
updated: 2026-01-10
status: active
scope: research
---

The research agent must never write to wiki/ directly.
""")

    # --- existing research answer for Q-0003 (solved) ---
    _write(vault / "research/Q-0003.md", """\
---
type: question_answer
id: Q-0003
solved_on: 2026-06-15
generated_by: research
---

HBM3e costs approximately $8/GB at volume vs $0.10/GB for DRAM.
Sources: [[wiki/sources/hbm-cost-analysis]].
""")

    # --- wiki pages (minimal, for retrieval fallback tests) ---
    _write(vault / "wiki/hot.md", """\
---
type: hot
updated: 2026-06-21
---

## For future Claude
Current focus: optical interconnects and disaggregation scaling.

Recent: ingested photonics paper, updated bandwidth model.

Open: Q-0001 and Q-0002 need synthesis pass.
""")

    _write(vault / "wiki/index.md", """\
---
type: index
updated: 2026-06-21
ai-first: true
---

## For future Claude
This is the wiki index.

| Id | Title | Type | Summary |
|----|-------|------|---------|
| c-0001 | Photonics Overview | concept | Overview of photonic interconnects for compute. |
| c-0002 | Bandwidth Model | concept | Analytic model of memory bandwidth requirements. |
| c-0003 | HBM Cost Analysis | source | Per-byte cost comparison for HBM3e vs DRAM. |
""")

    _write(vault / "wiki/concepts/Photonics-Overview.md", """\
---
type: concept
id: c-0001
created: 2026-03-01
updated: 2026-06-01
ai-first: true
---

## For future Claude
Core concept: photonic interconnects for disaggregated compute.

## Summary
Photonic interconnects offer sub-microsecond latency at 800Gbps bandwidth.
Relevant to [[wiki/concepts/Bandwidth-Model]] and [[wiki/sources/HBM-Cost-Analysis]].

## Open Questions
- Can silicon photonics reach HBM-class bandwidth cost-effectively?
- What is the latency floor for rack-scale optical links?
""")


# ---------------------------------------------------------------------------
# Test: Step 0a - read_decisions returns active decisions in scope order
# (research agent must read these as hard constraints before any action)
# ---------------------------------------------------------------------------

def test_step0a_decisions_readable_scope_ordered() -> None:
    """The skill's Step 0a reads decisions via `objectives decisions --json`.

    Asserts:
    - Returns only active decisions.
    - scope=all comes before scope=research (binding for the research agent).
    - D-0001 body contains the no-web constraint text.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        decisions = objectives.read_decisions(vault)

        ids = [d["id"] for d in decisions]
        assert "D-0001" in ids, "D-0001 (active, all) must be readable"
        assert "D-0002" in ids, "D-0002 (active, research) must be readable"

        # scope=all before scope=research (SCOPE_RANK ordering)
        assert ids.index("D-0001") < ids.index("D-0002"), (
            "scope=all decisions must precede scope=research decisions"
        )

        # Body is accessible and contains constraint text
        d1 = next(d for d in decisions if d["id"] == "D-0001")
        assert "web" in d1["body"].lower() or "WebSearch" in d1["body"], (
            "D-0001 body should contain the no-web constraint"
        )

    print("PASS test_step0a_decisions_readable_scope_ordered")


# ---------------------------------------------------------------------------
# Test: Step 2 + Step 3 - frontier dump for full frontier query
# ---------------------------------------------------------------------------

def test_step3_full_frontier_dump() -> None:
    """The skill's Step 3 reads the full frontier payload.

    Asserts:
    - Open research_questions: Q-0001 (high, unsolved), Q-0002 (medium, unsolved).
    - Solved Q-0003 is excluded.
    - Open directions: DIR-0001, DIR-0002, DIR-0003 all present.
    - combined_ranked preserves high > medium > low ordering.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        result = objectives.open_frontier(vault)

        rqs = result["research_questions"]
        dirs = result["directions"]
        combined = result["combined_ranked"]

        rq_ids = {n["id"] for n in rqs}
        assert "Q-0001" in rq_ids, "Q-0001 (unsolved high) must be in frontier"
        assert "Q-0002" in rq_ids, "Q-0002 (unsolved medium) must be in frontier"
        assert "Q-0003" not in rq_ids, "Q-0003 (solved) must NOT be in frontier"

        dir_ids = {n["id"] for n in dirs}
        assert "DIR-0001" in dir_ids
        assert "DIR-0002" in dir_ids
        assert "DIR-0003" in dir_ids

        combined_ids = [n["id"] for n in combined]
        # Q-0001 (high) ranks before Q-0002 (medium) and DIR-0001 (high)
        # At same priority: Q before DIR, then older created first.
        # Q-0001 high (created 2026-01-01) -> rank 0
        # DIR-0001 high (created 2026-02-01) -> rank 1 (same pri, DIR after Q)
        idx_q1 = combined_ids.index("Q-0001")
        idx_q2 = combined_ids.index("Q-0002")
        idx_dir1 = combined_ids.index("DIR-0001")
        idx_dir3 = combined_ids.index("DIR-0003")

        assert idx_q1 < idx_q2, "Q-0001 (high) must precede Q-0002 (medium)"
        assert idx_q1 < idx_dir1, "Q-0001 (high) must precede DIR-0001 (high, Q before DIR)"
        assert idx_dir1 < idx_dir3, "DIR-0001 (high) must precede DIR-0003 (low)"

    print("PASS test_step3_full_frontier_dump")


# ---------------------------------------------------------------------------
# Test: Step 2 - topic filter (open questions for T-0001)
# ---------------------------------------------------------------------------

def test_step2_topic_filter() -> None:
    """The skill applies a topic filter for 'what open questions touch T-0001'.

    The skill uses the frontier output and filters by the `topic` field.
    Asserts:
    - Q-0001 and Q-0002 both have topic=T-0001 and appear in the frontier.
    - Q-0003 (topic=T-0002) does not appear (also solved).
    - All T-0001 directions are present in the frontier.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        result = objectives.open_frontier(vault)
        rqs = result["research_questions"]

        t0001_questions = [n for n in rqs if n.get("topic") == "T-0001"]
        ids = {n["id"] for n in t0001_questions}
        assert "Q-0001" in ids, "Q-0001 (topic T-0001) should survive T-0001 filter"
        assert "Q-0002" in ids, "Q-0002 (topic T-0001) should survive T-0001 filter"

        # T-0002 question Q-0003 is solved so not in frontier anyway, but
        # also check no T-0002 leaks in.
        assert all(n.get("topic") != "T-0002" for n in rqs), (
            "T-0002 questions should not appear in the frontier (Q-0003 is solved)"
        )

    print("PASS test_step2_topic_filter")


# ---------------------------------------------------------------------------
# Test: Step 2 - direction filter by serves_question
# ---------------------------------------------------------------------------

def test_step2_direction_filter_by_question() -> None:
    """The skill filters directions by serves_question for 'directions open for Q-0001'.

    The skill reads direction nodes from the frontier and applies a serves_question
    filter. Asserts:
    - DIR-0001 and DIR-0002 serve Q-0001.
    - DIR-0003 serves Q-0002 and must NOT appear in a Q-0001 filter.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        result = objectives.open_frontier(vault)
        dirs = result["directions"]

        q0001_dirs = [n for n in dirs if n.get("serves_question") == "Q-0001"]
        ids = {n["id"] for n in q0001_dirs}
        assert "DIR-0001" in ids, "DIR-0001 serves Q-0001 and should be in Q-0001 filter"
        assert "DIR-0002" in ids, "DIR-0002 serves Q-0001 and should be in Q-0001 filter"
        assert "DIR-0003" not in ids, "DIR-0003 serves Q-0002 and must NOT be in Q-0001 filter"

    print("PASS test_step2_direction_filter_by_question")


# ---------------------------------------------------------------------------
# Test: Step 4 - "answerable now" detection via research/ file scan
# ---------------------------------------------------------------------------

def test_step4_answerable_now_detection() -> None:
    """The skill checks for existing research/Q-NNNN.md files to detect answerable Qs.

    The skill's Step 4 scans research/ for existing answer files matching open
    questions. An open Q with a matching file is surfaced for question-solve.

    Asserts:
    - Q-0003 (solved) has research/Q-0003.md and can be found by file scan.
    - Q-0001 and Q-0002 (open, no answer file) are not falsely marked answerable.
    - The skill can cross-reference frontier with existing research/ files.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        # Simulate the Step 4 research/ scan the skill performs:
        # list research/*.md files matching Q-NNNN pattern
        research_dir = vault / "research"
        answer_files = {
            p.stem for p in research_dir.glob("*.md")
            if p.stem.startswith("Q-")
        }
        assert "Q-0003" in answer_files, "Q-0003.md must be detectable in research/"

        # Cross-reference: open frontier questions that have answer files
        result = objectives.open_frontier(vault)
        open_rq_ids = {n["id"] for n in result["research_questions"]}

        # Q-0003 is solved so not in the frontier; no open questions have answer files
        answered_open = open_rq_ids & answer_files
        assert len(answered_open) == 0, (
            f"No open questions should have answer files in this fixture; got {answered_open}"
        )

        # Now add an answer file for Q-0001 (simulates deep-synthesis --question Q-0001)
        _write(vault / "research/Q-0001.md", """\
---
type: question_answer
id: Q-0001
solved_on: 2026-06-21
generated_by: research
---

Optical accelerators can in principle replace HBM for KV-cache transfer.
Sources: [[wiki/concepts/Photonics-Overview]].
""")

        # Re-scan
        answer_files_2 = {
            p.stem for p in research_dir.glob("*.md")
            if p.stem.startswith("Q-")
        }
        answered_open_2 = open_rq_ids & answer_files_2
        assert "Q-0001" in answered_open_2, (
            "Q-0001 should be detected as answerable now (research/Q-0001.md exists)"
        )
        assert "Q-0002" not in answered_open_2, (
            "Q-0002 has no answer file and must not be marked answerable"
        )

    print("PASS test_step4_answerable_now_detection")


# ---------------------------------------------------------------------------
# Test: Step 3 - purpose file is readable
# ---------------------------------------------------------------------------

def test_step3_purpose_readable() -> None:
    """The skill reads objective/purpose/PURPOSE.md for purpose queries.

    Asserts:
    - The purpose file exists and contains the vault mission text.
    - The vault PURPOSE is loadable for use as a relevance filter.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        purpose_path = vault / "objective" / "purpose" / "PURPOSE.md"
        assert purpose_path.exists(), "objective/purpose/PURPOSE.md must exist"
        text = purpose_path.read_text(encoding="utf-8")
        assert "inference disaggregation" in text.lower(), (
            "PURPOSE.md body should contain the vault mission"
        )
        assert "type: purpose" in text, "PURPOSE.md must have type: purpose frontmatter"

    print("PASS test_step3_purpose_readable")


# ---------------------------------------------------------------------------
# Test: Step 3 - missing objective/ scaffold exits gracefully
# ---------------------------------------------------------------------------

def test_step3_missing_scaffold_graceful() -> None:
    """The skill requires objective/ to be scaffolded; if absent, exits gracefully.

    scan_objectives() returns an empty list when objective/ does not exist.
    open_frontier() returns empty lists, so the skill can detect and report.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        # No scaffold applied - objective/ directory does not exist.

        nodes = objectives.scan_objectives(vault)
        assert nodes == [], "scan_objectives should return [] when objective/ is absent"

        result = objectives.open_frontier(vault)
        assert result["research_questions"] == [], (
            "open_frontier should return empty research_questions when no objective/"
        )
        assert result["directions"] == [], (
            "open_frontier should return empty directions when no objective/"
        )

    print("PASS test_step3_missing_scaffold_graceful")


# ---------------------------------------------------------------------------
# Test: Step 6 - filing produces correct frontmatter
# ---------------------------------------------------------------------------

def test_step6_filing_frontmatter() -> None:
    """The skill's optional Step 6 files to research/query/YYYY-MM-DD-<slug>.md.

    Asserts:
    - Written file has correct frontmatter: type, generated_by, query, created.
    - The file body preserves the answer text.
    - Writing to research/query/ does not require a lock on objective/ files.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        # Simulate the Step 6 filing action the skill performs
        query_text = "What open high-priority questions touch T-0001?"
        answer_body = (
            "## Objective Query: open high-priority questions touching T-0001\n\n"
            "**Frontier snapshot** (as of 2026-06-21):\n"
            "- Open research questions: 2 total (1 high, 1 medium)\n"
            "- Open directions: 3 total\n\n"
            "Q-0001 (high): optical accelerators question\n"
            "Cite: [[objective/research_question/Q-0001-optical-accelerators]]\n"
        )

        import datetime
        today = datetime.date.today().isoformat()
        slug = "open-high-priority-t0001"
        target_rel = f"research/query/{today}-{slug}.md"
        target_path = vault / target_rel

        content = (
            "---\n"
            "type: query_record\n"
            "generated_by: research\n"
            f'query: "{query_text}"\n'
            f"created: {today}\n"
            "---\n\n"
            + answer_body
        )

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")

        assert target_path.exists(), "Filed query record must exist"
        written = target_path.read_text(encoding="utf-8")

        assert "type: query_record" in written, "query record must have type: query_record"
        assert "generated_by: research" in written, "must carry generated_by: research"
        assert query_text in written, "query text must be in the filed record"
        assert "Q-0001" in written, "answer body with citations must be preserved"
        assert "[[objective/research_question/Q-0001-optical-accelerators]]" in written, (
            "wikilink citations must be preserved in filed record"
        )

        # Verify filing did NOT touch any objective/ files (read-only on objective/)
        index_path = vault / "objective" / "index.md"
        if index_path.exists():
            index_text = index_path.read_text(encoding="utf-8")
            assert "query_record" not in index_text, (
                "obj-query filing must not append to objective/index.md"
            )

    print("PASS test_step6_filing_frontmatter")


# ---------------------------------------------------------------------------
# Test: frontier CLI verb produces JSON usable by skill Step 3
# ---------------------------------------------------------------------------

def test_frontier_cli_json_for_skill_step3() -> None:
    """The skill runs `python -m agents.objectives frontier --json` in Step 3.

    Asserts the JSON schema the skill consumes is stable:
    - Top-level keys: research_questions, directions, combined_ranked.
    - Each item has: id, type, priority, solved/status, path, body.
    - Combined ranked list preserves high > medium > low ordering.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        buf = StringIO()
        with redirect_stdout(buf):
            rc = objectives.main(["--vault-root", str(vault), "--json", "frontier"])
        assert rc == 0, f"frontier CLI returned non-zero: {rc}"

        data = json.loads(buf.getvalue())

        # Schema check: top-level keys present
        assert "research_questions" in data
        assert "directions" in data
        assert "combined_ranked" in data

        # Each item in research_questions must have fields the skill reads
        for rq in data["research_questions"]:
            assert "id" in rq, f"research_question missing id: {rq}"
            assert "priority" in rq, f"research_question missing priority: {rq}"
            assert "solved" in rq, f"research_question missing solved: {rq}"
            assert "path" in rq, f"research_question missing path: {rq}"
            assert rq["solved"] != "yes", (
                f"solved question in frontier: {rq['id']}"
            )

        # Each item in directions must have fields the skill reads
        for d in data["directions"]:
            assert "id" in d, f"direction missing id: {d}"
            assert "serves_question" in d, f"direction missing serves_question: {d}"
            assert "status" in d, f"direction missing status: {d}"
            assert d["status"] == "open", (
                f"non-open direction in frontier: {d['id']} status={d['status']}"
            )

        # Priority ordering in combined_ranked: high before medium before low
        combined = data["combined_ranked"]
        combined_ids = [n["id"] for n in combined]
        # Q-0001 high should come before Q-0002 medium
        assert combined_ids.index("Q-0001") < combined_ids.index("Q-0002"), (
            "Q-0001 (high) must precede Q-0002 (medium) in combined_ranked"
        )

    print("PASS test_frontier_cli_json_for_skill_step3")


# ---------------------------------------------------------------------------
# Test: decisions CLI JSON matches what skill Step 0a expects
# ---------------------------------------------------------------------------

def test_decisions_cli_json_for_skill_step0a() -> None:
    """The skill runs `python -m agents.objectives decisions --json` in Step 0a.

    Asserts the JSON schema the skill parses for hard constraints:
    - Returns a list of dicts with id, scope, status, body.
    - scope=all decisions appear first (binding for all agents).
    - Archived decisions excluded.
    """
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_query_fixture(vault)

        buf = StringIO()
        with redirect_stdout(buf):
            rc = objectives.main(["--vault-root", str(vault), "--json", "decisions"])
        assert rc == 0

        data = json.loads(buf.getvalue())
        assert isinstance(data, list), "decisions --json must return a list"

        # Each decision dict has required fields
        for dec in data:
            assert "id" in dec
            assert "scope" in dec
            assert "body" in dec

        # scope=all first (D-0001)
        ids = [d["id"] for d in data]
        assert ids[0] == "D-0001", f"D-0001 (scope=all) should be first; got {ids}"

        # No archived decisions
        assert all(d.get("status") != "archived" for d in data), (
            "archived decisions must not appear in decisions output"
        )

    print("PASS test_decisions_cli_json_for_skill_step0a")


# ---------------------------------------------------------------------------
# Self-running fallback
# ---------------------------------------------------------------------------

def _run_all() -> int:
    test_step0a_decisions_readable_scope_ordered()
    test_step3_full_frontier_dump()
    test_step2_topic_filter()
    test_step2_direction_filter_by_question()
    test_step4_answerable_now_detection()
    test_step3_purpose_readable()
    test_step3_missing_scaffold_graceful()
    test_step6_filing_frontmatter()
    test_frontier_cli_json_for_skill_step3()
    test_decisions_cli_json_for_skill_step0a()
    print("\nAll obj-query skill tests PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
