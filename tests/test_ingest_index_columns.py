"""Hermetic tests for the four new ingest_index display columns.

Tests cover:
  1. `published` field storage in enqueue() -- set, absent (None), re-enqueue no-clobber.
  2. render_md() table contains the four column headers.
  3. render_md() row shows published date, rationale (truncated), and a resolved [[...]] link.
  4. _relevance_links() resolution:
     - DIR/Q/QP/T/D ids -> full-path wikilinks when the file exists in objective/<subdir>/
     - GAP-## -> [[wiki/gap/<stem>]] when a matching GAP-##-*.md file exists in wiki/gap/
     - GAP-## -> [[wiki/gap/index]] (GAP-##) fallback when no file found
     - fallback to [[<id>]] when the objective file does not exist
     - empty / None input -> ""

No network, no paid API -- all I/O is in a temp directory.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.ingest_index as ii


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    """Set up a minimal vault skeleton and write ingest_index.json if rows given."""
    (tmp_path / "meta").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki").mkdir(parents=True, exist_ok=True)
    if rows is not None:
        data = {"version": 3, "vault": "TestVault", "sources": {}}
        for row in rows:
            # Strip test-only helper keys before storing
            clean = {k: v for k, v in row.items() if not k.startswith("_")}
            data["sources"][clean["id"]] = clean
        (tmp_path / "meta" / "ingest_index.json").write_text(
            json.dumps(data, indent=2)
        )
    return tmp_path


def _make_objective_file(tmp_path: Path, subdir: str, filename: str) -> Path:
    """Create a stub objective/<subdir>/<filename>.md and return its path."""
    p = tmp_path / "objective" / subdir / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: {subdir}\n---\n# {filename}\n")
    return p


def _make_gap_file(tmp_path: Path, filename: str) -> Path:
    """Create a stub wiki/gap/<filename>.md and return its path."""
    p = tmp_path / "wiki" / "gap" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: gap\nid: {filename.split('-')[0]+'-'+filename.split('-')[1]}\n---\n")
    return p


# ---------------------------------------------------------------------------
# 1. published field in enqueue()
# ---------------------------------------------------------------------------

def test_enqueue_stores_published(tmp_path, monkeypatch):
    """enqueue(..., published='2026-05-01') stores the date in the row."""
    _make_vault(tmp_path)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    row = ii.enqueue("url:https://example.com/article",
                     title="Example Article",
                     published="2026-05-01")
    assert row["published"] == "2026-05-01"

    # Also confirmed via JSON on disk
    data = json.loads((tmp_path / "meta" / "ingest_index.json").read_text())
    sid = "url:https://example.com/article"
    assert data["sources"][sid]["published"] == "2026-05-01"


def test_enqueue_absent_published_is_none(tmp_path, monkeypatch):
    """enqueue() without published= leaves published as None."""
    _make_vault(tmp_path)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    row = ii.enqueue("url:https://example.com/nopub", title="No Pub")
    assert row["published"] is None


def test_enqueue_no_clobber_existing_published(tmp_path, monkeypatch):
    """Re-enqueue without published= must NOT overwrite an existing published value."""
    _make_vault(tmp_path)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    ii.enqueue("url:https://example.com/keep", title="Keep Pub", published="2025-12-01")
    # Re-enqueue without published= -- original value must survive
    row = ii.enqueue("url:https://example.com/keep", title="Keep Pub Updated")
    assert row["published"] == "2025-12-01", (
        f"published was clobbered: {row['published']!r}"
    )


def test_enqueue_can_update_published(tmp_path, monkeypatch):
    """Providing a new published= on re-enqueue DOES update the value."""
    _make_vault(tmp_path)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    ii.enqueue("url:https://example.com/update", title="Update Pub", published="2025-01-01")
    row = ii.enqueue("url:https://example.com/update", published="2025-06-15")
    assert row["published"] == "2025-06-15"


# ---------------------------------------------------------------------------
# 2. render_md() column headers
# ---------------------------------------------------------------------------

def test_render_md_has_four_new_column_headers(tmp_path, monkeypatch):
    """render_md() output must contain all four new column header names."""
    _make_vault(tmp_path, rows=[])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    md_path = ii.render_md()
    content = md_path.read_text()

    for header in ("Date Published", "Date Ingested", "Rationale", "Relevance"):
        assert header in content, f"Column header '{header}' missing from render_md output"


# ---------------------------------------------------------------------------
# 3. render_md() row values -- published, rationale, relevance link
# ---------------------------------------------------------------------------

def _base_row(extra: dict | None = None) -> dict:
    """Build a minimal valid v3 row dict."""
    row = {
        "id": "url:https://example.com/paper",
        "id_type": "url",
        "status": "waiting_approval",
        "title": "Test Paper on Disaggregation",
        "filename": "",
        "url": "https://example.com/paper",
        "source_type": "url",
        "content_hash": "",
        "first_seen": "2026-01-01T00:00:00+00:00",
        "ingested_at": None,
        "source_page": "",
        "relevance_score": 0.85,
        "rationale": "Covers KV cache disaggregation in the context of optical interconnects.",
        "discovered_by": "web",
        "objective_ids": [],
        "proposed_at": "2026-01-01T00:00:00+00:00",
        "rejected_at": None,
        "rejection_reason": "",
        "published": "2026-05-15",
    }
    if extra:
        row.update(extra)
    return row


def test_render_md_shows_published_date(tmp_path, monkeypatch):
    """A row with published='2026-05-15' must show that date in the table."""
    _make_vault(tmp_path, rows=[_base_row()])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    content = ii.render_md().read_text()
    assert "2026-05-15" in content, "Published date not found in render_md output"


def test_render_md_shows_dash_when_no_published(tmp_path, monkeypatch):
    """A row with published=None must show '—' in the Date Published column."""
    row = _base_row({"published": None})
    _make_vault(tmp_path, rows=[row])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    content = ii.render_md().read_text()
    # Find the data row (not the header)
    for line in content.splitlines():
        if "url:https://example.com/paper" in line:
            assert "—" in line, f"Expected '—' for missing published in: {line!r}"
            break


def test_render_md_shows_rationale(tmp_path, monkeypatch):
    """A row with a rationale must show the text (up to 120 chars) in the table."""
    _make_vault(tmp_path, rows=[_base_row()])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    content = ii.render_md().read_text()
    assert "Covers KV cache disaggregation" in content, "Rationale not found in render_md output"


def test_render_md_truncates_long_rationale(tmp_path, monkeypatch):
    """A rationale longer than 120 chars is truncated with '...' in the table."""
    long_rat = "A" * 150
    row = _base_row({"rationale": long_rat})
    _make_vault(tmp_path, rows=[row])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    content = ii.render_md().read_text()
    # The full 150-char string should NOT appear; the truncated form should
    assert long_rat not in content, "Long rationale was not truncated"
    assert "A" * 120 in content, "Truncated prefix not found"


def test_render_md_resolves_dir_link(tmp_path, monkeypatch):
    """A row with objective_ids=['DIR-0004'] should show a resolved wikilink."""
    _make_objective_file(tmp_path, "direction", "DIR-0004-optical-prior-art-comparison.md")
    row = _base_row({"objective_ids": ["DIR-0004"]})
    _make_vault(tmp_path, rows=[row])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    content = ii.render_md().read_text()
    assert "[[objective/direction/DIR-0004-optical-prior-art-comparison]]" in content, (
        f"Resolved DIR link not found in:\n{content}"
    )


def test_render_md_gap_link_with_file(tmp_path, monkeypatch):
    """A row with objective_ids=['GAP-08'] shows [[wiki/gap/GAP-08-<slug>]] when the file exists."""
    _make_gap_file(tmp_path, "GAP-08-optical-prior-art.md")
    row = _base_row({"objective_ids": ["GAP-08"]})
    _make_vault(tmp_path, rows=[row])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    content = ii.render_md().read_text()
    assert "[[wiki/gap/GAP-08-optical-prior-art]]" in content, (
        f"[[wiki/gap/GAP-08-optical-prior-art]] not found in:\n{content}"
    )
    assert "GAP-08" in content


def test_render_md_gap_link_fallback(tmp_path, monkeypatch):
    """A row with objective_ids=['GAP-08'] falls back to [[wiki/gap/index]] (GAP-08) when no file."""
    row = _base_row({"objective_ids": ["GAP-08"]})
    _make_vault(tmp_path, rows=[row])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    content = ii.render_md().read_text()
    assert "[[wiki/gap/index]]" in content, f"[[wiki/gap/index]] not found in:\n{content}"
    assert "GAP-08" in content


def test_render_md_mixed_relevance(tmp_path, monkeypatch):
    """DIR + GAP ids together produce a comma-separated relevance cell."""
    _make_objective_file(tmp_path, "direction", "DIR-0004-optical-prior-art-comparison.md")
    _make_gap_file(tmp_path, "GAP-08-optical-prior-art.md")
    row = _base_row({"objective_ids": ["DIR-0004", "GAP-08"]})
    _make_vault(tmp_path, rows=[row])
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    content = ii.render_md().read_text()
    assert "[[objective/direction/DIR-0004-optical-prior-art-comparison]]" in content
    assert "[[wiki/gap/GAP-08-optical-prior-art]]" in content


# ---------------------------------------------------------------------------
# 4. _relevance_links() unit tests
# ---------------------------------------------------------------------------

def test_relevance_links_empty():
    """Empty / None input returns empty string."""
    assert ii._relevance_links([], Path("/nonexistent")) == ""
    assert ii._relevance_links(None, Path("/nonexistent")) == ""


def test_relevance_links_gap_fallback(tmp_path):
    """GAP-## with no matching file resolves to [[wiki/gap/index]] (GAP-##) fallback."""
    result = ii._relevance_links(["GAP-01"], tmp_path)
    assert result == "[[wiki/gap/index]] (GAP-01)"


