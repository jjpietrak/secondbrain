#!/usr/bin/env python3
"""Hermetic tests for agents/objectives.py.

No network, no live vault. Builds a fixture objective/ tree under mktemp.

Run:  .venv/bin/python -m pytest tests/test_objectives.py -v
 or:  .venv/bin/python tests/test_objectives.py
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


def _load(modname: str, relpath: str):
    spec = importlib.util.spec_from_file_location(modname, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


objectives = _load("objectives", "agents/objectives.py")
obj_init = _load("obj_init", "scripts/obj_init.py")


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_fixture(vault: Path) -> None:
    """Create a minimal objective/ tree for testing."""

    # Scaffold the base structure first (creates folders + index.md with next_id counters).
    obj_init.run_apply(vault)

    # --- research_question nodes ---
    _write(vault / "objective/research_question/Q-0001-latency-floor.md", """\
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

What is the theoretical latency floor for disaggregated inference?
""")

    _write(vault / "objective/research_question/Q-0002-hbm-bandwidth.md", """\
---
type: research_question
id: Q-0002
created: 2026-02-01
updated: 2026-06-10
solved: "yes"
topic: T-0001
priority: medium
answer_ref: "research/Q-0002.md"
---

Does HBM bandwidth dominate KV-cache transfer latency?
""")

    # --- direction nodes ---
    _write(vault / "objective/direction/DIR-0001-measure-hbm.md", """\
---
type: direction
id: DIR-0001
created: 2026-02-10
updated: 2026-06-01
generated_by: research
serves_question: Q-0001
topics: [T-0001]
targets_gap: latency-floor
priority: high
status: open
---

## reasoning_pattern
Measure HBM bandwidth vs SRAM transfer cost.

## expected_evidence
Benchmark showing latency breakdown.

## seed_queries
wiki/concepts/hbm, wiki/sources/bandwidth-paper

## solves_when
Can state minimum transfer latency for a given model size.
""")

    _write(vault / "objective/direction/DIR-0002-network-overhead.md", """\
---
type: direction
id: DIR-0002
created: 2026-03-01
updated: 2026-06-01
generated_by: research
serves_question: Q-0001
topics: [T-0001]
targets_gap: network-latency
priority: medium
status: open
---

## reasoning_pattern
Analyze RDMA vs TCP for KV transfer.

## expected_evidence
Latency comparison table.

## seed_queries
wiki/concepts/rdma

## solves_when
Network overhead < 10% of compute time shown.
""")

    _write(vault / "objective/direction/DIR-0003-old-superseded.md", """\
---
type: direction
id: DIR-0003
created: 2026-01-05
updated: 2026-04-01
generated_by: research
serves_question: Q-0002
topics: [T-0001]
targets_gap: hbm-vs-sram
priority: low
status: crawled
---

## reasoning_pattern
Old direction; already explored.

## expected_evidence
Done.

## seed_queries
-

## solves_when
Marked crawled.
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

Do not fetch web content from the research agent (no WebSearch/WebFetch).
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

    _write(vault / "objective/decision/D-0003-archived.md", """\
---
type: decision
id: D-0003
created: 2026-01-10
updated: 2026-05-01
status: archived
scope: all
---

Old decision, now archived.
""")

    # --- research_question_proposal ---
    _write(vault / "objective/research_question_proposal/QP-0001-memory-bw.md", """\
---
type: research_question_proposal
id: QP-0001
created: 2026-05-01
updated: 2026-05-01
generated_by: research
from_gap: memory-bandwidth-wall
status: pending
---

Does memory bandwidth wall limit disaggregation scaling?
""")

    # --- template files (must be skipped by scan) ---
    # These already exist from obj_init; confirm they are present.
    assert (vault / "objective/direction/_template.md").exists(), (
        "direction _template.md should exist after obj_init"
    )


# ---------------------------------------------------------------------------
# Test: scan_objectives parses all node types/ids/fields correctly
# ---------------------------------------------------------------------------

