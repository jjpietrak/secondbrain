"""Tests for scripts/wiki_focus_zone.py"""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import wiki_focus_zone as fz


@pytest.fixture()
def store(tmp_path) -> Path:
    """A temporary store path (code-repo side, not vault)."""
    (tmp_path / "meta").mkdir()
    return tmp_path / "meta" / "focus_zone.json"


@pytest.fixture()
def vault(tmp_path) -> Path:
    """A temporary vault root (used only for path relativization)."""
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# _record
# ---------------------------------------------------------------------------

def test_record_new_path_prepended(store):
    fz._record(store, "wiki/concepts/foo.md")
    window = fz._load(store)
    assert window[0]["path"] == "wiki/concepts/foo.md"


def test_record_dedup_moves_to_front(store):
    fz._record(store, "wiki/concepts/foo.md")
    fz._record(store, "wiki/entities/bar.md")
    fz._record(store, "wiki/concepts/foo.md")  # already in window
    window = fz._load(store)
    assert window[0]["path"] == "wiki/concepts/foo.md"
    assert len(window) == 2


def test_record_truncates_at_window_size(store):
    for i in range(fz.WINDOW_SIZE + 5):
        fz._record(store, f"wiki/concepts/page{i}.md")
    window = fz._load(store)
    assert len(window) == fz.WINDOW_SIZE


def test_record_skips_non_wiki_path(store):
    fz._record(store, "meta/ingest_index.json")
    assert not store.exists()


def test_record_skips_infrastructure_files(store):
    for name in ("hot.md", "log.md", "index.md", "_template.md"):
        fz._record(store, f"wiki/{name}")
    window = fz._load(store)
    assert window == []


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def test_render_empty(store, capsys):
    fz.cmd_render(store)
    out = capsys.readouterr().out
    assert "## Focus zone (last 10 nodes)" in out
    assert out.strip().endswith("-")


def test_render_three_entries(store, capsys):
    paths = [
        "wiki/concepts/attention-ffn-disaggregation.md",
        "wiki/entities/iris-tetra.md",
        "wiki/sources/aiconfigurator-2601.06288.md",
    ]
    for p in paths:
        fz._record(store, p)
    fz.cmd_render(store)
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.startswith("- [[")]
    assert len(lines) == 3
    for line in lines:
        assert ".md]]" not in line
    assert "[[wiki/sources/aiconfigurator-2601.06288]]" in out