def test_relevance_links_gap_file_found(tmp_path):
    """GAP-08 with a matching GAP-08-*.md file resolves to [[wiki/gap/GAP-08-<stem>]]."""
    _make_gap_file(tmp_path, "GAP-08-optical-prior-art.md")
    result = ii._relevance_links(["GAP-08"], tmp_path)
    assert result == "[[wiki/gap/GAP-08-optical-prior-art]]"


def test_relevance_links_gap_two_digit_fallback(tmp_path):
    """GAP-08, GAP-10, etc. with no file -> [[wiki/gap/index]] (GAP-##) fallback form."""
    assert ii._relevance_links(["GAP-08"], tmp_path) == "[[wiki/gap/index]] (GAP-08)"
    assert ii._relevance_links(["GAP-10"], tmp_path) == "[[wiki/gap/index]] (GAP-10)"


def test_relevance_links_dir_file_found(tmp_path):
    """DIR-0004 with matching file -> [[objective/direction/DIR-0004-<stem>]]."""
    _make_objective_file(tmp_path, "direction", "DIR-0004-optical-prior-art-comparison.md")
    result = ii._relevance_links(["DIR-0004"], tmp_path)
    assert result == "[[objective/direction/DIR-0004-optical-prior-art-comparison]]"


def test_relevance_links_q_file_found(tmp_path):
    """Q-0001 with matching file -> [[objective/research_question/Q-0001-<stem>]]."""
    _make_objective_file(tmp_path, "research_question", "Q-0001-afd-dead-zone-optical-bw-target.md")
    result = ii._relevance_links(["Q-0001"], tmp_path)
    assert result == "[[objective/research_question/Q-0001-afd-dead-zone-optical-bw-target]]"


