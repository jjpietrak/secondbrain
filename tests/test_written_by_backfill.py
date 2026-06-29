#!/usr/bin/env python3
"""Hermetic tests for scripts/written_by_backfill.py.

No LLM, no network, no live vault.  All fixtures are temp directories.

Covers:
  1. wiki/ files get written_by: wiki.
  2. research/ top-level and deep/ files get written_by: research.
  3. research/notes/ files get written_by: USER.
  4. generated_by is removed when present.
  5. Idempotency: second run produces no changes.
  6. _template.md is skipped.
  7. index.md, hot.md, log.md are skipped.
  8. .gitkeep is skipped.
  9. Files with correct written_by and no generated_by are not rewritten.
  10. written_by with wrong value is corrected.
  11. Files without any frontmatter get a minimal frontmatter block.
  12. dry_run=True does not write files.
  13. Body content is preserved verbatim.
  14. All other frontmatter fields are preserved.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.written_by_backfill import (  # noqa: E402
    _written_by_value,
    _should_skip,
    _rewrite_frontmatter,
    run_backfill,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fm_with(fields: dict[str, str], body: str = "# Body\n\nSome content.\n") -> str:
    """Build a markdown file with frontmatter."""
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def _read_fm(path: Path) -> dict[str, str]:
    """Parse frontmatter from a file into a flat str->str dict."""
    import re
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        fm[k.strip().lower()] = v.strip()
    return fm


# ---------------------------------------------------------------------------
# 1 + 2 + 3. _written_by_value area routing
# ---------------------------------------------------------------------------

class TestWrittenByValue:
    def test_wiki_area(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        wiki_file = vault / "wiki" / "concepts" / "latency.md"
        assert _written_by_value(wiki_file, vault) == "wiki"

    def test_wiki_top_level(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        wiki_file = vault / "wiki" / "gaps.md"
        assert _written_by_value(wiki_file, vault) == "wiki"

    def test_research_top_level(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        research_file = vault / "research" / "Q-0001.md"
        assert _written_by_value(research_file, vault) == "research"

    def test_research_deep(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        deep_file = vault / "research" / "deep" / "2026-06-21-analysis.md"
        assert _written_by_value(deep_file, vault) == "research"

    def test_research_query(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        query_file = vault / "research" / "query" / "2026-06-21-q.md"
        assert _written_by_value(query_file, vault) == "research"

    def test_research_notes_is_USER(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        notes_file = vault / "research" / "notes" / "my-note.md"
        assert _written_by_value(notes_file, vault) == "USER"


# ---------------------------------------------------------------------------
# 6 + 7 + 8. _should_skip
# ---------------------------------------------------------------------------

class TestShouldSkip:
    def test_template_skipped(self, tmp_path: Path) -> None:
        assert _should_skip(tmp_path / "wiki" / "concepts" / "_template.md") is True

    def test_template_in_research_skipped(self, tmp_path: Path) -> None:
        assert _should_skip(tmp_path / "research" / "deep" / "_template.md") is True

    def test_index_skipped(self, tmp_path: Path) -> None:
        assert _should_skip(tmp_path / "wiki" / "index.md") is True

    def test_hot_skipped(self, tmp_path: Path) -> None:
        assert _should_skip(tmp_path / "wiki" / "hot.md") is True

    def test_log_skipped(self, tmp_path: Path) -> None:
        assert _should_skip(tmp_path / "wiki" / "log.md") is True

    def test_gitkeep_skipped(self, tmp_path: Path) -> None:
        assert _should_skip(tmp_path / "wiki" / ".gitkeep") is True

    def test_normal_page_not_skipped(self, tmp_path: Path) -> None:
        assert _should_skip(tmp_path / "wiki" / "concepts" / "latency.md") is False

    def test_research_report_not_skipped(self, tmp_path: Path) -> None:
        assert _should_skip(tmp_path / "research" / "Q-0001.md") is False


# ---------------------------------------------------------------------------
# 4. generated_by removed, written_by added
# ---------------------------------------------------------------------------

class TestRewriteFrontmatter:
    def test_adds_written_by_and_removes_generated_by(self) -> None:
        text = _fm_with({
            "type": "research_report",
            "generated_by": "research",
            "created": "2026-06-21",
        })
        new_text, changed, reason = _rewrite_frontmatter(text, "research")
        assert changed is True
        assert "written_by: research" in new_text
        assert "generated_by" not in new_text
        assert "removed generated_by" in reason
        assert "added written_by" in reason

    def test_adds_written_by_when_absent(self) -> None:
        text = _fm_with({"type": "wiki_page", "created": "2026-06-21"})
        new_text, changed, reason = _rewrite_frontmatter(text, "wiki")
        assert changed is True
        assert "written_by: wiki" in new_text
        assert "generated_by" not in new_text

    def test_no_change_when_already_correct(self) -> None:
        text = _fm_with({
            "type": "wiki_page",
            "written_by": "wiki",
            "created": "2026-06-21",
        })
        _, changed, _ = _rewrite_frontmatter(text, "wiki")
        assert changed is False

    def test_corrects_wrong_written_by_value(self) -> None:
        text = _fm_with({
            "type": "research_report",
            "written_by": "wiki",  # wrong
            "created": "2026-06-21",
        })
        new_text, changed, reason = _rewrite_frontmatter(text, "research")
        assert changed is True
        assert "written_by: research" in new_text
        assert "written_by: wiki" not in new_text
        assert "corrected written_by" in reason

    def test_removes_generated_by_when_written_by_already_correct(self) -> None:
        text = _fm_with({
            "type": "research_report",
            "written_by": "research",
            "generated_by": "research",  # residual - must be removed
            "created": "2026-06-21",
        })
        new_text, changed, reason = _rewrite_frontmatter(text, "research")
        assert changed is True
        assert "generated_by" not in new_text
        assert "written_by: research" in new_text
        assert "removed generated_by" in reason

    def test_preserves_body(self) -> None:
        body = "# Title\n\nThis is the body content with **markdown**.\n"
        text = _fm_with({"type": "wiki_page", "generated_by": "wiki"}, body=body)
        new_text, changed, _ = _rewrite_frontmatter(text, "wiki")
        assert changed is True
        assert body in new_text

    def test_preserves_other_fm_fields(self) -> None:
        text = _fm_with({
            "type": "research_report",
            "id": "Q-0001",
            "created": "2026-06-21",
            "status": "draft",
            "generated_by": "research",
        })
        new_text, changed, _ = _rewrite_frontmatter(text, "research")
        assert changed is True
        assert "type: research_report" in new_text
        assert "id: Q-0001" in new_text
        assert "created: 2026-06-21" in new_text
        assert "status: draft" in new_text

    def test_no_frontmatter_gets_minimal_block(self) -> None:
        text = "# Just a heading\n\nNo frontmatter here.\n"
        new_text, changed, reason = _rewrite_frontmatter(text, "wiki")
        assert changed is True
        assert "written_by: wiki" in new_text
        assert new_text.startswith("---\n")


# ---------------------------------------------------------------------------
# 5. Idempotency via run_backfill
# ---------------------------------------------------------------------------

class TestRunBackfillIdempotent:
    def test_idempotent(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        wiki_dir = vault / "wiki" / "concepts"
        wiki_dir.mkdir(parents=True)

        # Page with generated_by and missing written_by
        page = _write(
            wiki_dir / "latency.md",
            _fm_with({
                "type": "wiki_page",
                "generated_by": "research",
                "created": "2026-06-21",
            }),
        )

        # First run (apply)
        summary1 = run_backfill(vault, dry_run=False)
        assert summary1["changed"] >= 1

        text_after_first = page.read_text(encoding="utf-8")

        # Second run (apply) - must be a no-op
        summary2 = run_backfill(vault, dry_run=False)
        assert summary2["changed"] == 0, (
            f"Second run changed {summary2['changed']} files; expected 0"
        )
        text_after_second = page.read_text(encoding="utf-8")
        assert text_after_first == text_after_second, "File changed on second run"


# ---------------------------------------------------------------------------
# 12. dry_run does not write
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_no_writes(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        wiki_dir = vault / "wiki"
        wiki_dir.mkdir(parents=True)

        page = _write(
            wiki_dir / "test.md",
            _fm_with({
                "type": "wiki_page",
                "generated_by": "wiki",
                "created": "2026-06-21",
            }),
        )
        original_text = page.read_text(encoding="utf-8")

        summary = run_backfill(vault, dry_run=True)
        assert summary["changed"] >= 1, "dry-run should report pending changes"

        # File must not be modified
        assert page.read_text(encoding="utf-8") == original_text, (
            "dry_run=True must not modify files"
        )


# ---------------------------------------------------------------------------
# Integration: correct values per area + skip logic
# ---------------------------------------------------------------------------

class TestRunBackfillIntegration:
    def _setup_vault(self, vault: Path) -> dict[str, Path]:
        """Create a fixture vault with pages in multiple areas."""
        paths: dict[str, Path] = {}

        # wiki/concepts/latency.md - has generated_by -> should get written_by: wiki
        paths["wiki_page"] = _write(
            vault / "wiki" / "concepts" / "latency.md",
            _fm_with({
                "type": "wiki_page",
                "generated_by": "wiki",
                "created": "2026-06-21",
            }),
        )

        # research/Q-0001.md - has generated_by -> written_by: research
        paths["research_report"] = _write(
            vault / "research" / "Q-0001.md",
            _fm_with({
                "type": "research_report",
                "generated_by": "research",
                "created": "2026-06-21",
            }),
        )

        # research/deep/2026-06-21-test.md - has generated_by -> written_by: research
        paths["research_deep"] = _write(
            vault / "research" / "deep" / "2026-06-21-test.md",
            _fm_with({
                "type": "research_report",
                "generated_by": "research",
                "created": "2026-06-21",
            }),
        )

        # research/notes/my-note.md -> written_by: USER
        paths["research_notes"] = _write(
            vault / "research" / "notes" / "my-note.md",
            _fm_with({
                "type": "note",
                "generated_by": "research",
                "created": "2026-06-21",
            }),
        )

        # Templates and structural files that must be skipped
        _write(
            vault / "wiki" / "_template.md",
            _fm_with({"type": "wiki_page"}),
        )
        _write(
            vault / "wiki" / "index.md",
            _fm_with({"type": "index"}),
        )
        _write(
            vault / "wiki" / "hot.md",
            _fm_with({"type": "hot"}),
        )
        _write(
            vault / "wiki" / "log.md",
            _fm_with({"type": "log"}),
        )
        _write(
            vault / "research" / "index.md",
            _fm_with({"type": "index"}),
        )
        # .gitkeep
        (vault / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
        (vault / "wiki" / "entities" / ".gitkeep").write_text("", encoding="utf-8")

        return paths

    def test_stamps_correct_values_per_area(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        paths = self._setup_vault(vault)

        summary = run_backfill(vault, dry_run=False)

        # wiki page -> written_by: wiki
        fm_wiki = _read_fm(paths["wiki_page"])
        assert fm_wiki.get("written_by") == "wiki", (
            f"wiki page: expected written_by=wiki, got {fm_wiki.get('written_by')!r}"
        )
        assert "generated_by" not in fm_wiki

        # research report -> written_by: research
        fm_research = _read_fm(paths["research_report"])
        assert fm_research.get("written_by") == "research", (
            f"research report: expected written_by=research, got {fm_research.get('written_by')!r}"
        )
        assert "generated_by" not in fm_research

        # research/deep -> written_by: research
        fm_deep = _read_fm(paths["research_deep"])
        assert fm_deep.get("written_by") == "research"
        assert "generated_by" not in fm_deep

        # research/notes -> written_by: USER
        fm_notes = _read_fm(paths["research_notes"])
        assert fm_notes.get("written_by") == "USER", (
            f"research/notes: expected written_by=USER, got {fm_notes.get('written_by')!r}"
        )
        assert "generated_by" not in fm_notes

    def test_skips_templates_index_log_hot(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        paths = self._setup_vault(vault)

        # Snapshot the structural files before
        template_before = (vault / "wiki" / "_template.md").read_text(encoding="utf-8")
        index_before = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
        hot_before = (vault / "wiki" / "hot.md").read_text(encoding="utf-8")
        log_before = (vault / "wiki" / "log.md").read_text(encoding="utf-8")

        run_backfill(vault, dry_run=False)

        assert (vault / "wiki" / "_template.md").read_text(encoding="utf-8") == template_before
        assert (vault / "wiki" / "index.md").read_text(encoding="utf-8") == index_before
        assert (vault / "wiki" / "hot.md").read_text(encoding="utf-8") == hot_before
        assert (vault / "wiki" / "log.md").read_text(encoding="utf-8") == log_before

    def test_summary_counts(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        self._setup_vault(vault)

        summary = run_backfill(vault, dry_run=False)

        # 4 content pages should be changed; skipped count >= structural files
        assert summary["changed"] == 4, (
            f"Expected 4 changed files, got {summary['changed']}"
        )
        assert summary["skipped"] >= 5, (
            f"Expected at least 5 skipped (template+index+hot+log+research_index), "
            f"got {summary['skipped']}"
        )
        assert summary["errors"] == 0

    def test_already_correct_pages_not_rewritten(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        wiki_dir = vault / "wiki"
        wiki_dir.mkdir(parents=True)

        # Page already has correct written_by and no generated_by
        page = _write(
            wiki_dir / "already-ok.md",
            _fm_with({
                "type": "wiki_page",
                "written_by": "wiki",
                "created": "2026-06-21",
            }),
        )
        original_text = page.read_text(encoding="utf-8")

        summary = run_backfill(vault, dry_run=False)
        assert summary["changed"] == 0

        # File must not be touched
        assert page.read_text(encoding="utf-8") == original_text


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
