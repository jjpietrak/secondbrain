#!/usr/bin/env python3
"""Hermetic tests for scripts/report_approve.py.

Tests:
  1. parse_report -- 4-block fixture (approve, reject-with-reason, conflict, untouched)
     + Decision trace section that is ignored.
  2. apply(dry_run=True)  -- returns plan, does NOT mutate a tmp ingest_index.
  3. apply(dry_run=False) -- flips statuses in a tmp vault ingest_index:
       approve  -> waiting_approval -> pending
       reject   -> waiting_approval -> rejected (with reason)
       conflict -> skipped
  4. Malformed / edge lines do not crash.

No LLM, no network, no real vault. $0.
Run:  .venv/bin/python -m pytest tests/test_report_approve.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts.report_approve import parse_report, apply  # noqa: E402
from agents import ingest_index as ii                   # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# The four-block report fixture mirrors the pinned format exactly.
# Block A: arxiv:2606.08635v1 -> approve ticked
# Block B: arxiv:2606.11111v1 -> reject ticked with reason
# Block C: arxiv:2606.22222v1 -> BOTH ticked (conflict)
# Block D: arxiv:2606.33333v1 -> untouched (no tick)
# After the ## Decision trace heading there is a stray ticked line that must be IGNORED.
REPORT_MD = """\
# Nightly Report 2026-06-23

## Candidates

### 1. SpectrumKV: Per-Token Mixed-Precision KV Cache Transfer
- [x] approve · `arxiv:2606.08635v1`
- [ ] reject · `arxiv:2606.08635v1`
    - reason:
- **published**: 2026-05-12 · **score**: 0.8312 · **lane**: gap
- **relevance**: [[wiki/gaps]] (GAP-08)
- **rationale**: Directly addresses KV cache memory reduction.
- **url**: https://arxiv.org/abs/2606.08635

### 2. BlogPost on Scheduling
- [ ] approve · `arxiv:2606.11111v1`
- [x] reject · `arxiv:2606.11111v1`
    - reason: Not peer-reviewed; duplicates GAP-03 coverage
- **published**: 2026-06-01 · **score**: 0.52 · **lane**: research
- **url**: https://arxiv.org/abs/2606.11111

### 3. Ambiguous Paper
- [x] approve · `arxiv:2606.22222v1`
- [x] reject · `arxiv:2606.22222v1`
    - reason: Changed my mind
- **published**: 2026-06-10 · **score**: 0.61 · **lane**: gap

### 4. Untouched Source
- [ ] approve · `arxiv:2606.33333v1`
- [ ] reject · `arxiv:2606.33333v1`
    - reason:
- **published**: 2026-05-20 · **score**: 0.41 · **lane**: news

## Decision trace