def test_relevance_links_qp_file_found(tmp_path):
    """QP-0001 with matching file -> [[objective/research_question_proposal/QP-0001-<stem>]]."""
    _make_objective_file(tmp_path, "research_question_proposal",
                         "QP-0001-software-disagg-primitives.md")
    result = ii._relevance_links(["QP-0001"], tmp_path)
    assert result == "[[objective/research_question_proposal/QP-0001-software-disagg-primitives]]"


def test_relevance_links_d_file_found(tmp_path):
    """D-0001 with matching file -> [[objective/decision/D-0001-<stem>]]."""
    _make_objective_file(tmp_path, "decision", "D-0001-no-web-research.md")
    result = ii._relevance_links(["D-0001"], tmp_path)
    assert result == "[[objective/decision/D-0001-no-web-research]]"


def test_relevance_links_fallback_no_file(tmp_path):
    """DIR-0099 with no matching file -> [[DIR-0099]] fallback."""
    result = ii._relevance_links(["DIR-0099"], tmp_path)
    assert result == "[[DIR-0099]]"


def test_relevance_links_fallback_missing_objective_dir(tmp_path):
    """DIR-0001 when objective/ does not exist at all -> [[DIR-0001]] fallback (no crash)."""
    # tmp_path has no objective/ subdir
    result = ii._relevance_links(["DIR-0001"], tmp_path)
    assert result == "[[DIR-0001]]"


def test_relevance_links_unknown_id(tmp_path):
    """An id with an unrecognised prefix -> [[<id>]] verbatim."""
    result = ii._relevance_links(["FOO-001"], tmp_path)
    assert result == "[[FOO-001]]"