def test_scan_parses_nodes() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        nodes = objectives.scan_objectives(vault)

        # Should parse: 2 research_question + 3 direction + 3 decision + 1 proposal = 9 nodes.
        # _template.md files must NOT appear.
        ids = {n["id"] for n in nodes}
        assert "Q-0001" in ids, f"Q-0001 not found in {ids}"
        assert "Q-0002" in ids, f"Q-0002 not found in {ids}"
        assert "DIR-0001" in ids
        assert "DIR-0002" in ids
        assert "DIR-0003" in ids
        assert "D-0001" in ids
        assert "D-0002" in ids
        assert "D-0003" in ids
        assert "QP-0001" in ids

        # Template nodes must NOT appear.
        stems = {n["stem"] for n in nodes}
        assert "_template" not in stems, f"_template appeared in scan: {stems}"
        assert not any(n["stem"].startswith("_") for n in nodes), (
            "template file leaked into scan"
        )

        # Check specific field parsing.
        q1 = next(n for n in nodes if n["id"] == "Q-0001")
        assert q1["type"] == "research_question"
        assert q1["solved"] == "no"
        assert q1["priority"] == "high"
        assert q1["topic"] == "T-0001"

        q2 = next(n for n in nodes if n["id"] == "Q-0002")
        assert q2["solved"] == "yes"
        assert q2["answer_ref"] == "research/Q-0002.md"

        d1 = next(n for n in nodes if n["id"] == "DIR-0001")
        assert d1["type"] == "direction"
        assert d1["status"] == "open"
        assert d1["priority"] == "high"
        assert d1["generated_by"] == "research"

        dec1 = next(n for n in nodes if n["id"] == "D-0001")
        assert dec1["type"] == "decision"
        assert dec1["status"] == "active"
        assert dec1["scope"] == "all"

    print("PASS test_scan_parses_nodes")


# ---------------------------------------------------------------------------
# Test: build_index writes index.md with correct content
# ---------------------------------------------------------------------------

def test_build_index_writes_correctly() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        # Capture stderr to avoid noise.
        content = objectives.build_index(vault, dry_run=False)

        index_path = vault / "objective" / "index.md"
        assert index_path.exists(), "build_index did not create index.md"
        written = index_path.read_text(encoding="utf-8")

        # Frontmatter present.
        assert "type: index" in written
        assert "next_id:" in written
        assert "ai-first: true" in written

        # Node counts table present.
        assert "research_question" in written
        assert "direction" in written
        assert "decision" in written

        # Returns the content string.
        assert content == written

        # All node ids visible in the all-nodes table.
        for nid in ("Q-0001", "Q-0002", "DIR-0001", "DIR-0002", "D-0001"):
            assert nid in written, f"node id {nid} missing from index"

    print("PASS test_build_index_writes_correctly")


# ---------------------------------------------------------------------------
# Test: build_index preserves next_id counters
# ---------------------------------------------------------------------------

def test_build_index_preserves_next_id() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        # Manually bump next_id for direction to 5.
        index_path = vault / "objective" / "index.md"
        text = index_path.read_text(encoding="utf-8")
        text = text.replace("  direction: 1", "  direction: 5")
        index_path.write_text(text, encoding="utf-8")

        objectives.build_index(vault, dry_run=False)

        written = index_path.read_text(encoding="utf-8")
        assert "  direction: 5" in written, (
            "build_index overwrote existing next_id counter"
        )

    print("PASS test_build_index_preserves_next_id")


# ---------------------------------------------------------------------------
# Test: open_frontier returns only open items, ranked by priority
# ---------------------------------------------------------------------------

def test_frontier_returns_open_items_ranked() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        result = objectives.open_frontier(vault)

        rqs = result["research_questions"]
        dirs = result["directions"]
        combined = result["combined_ranked"]

        # Only unsolved research_question.
        rq_ids = {n["id"] for n in rqs}
        assert "Q-0001" in rq_ids, "Q-0001 (unsolved) should be in frontier"
        assert "Q-0002" not in rq_ids, "Q-0002 (solved) must NOT be in frontier"

        # Only open directions.
        dir_ids = {n["id"] for n in dirs}
        assert "DIR-0001" in dir_ids, "DIR-0001 (open) should be in frontier"
        assert "DIR-0002" in dir_ids, "DIR-0002 (open) should be in frontier"
        assert "DIR-0003" not in dir_ids, "DIR-0003 (crawled) must NOT be in frontier"

        # Priority ranking: Q-0001 (high) should come before DIR-0002 (medium) in combined.
        combined_ids = [n["id"] for n in combined]
        idx_q1 = combined_ids.index("Q-0001")
        idx_dir2 = combined_ids.index("DIR-0002")
        assert idx_q1 < idx_dir2, (
            f"Q-0001 (high) should rank before DIR-0002 (medium); got {combined_ids}"
        )

        # DIR-0001 (high) should come before DIR-0002 (medium).
        idx_dir1 = combined_ids.index("DIR-0001")
        assert idx_dir1 < idx_dir2, (
            f"DIR-0001 (high) should rank before DIR-0002 (medium); got {combined_ids}"
        )

    print("PASS test_frontier_returns_open_items_ranked")