Some notes here that look like checkboxes but must be IGNORED:
- [x] approve · `arxiv:9999.99999v1`
- [x] reject  · `arxiv:9999.99999v1`
"""

APPROVE_ID = "arxiv:2606.08635v1"
REJECT_ID  = "arxiv:2606.11111v1"
CONFLICT_ID = "arxiv:2606.22222v1"
UNTOUCHED_ID = "arxiv:2606.33333v1"
TRACE_ID = "arxiv:9999.99999v1"


# ---------------------------------------------------------------------------
# 1. parse_report
# ---------------------------------------------------------------------------

class TestParseReport:

    def test_returns_three_entries(self):
        """Only the three ticked sources (approve, reject, conflict) are returned."""
        results = parse_report(REPORT_MD)
        ids = [r["id"] for r in results]
        assert len(results) == 3
        assert APPROVE_ID in ids
        assert REJECT_ID in ids
        assert CONFLICT_ID in ids

    def test_untouched_not_included(self):
        results = parse_report(REPORT_MD)
        ids = [r["id"] for r in results]
        assert UNTOUCHED_ID not in ids

    def test_trace_section_ignored(self):
        """The stray ticked lines after ## Decision trace must be silently ignored."""
        results = parse_report(REPORT_MD)
        ids = [r["id"] for r in results]
        assert TRACE_ID not in ids

    def test_approve_action(self):
        results = parse_report(REPORT_MD)
        by_id = {r["id"]: r for r in results}
        assert by_id[APPROVE_ID]["action"] == "approve"
        assert by_id[APPROVE_ID]["reason"] == ""

    def test_reject_action_with_reason(self):
        results = parse_report(REPORT_MD)
        by_id = {r["id"]: r for r in results}
        rec = by_id[REJECT_ID]
        assert rec["action"] == "reject"
        assert rec["reason"] == "Not peer-reviewed; duplicates GAP-03 coverage"

    def test_conflict_action(self):
        results = parse_report(REPORT_MD)
        by_id = {r["id"]: r for r in results}
        assert by_id[CONFLICT_ID]["action"] == "conflict"

    def test_empty_string_does_not_crash(self):
        results = parse_report("")
        assert results == []

    def test_malformed_checkbox_line_ignored(self):
        """A checkbox line missing the backtick-wrapped id is silently skipped."""
        bad_md = "- [x] approve noid\n- [ ] reject · `ok:id`\n"
        results = parse_report(bad_md)
        # 'ok:id' is unticked -> nothing returned
        assert results == []

    def test_no_reason_line_gives_empty_reason(self):
        md = "- [x] reject · `arxiv:1234.56789`\n"
        results = parse_report(md)
        assert results[0]["reason"] == ""

    def test_reason_line_with_only_whitespace_gives_empty_reason(self):
        md = "- [x] reject · `arxiv:0000.00001`\n    - reason:   \n"
        results = parse_report(md)
        assert results[0]["reason"] == ""


# ---------------------------------------------------------------------------
# 2. apply(dry_run=True)
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_vault(tmp_path: Path):
    """Create a minimal vault dir with a seeded ingest_index.json.

    Seeds three waiting_approval rows (approve, reject, conflict ids).
    Returns (vault_root_path, vault_name).
    """
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)

    data = {
        "version": 3,
        "vault": tmp_path.name,
        "sources": {},
    }
    for ident in (APPROVE_ID, REJECT_ID, CONFLICT_ID):
        data["sources"][ident] = {
            "id": ident,
            "id_type": "arxiv",
            "status": "waiting_approval",
            "title": f"Test source {ident}",
            "filename": "",
            "url": "",
            "source_type": "arxiv",
            "content_hash": "",
            "first_seen": "2026-06-23T00:00:00+00:00",
            "ingested_at": None,
            "source_page": "",
            "relevance_score": None,
            "rationale": "",
            "discovered_by": "",
            "objective_ids": [],
            "proposed_at": "2026-06-23T00:00:00+00:00",
            "rejected_at": None,
            "rejection_reason": "",
            "published": None,
        }

    (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))
    return tmp_path


@pytest.fixture()
def tmp_report(tmp_path: Path) -> Path:
    """Write the REPORT_MD fixture to a temp file and return its path."""
    p = tmp_path / "2026-06-23.md"
    p.write_text(REPORT_MD, encoding="utf-8")
    return p


class TestApplyDryRun:

    def test_dry_run_returns_correct_plan(self, tmp_vault, tmp_report, monkeypatch):
        """apply(dry_run=True) returns the expected plan dict."""
        # Point VAULT_PATH to tmp_vault so ingest_index._load finds the seeded index.
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
        # Use the vault name as the vault root (matches Path(vault_root).name logic).
        plan = apply(str(tmp_report), str(tmp_vault), dry_run=True)

        assert plan["applied"] is False
        assert APPROVE_ID in plan["approved"]
        assert any(r["id"] == REJECT_ID for r in plan["rejected"])
        assert CONFLICT_ID in plan["conflicts"]

    def test_dry_run_does_not_mutate_index(self, tmp_vault, tmp_report, monkeypatch):
        """apply(dry_run=True) must NOT change the ingest_index on disk."""
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))

        index_path = tmp_vault / "meta" / "ingest_index.json"
        before = json.loads(index_path.read_text())

        apply(str(tmp_report), str(tmp_vault), dry_run=True)

        after = json.loads(index_path.read_text())
        assert before == after, "Dry-run must not write to the ingest_index"

    def test_dry_run_reject_reason_in_plan(self, tmp_vault, tmp_report, monkeypatch):
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
        plan = apply(str(tmp_report), str(tmp_vault), dry_run=True)
        rej_entry = next(r for r in plan["rejected"] if r["id"] == REJECT_ID)
        assert "GAP-03" in rej_entry["reason"]


