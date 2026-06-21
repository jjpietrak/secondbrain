"""Hermetic tests for ingest_index title resolution (_resolve_title / render_md).

Verifies that:
  - A row whose source_page frontmatter has a real title: renders that title,
    not the filename stem / arXiv id.
  - The resolved title is backfilled into ingest_index.json (row["title"]).
  - _is_stem_title correctly identifies arXiv-id-shaped and filename-stem values.
  - A row with no source_page but a good stored title is left unchanged.
  - The missing-.md suffix case (e.g. "wiki/sources/astra-sim-3" without .md)
    is handled transparently.
  - A row with a real stored title (not stem-shaped) is NOT re-resolved.

No network, no paid API -- all I/O is in a temp directory.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the repo root is importable when running from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent))

import importlib
import agents.ingest_index as ii


# --------------------------------------------------------------------------- #
# _is_stem_title
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("title,expected", [
    # Clearly stem-like (bad titles)
    ("2504.02263v4", True),
    ("2602.09721v1", True),
    ("2507.19427v1", True),
    ("2606.10440v1", True),
    ("Photons_to_Tokens", True),
    ("astra-sim-3", True),
    ("my_paper_stem", True),
    ("", True),
    # Real human titles (not stem-like)
    ("MegaScale-Infer: Serving Mixture-of-Experts at Scale", False),
    ("Revealing the Challenges of Attention-FFN Disaggregation", False),
    ("Step-3 is Large yet Affordable", False),
    ("Photons to Tokens: Systems-Level Performance Characterization", False),
    ("GTC 2026 - The Inference Kingdom Expands", False),
    # Edge: a real title that happens to start with a number word
    ("Three papers on inference disaggregation", False),
])
def test_is_stem_title(title, expected):
    assert ii._is_stem_title(title) == expected


# --------------------------------------------------------------------------- #
# _resolve_title -- core resolution logic
# --------------------------------------------------------------------------- #

def _make_vault(tmp_path: Path, rows: list[dict]) -> dict:
    """Set up a minimal vault with source pages + ingest_index.json, return data dict."""
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)
    sources_dir = tmp_path / "wiki" / "sources"
    sources_dir.mkdir(parents=True)
    raw_dir = tmp_path / "raw" / "papers"
    raw_dir.mkdir(parents=True)

    data = {"version": 3, "vault": "TestVault", "sources": {}}
    for row in rows:
        data["sources"][row["id"]] = row
        # Write source_page file if specified in the row.
        sp = row.get("source_page", "")
        sp_title = row.get("_sp_title", "")  # test-only helper key
        if sp and sp_title:
            p = tmp_path / sp
            if not p.suffix:
                p = p.with_suffix(".md")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                f'---\ntype: source\ntitle: "{sp_title}"\n---\n# Body\n'
            )

    (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))
    return data


def test_resolve_title_from_source_page(tmp_path):
    """A row with a stem title but a valid source_page -> real title from frontmatter."""
    rows = [
        {
            "id": "arxiv:2504.02263",
            "id_type": "arxiv",
            "status": "ingested",
            "title": "2504.02263v4",          # bad stem title
            "filename": "raw/papers/2504.02263v4.pdf",
            "url": "https://arxiv.org/abs/2504.02263",
            "source_type": "paper",
            "content_hash": "sha256:aabbccdd",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "ingested_at": "2026-01-01T00:00:00+00:00",
            "source_page": "wiki/sources/megascale-infer.md",
            "_sp_title": "MegaScale-Infer: Serving Mixture-of-Experts at Scale",
        }
    ]
    _make_vault(tmp_path, rows)
    row = {"id": "arxiv:2504.02263", "title": "2504.02263v4",
           "source_page": "wiki/sources/megascale-infer.md", "filename": ""}
    result = ii._resolve_title(row, tmp_path)
    assert result == "MegaScale-Infer: Serving Mixture-of-Experts at Scale"


def test_resolve_title_missing_md_suffix(tmp_path):
    """source_page stored without .md extension -> resolver adds it transparently."""
    rows = [
        {
            "id": "arxiv:2606.10440",
            "id_type": "arxiv",
            "status": "ingested",
            "title": "2606.10440v1",
            "filename": "raw/papers/2606.10440v1.pdf",
            "url": "https://arxiv.org/abs/2606.10440",
            "source_type": "paper",
            "content_hash": "sha256:aabbccdd",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "ingested_at": "2026-01-01T00:00:00+00:00",
            "source_page": "wiki/sources/astra-sim-3",   # NO .md suffix
            "_sp_title": "ASTRA-sim 3.0: Next-Level Distributed Machine Learning Simulations",
        }
    ]
    _make_vault(tmp_path, rows)
    row = {"id": "arxiv:2606.10440", "title": "2606.10440v1",
           "source_page": "wiki/sources/astra-sim-3", "filename": ""}
    result = ii._resolve_title(row, tmp_path)
    assert result == "ASTRA-sim 3.0: Next-Level Distributed Machine Learning Simulations"


def test_resolve_title_good_stored_title_unchanged(tmp_path):
    """A row with a real stored title (not stem-shaped) -> returned as-is without I/O."""
    row = {
        "id": "sha256:af5f66ad",
        "title": "GTC 2026 - The Inference Kingdom Expands",
        "source_page": "",
        "filename": "",
    }
    result = ii._resolve_title(row, tmp_path)
    assert result == "GTC 2026 - The Inference Kingdom Expands"


def test_resolve_title_no_source_page_returns_stored(tmp_path):
    """Row with stem title and no source_page -> returns the stored stem (last resort)."""
    row = {
        "id": "arxiv:9999.00001",
        "title": "9999.00001v1",
        "source_page": "",
        "filename": "",
    }
    result = ii._resolve_title(row, tmp_path)
    assert result == "9999.00001v1"


# --------------------------------------------------------------------------- #
# render_md integration: backfill + correct title column
# --------------------------------------------------------------------------- #

def test_render_md_backfills_title(tmp_path, monkeypatch):
    """render_md writes the resolved title into ingest_index.json (backfill)."""
    real_title = "MegaScale-Infer: Serving Mixture-of-Experts at Scale"
    rows = [
        {
            "id": "arxiv:2504.02263",
            "id_type": "arxiv",
            "status": "ingested",
            "title": "2504.02263v4",           # bad stem
            "filename": "raw/papers/2504.02263v4.pdf",
            "url": "https://arxiv.org/abs/2504.02263",
            "source_type": "paper",
            "content_hash": "sha256:aabbccdd",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "ingested_at": "2026-01-01T00:00:00+00:00",
            "source_page": "wiki/sources/megascale-infer.md",
            "_sp_title": real_title,
            # v3 fields
            "relevance_score": None, "rationale": "", "discovered_by": "",
            "objective_ids": [], "proposed_at": None, "rejected_at": None,
            "rejection_reason": "",
        }
    ]
    _make_vault(tmp_path, rows)

    # Point vault_config at our temp vault.
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    # render_md should resolve + backfill.
    md_path = ii.render_md()

    # 1. The markdown table must show the real title, not the stem in the Title column.
    md_content = md_path.read_text()
    assert real_title[:50] in md_content, f"Title not found in MD:\n{md_content}"
    # The stem "2504.02263v4" appears in the File column (filename), which is fine.
    # Check that the Title column cell does NOT contain the stem by looking at the
    # data row: the cell before the filename should be the real title.
    # Table row format: | `id` | status | type | TITLE | filename | url | wiki |
    # We verify the title cell specifically: split on " | " and check column index 3.
    for line in md_content.splitlines():
        if "arxiv:2504.02263" in line:
            cells = [c.strip() for c in line.split("|")]
            # cells[0] is empty (before first |), cells[1]=id, cells[2]=status,
            # cells[3]=type, cells[4]=title, cells[5]=file, ...
            title_cell = cells[4] if len(cells) > 4 else ""
            assert real_title[:50] in title_cell, (
                f"Title cell contains wrong value: {title_cell!r}"
            )
            assert "2504.02263v4" not in title_cell, (
                f"Stem still in title cell: {title_cell!r}"
            )
            break

    # 2. ingest_index.json must have the backfilled title.
    json_data = json.loads((tmp_path / "meta" / "ingest_index.json").read_text())
    stored_title = json_data["sources"]["arxiv:2504.02263"]["title"]
    assert stored_title == real_title, f"JSON title not backfilled: {stored_title!r}"


def test_render_md_article_title_unchanged(tmp_path, monkeypatch):
    """A row with a good article title (contains spaces, not stem-shaped) is left alone."""
    article_title = "GTC 2026 - The Inference Kingdom Expands"
    rows = [
        {
            "id": "sha256:af5f66ad",
            "id_type": "hash",
            "status": "ingested",
            "title": article_title,
            "filename": "raw/articles/GTC 2026.md",
            "url": "",
            "source_type": "article",
            "content_hash": "sha256:af5f66ad",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "ingested_at": "2026-01-01T00:00:00+00:00",
            "source_page": "wiki/sources/gtc.md",
            "_sp_title": "Different title in source_page",
            # v3 fields
            "relevance_score": None, "rationale": "", "discovered_by": "",
            "objective_ids": [], "proposed_at": None, "rejected_at": None,
            "rejection_reason": "",
        }
    ]
    _make_vault(tmp_path, rows)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))

    md_path = ii.render_md()
    md_content = md_path.read_text()

    # The good article title must be preserved (fast path: not stem-shaped).
    assert article_title in md_content