# ---------------------------------------------------------------------------
# Test: Q before DIR at same priority
# ---------------------------------------------------------------------------

def test_frontier_q_before_dir_same_priority() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        obj_init.run_apply(vault)

        # Q-0001 medium priority, DIR-0001 medium priority, same created date.
        _write(vault / "objective/research_question/Q-0001-test.md", """\
---
type: research_question
id: Q-0001
created: 2026-01-01
updated: 2026-01-01
solved: "no"
topic: T-0001
priority: medium
answer_ref: ""
---
Test question.
""")
        _write(vault / "objective/direction/DIR-0001-test.md", """\
---
type: direction
id: DIR-0001
created: 2026-01-01
updated: 2026-01-01
generated_by: research
serves_question: Q-0001
topics: []
targets_gap: ""
priority: medium
status: open
---

## reasoning_pattern
test
## expected_evidence
test
## seed_queries
test
## solves_when
test
""")
        result = objectives.open_frontier(vault)
        combined_ids = [n["id"] for n in result["combined_ranked"]]
        assert combined_ids.index("Q-0001") < combined_ids.index("DIR-0001"), (
            "Q- should sort before DIR- at same priority and same date"
        )

    print("PASS test_frontier_q_before_dir_same_priority")


# ---------------------------------------------------------------------------
# Test: read_decisions returns only active decisions, sorted by scope
# ---------------------------------------------------------------------------

def test_read_decisions_active_only_scope_sorted() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        decisions = objectives.read_decisions(vault)

        # Only active decisions (D-0001 scope=all, D-0002 scope=research).
        # D-0003 is archived -> excluded.
        ids = [d["id"] for d in decisions]
        assert "D-0001" in ids, "D-0001 (active) should be in decisions"
        assert "D-0002" in ids, "D-0002 (active) should be in decisions"
        assert "D-0003" not in ids, "D-0003 (archived) must NOT be in decisions"

        # Scope order: all first, then research.
        assert ids[0] == "D-0001", f"D-0001 (scope=all) should be first; got {ids}"
        assert ids[1] == "D-0002", f"D-0002 (scope=research) should be second; got {ids}"

        # Body contains the constraint text.
        d1 = next(d for d in decisions if d["id"] == "D-0001")
        assert "WebSearch" in d1["body"] or "web content" in d1["body"].lower(), (
            "D-0001 body should contain the decision text"
        )

    print("PASS test_read_decisions_active_only_scope_sorted")


# ---------------------------------------------------------------------------
# Test: next_id increments correctly + is safe when called twice
# ---------------------------------------------------------------------------

def test_next_id_increments_correctly() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        obj_init.run_apply(vault)

        # First call for research_question -> Q-0001 (counter was 1, returns 1, sets to 2).
        id1 = objectives.next_id(vault, "research_question")
        assert id1 == "Q-0001", f"expected Q-0001, got {id1!r}"

        # Second call -> Q-0002.
        id2 = objectives.next_id(vault, "research_question")
        assert id2 == "Q-0002", f"expected Q-0002, got {id2!r}"

        # Direction counter is independent.
        dir1 = objectives.next_id(vault, "direction")
        assert dir1 == "DIR-0001", f"expected DIR-0001, got {dir1!r}"

        # Verify the counter in the file advanced.
        index_text = (vault / "objective" / "index.md").read_text(encoding="utf-8")
        counters = objectives._parse_next_id_block(index_text)
        assert counters["research_question"] == 3, (
            f"counter should be 3 after 2 increments; got {counters}"
        )
        assert counters["direction"] == 2, (
            f"direction counter should be 2 after 1 increment; got {counters}"
        )

    print("PASS test_next_id_increments_correctly")