# ---------------------------------------------------------------------------
# 3. apply(dry_run=False)
# ---------------------------------------------------------------------------

class TestApplyReal:

    def test_approve_flips_to_pending(self, tmp_vault, tmp_report, monkeypatch):
        """Approved id: waiting_approval -> pending."""
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
        apply(str(tmp_report), str(tmp_vault), dry_run=False)

        data = json.loads((tmp_vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"][APPROVE_ID]["status"] == "pending"

    def test_reject_flips_to_rejected_with_reason(self, tmp_vault, tmp_report, monkeypatch):
        """Rejected id: waiting_approval -> rejected with rejection_reason set."""
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
        apply(str(tmp_report), str(tmp_vault), dry_run=False)

        data = json.loads((tmp_vault / "meta" / "ingest_index.json").read_text())
        row = data["sources"][REJECT_ID]
        assert row["status"] == "rejected"
        assert "GAP-03" in row["rejection_reason"]

    def test_conflict_is_skipped(self, tmp_vault, tmp_report, monkeypatch):
        """Conflict id must remain waiting_approval (never approved or rejected)."""
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
        apply(str(tmp_report), str(tmp_vault), dry_run=False)

        data = json.loads((tmp_vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"][CONFLICT_ID]["status"] == "waiting_approval"

    def test_applied_flag_is_true(self, tmp_vault, tmp_report, monkeypatch):
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
        plan = apply(str(tmp_report), str(tmp_vault), dry_run=False)
        assert plan["applied"] is True

    def test_missing_report_returns_empty_plan(self, tmp_vault, monkeypatch):
        """apply() on a non-existent report path must not raise."""
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
        plan = apply("/nonexistent/path/report.md", str(tmp_vault), dry_run=False)
        assert plan["approved"] == []
        assert plan["rejected"] == []
        assert plan["conflicts"] == []
        assert plan["applied"] is False


# ---------------------------------------------------------------------------
# 4. Edge / malformed inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_only_decision_trace_section(self):
        """A report with only a Decision trace section returns no decisions."""
        md = "## Decision trace\n\n- [x] approve · `arxiv:1234.00001`\n"
        results = parse_report(md)
        assert results == []

    def test_mixed_case_action_keyword(self):
        """Action keyword matching is case-insensitive."""
        md = "- [x] APPROVE · `test:abc`\n"
        results = parse_report(md)
        assert len(results) == 1
        assert results[0]["action"] == "approve"

    def test_extra_indent_on_checkbox(self):
        """Deeply indented checkbox lines are still parsed."""
        md = "      - [x] reject · `test:deeply-indented`\n    - reason: too old\n"
        results = parse_report(md)
        assert results[0]["id"] == "test:deeply-indented"
        assert results[0]["reason"] == "too old"

    def test_period_separator_accepted(self):
        """The separator between action and id can be a period as well as a bullet."""
        md = "- [x] approve . `test:period-sep`\n"
        results = parse_report(md)
        assert len(results) == 1
        assert results[0]["id"] == "test:period-sep"

    def test_no_crash_on_none_like_content(self):
        """Unicode / special chars in reason do not raise."""
        md = "- [x] reject · `test:unicode`\n    - reason: café reason\n"
        results = parse_report(md)
        assert results[0]["reason"] == "café reason"