def test_relevance_links_multiple(tmp_path):
    """Multiple ids are joined with ', '; GAP falls back to index when no file."""
    _make_objective_file(tmp_path, "direction", "DIR-0001-some-dir.md")
    result = ii._relevance_links(["DIR-0001", "GAP-03", "Q-0099"], tmp_path)
    assert result == "[[objective/direction/DIR-0001-some-dir]], [[wiki/gap/index]] (GAP-03), [[Q-0099]]"


def test_relevance_links_case_insensitive_gap(tmp_path):
    """GAP ids are matched case-insensitively; lowercase gap-05 falls back to index form."""
    result = ii._relevance_links(["gap-05"], tmp_path)
    assert "[[wiki/gap/index]]" in result
    assert "GAP-05" in result


# ---------------------------------------------------------------------------
# 5. Sample render_md table output (integration smoke test)
# ---------------------------------------------------------------------------

def test_render_md_sample_table(tmp_path, monkeypatch, capsys):
    """
    End-to-end smoke: build a vault with one rich row (published + rationale + DIR + GAP),
    call render_md(), and assert the exact shape of the Relevance cell.
    Prints the table rows to stdout so the caller can visually inspect them.
    """
    _make_objective_file(tmp_path, "direction", "DIR-0004-optical-prior-art-comparison.md")
    _make_gap_file(tmp_path, "GAP-08-optical-prior-art.md")

    rows = [
        {
            "id": "arxiv:2407.00001",
            "id_type": "arxiv",
            "status": "waiting_approval",
            "title": "Optical KV Cache Transfer for LLM Disaggregation",
            "filename": "",
            "url": "https://arxiv.org/abs/2407.00001",
            "source_type": "arxiv",
            "content_hash": "",
            "first_seen": "2026-06-01T00:00:00+00:00",
            "ingested_at": None,
            "source_page": "",
            "relevance_score": 0.93,
            "rationale": (
                "Proposes a novel KV cache format for optical interconnects. "
                "Directly relevant to the 3-tier disaggregation architecture "
                "being investigated for Inference-Disagg."
            ),
            "discovered_by": "web",
            "objective_ids": ["DIR-0004", "GAP-08"],
            "proposed_at": "2026-06-01T00:00:00+00:00",
            "rejected_at": None,
            "rejection_reason": "",
            "published": "2026-04-22",
        },
        {
            "id": "url:https://blog.example.com/no-pub",
            "id_type": "url",
            "status": "waiting_approval",
            "title": "A blog post with no publish date",
            "filename": "",
            "url": "https://blog.example.com/no-pub",
            "source_type": "url",
            "content_hash": "",
            "first_seen": "2026-06-01T00:00:00+00:00",
            "ingested_at": None,
            "source_page": "",
            "relevance_score": 0.55,
            "rationale": "",
            "discovered_by": "web",
            "objective_ids": [],
            "proposed_at": "2026-06-01T00:00:00+00:00",
            "rejected_at": None,
            "rejection_reason": "",
            "published": None,
        },
    ]

    _make_vault(tmp_path, rows=rows)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    md_path = ii.render_md()
    content = md_path.read_text()

    # Print the table for visual inspection in pytest -s output
    for line in content.splitlines():
        print(line)

    # Assertions on the rich row
    assert "2026-04-22" in content, "published date not in table"
    assert "Proposes a novel KV cache format" in content, "rationale prefix not in table"
    assert "[[objective/direction/DIR-0004-optical-prior-art-comparison]]" in content
    assert "[[wiki/gap/GAP-08-optical-prior-art]]" in content

    # Assertions on the no-pub row
    # Find the data line for the no-pub row and check Date Published is "—"
    for line in content.splitlines():
        if "url:https://blog.example.com/no-pub" in line:
            cells = [c.strip() for c in line.split("|")]
            # Table columns (0-indexed after split):
            # 0=empty, 1=id, 2=status, 3=type, 4=title, 5=date_pub, 6=date_ing,
            # 7=rationale, 8=relevance, 9=file, 10=url, 11=wiki
            date_pub_cell = cells[5] if len(cells) > 5 else ""
            rationale_cell = cells[7] if len(cells) > 7 else ""
            relevance_cell = cells[8] if len(cells) > 8 else ""
            assert date_pub_cell == "—", f"Expected '—' for missing published, got: {date_pub_cell!r}"
            assert rationale_cell == "—", f"Expected '—' for empty rationale, got: {rationale_cell!r}"
            assert relevance_cell == "—", f"Expected '—' for empty relevance, got: {relevance_cell!r}"
            break