# ---------------------------------------------------------------------------
# Test: next_id pads to 4 digits
# ---------------------------------------------------------------------------

def test_next_id_format() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        obj_init.run_apply(vault)

        # Set topic counter to 99.
        index_path = vault / "objective" / "index.md"
        text = index_path.read_text(encoding="utf-8")
        text = text.replace("  topic: 1", "  topic: 99")
        index_path.write_text(text, encoding="utf-8")

        id99 = objectives.next_id(vault, "topic")
        assert id99 == "T-0099", f"expected T-0099, got {id99!r}"

    print("PASS test_next_id_format")


# ---------------------------------------------------------------------------
# Test: decisions CLI verb prints without error
# ---------------------------------------------------------------------------

def test_decisions_cli_verb_runs() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        buf = StringIO()
        with redirect_stdout(buf):
            rc = objectives.main(["--vault-root", str(vault), "decisions"])
        assert rc == 0, f"decisions verb returned non-zero: {rc}"
        output = buf.getvalue()
        # Should mention active decisions.
        assert "D-0001" in output or "all" in output, (
            f"decisions output missing expected content: {output!r}"
        )

    print("PASS test_decisions_cli_verb_runs")


# ---------------------------------------------------------------------------
# Test: decisions CLI --json emits valid JSON
# ---------------------------------------------------------------------------

def test_decisions_cli_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        buf = StringIO()
        with redirect_stdout(buf):
            rc = objectives.main(["--vault-root", str(vault), "--json", "decisions"])
        assert rc == 0
        data = json.loads(buf.getvalue())
        assert isinstance(data, list)
        assert any(d["id"] == "D-0001" for d in data)
        assert not any(d["id"] == "D-0003" for d in data), "archived D-0003 must not appear"

    print("PASS test_decisions_cli_json")


# ---------------------------------------------------------------------------
# Test: status verb counts per type
# ---------------------------------------------------------------------------

def test_status_verb_counts() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        buf = StringIO()
        with redirect_stdout(buf):
            rc = objectives.main(["--vault-root", str(vault), "--json", "status"])
        assert rc == 0
        data = json.loads(buf.getvalue())
        counts = data["counts"]
        assert counts.get("research_question", 0) == 2, f"expected 2 rq, got {counts}"
        assert counts.get("direction", 0) == 3, f"expected 3 directions, got {counts}"
        assert counts.get("decision", 0) == 3, f"expected 3 decisions, got {counts}"
        assert data["research_question_open"] == 1, "1 unsolved rq expected"
        assert data["research_question_solved"] == 1, "1 solved rq expected"

    print("PASS test_status_verb_counts")


# ---------------------------------------------------------------------------
# Test: frontier verb emits valid JSON
# ---------------------------------------------------------------------------

def test_frontier_cli_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault"
        vault.mkdir()
        make_fixture(vault)

        buf = StringIO()
        with redirect_stdout(buf):
            rc = objectives.main(["--vault-root", str(vault), "--json", "frontier"])
        assert rc == 0
        data = json.loads(buf.getvalue())
        assert "research_questions" in data
        assert "directions" in data
        assert "combined_ranked" in data
        rq_ids = {n["id"] for n in data["research_questions"]}
        assert "Q-0001" in rq_ids
        assert "Q-0002" not in rq_ids  # solved

    print("PASS test_frontier_cli_json")


# ---------------------------------------------------------------------------
# Self-running fallback
# ---------------------------------------------------------------------------

def _run_all() -> int:
    test_scan_parses_nodes()
    test_build_index_writes_correctly()
    test_build_index_preserves_next_id()
    test_frontier_returns_open_items_ranked()
    test_frontier_q_before_dir_same_priority()
    test_read_decisions_active_only_scope_sorted()
    test_next_id_increments_correctly()
    test_next_id_format()
    test_decisions_cli_verb_runs()
    test_decisions_cli_json()
    test_status_verb_counts()
    test_frontier_cli_json()
    print("\nAll objectives tests PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
