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
  5. parse_candidates -- classifies approve/reject/conflict/deferred; block_text captured.
  6. report-mode apply -- deferred -> backlog.md with search-date; dedupe; dry_run no-op.
  7. backlog-mode apply -- approve/reject drop from backlog; deferred kept; rewrite.
  8. search-date sourced from frontmatter date: and filename fallback.

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

from scripts.report_approve import parse_report, parse_candidates, apply  # noqa: E402
from agents import ingest_index as ii                                       # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# The four-block report fixture mirrors the pinned format exactly.
# Block A: arxiv:2606.08635v1 -> approve ticked
# Block B: arxiv:2606.11111v1 -> reject ticked with reason
# Block C: arxiv:2606.22222v1 -> BOTH ticked (conflict)
# Block D: arxiv:2606.33333v1 -> untouched (no tick) => DEFERRED
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
- **url**: https://arxiv.org/abs/2606.33333

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
    The APPROVE_ID row is given a valid https URL so it passes the approve gate.
    The REJECT_ID and CONFLICT_ID rows have no URL (url gate only applies to approve).
    Also seeds UNTOUCHED_ID (deferred) with a valid URL so backlog tests work.
    Returns the vault root path.
    """
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)

    data = {
        "version": 3,
        "vault": tmp_path.name,
        "sources": {},
    }
    # URL map: APPROVE_ID gets a real https URL; others stay empty.
    _urls = {
        APPROVE_ID: "https://arxiv.org/abs/2606.08635",
        REJECT_ID: "",
        CONFLICT_ID: "",
        UNTOUCHED_ID: "https://arxiv.org/abs/2606.33333",
    }
    for ident in (APPROVE_ID, REJECT_ID, CONFLICT_ID, UNTOUCHED_ID):
        data["sources"][ident] = {
            "id": ident,
            "id_type": "arxiv",
            "status": "waiting_approval",
            "title": f"Test source {ident}",
            "filename": "",
            "url": _urls[ident],
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

    def test_missing_report_returns_blocked_key(self, tmp_vault, monkeypatch):
        """apply() on a non-existent report must include 'blocked' in the returned dict."""
        monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
        plan = apply("/nonexistent/path/report.md", str(tmp_vault), dry_run=False)
        assert "blocked" in plan
        assert plan["blocked"] == []

    def test_report_with_leading_learning_briefing_parses_correctly(self):
        """A report with a ## Learning briefing section before the candidates still parses.

        The briefing section is a pure informational block that must not confuse
        the checkbox parser. parse_report must return the same decisions as without it.
        """
        briefing_prefix = (
            "## Learning briefing (applied from 2026-06-23 crawl)\n\n"
            "**Sources updated:**\n"
            "- `arxiv_cs_dc`: rep=+0.500 (new) | accept=3, reject=0\n\n"
            "_3 new decision(s) processed._\n\n"
        )
        report_with_briefing = (
            "# Nightly Report 2026-06-24\n\n"
            + briefing_prefix
            + "### 1. Some Paper\n"
            "- [x] approve \xb7 `arxiv:2606.08635v1`\n"
            "- [ ] reject \xb7 `arxiv:2606.08635v1`\n"
            "    - reason:\n"
            "- **url**: https://arxiv.org/abs/2606.08635\n\n"
            "### 2. Another Paper\n"
            "- [ ] approve \xb7 `arxiv:2606.11111v1`\n"
            "- [x] reject \xb7 `arxiv:2606.11111v1`\n"
            "    - reason: not relevant\n"
            "- **url**: https://arxiv.org/abs/2606.11111\n\n"
            "## Decision trace\n\n"
            "- [x] approve \xb7 `arxiv:9999.99999v1`\n"
        )
        results = parse_report(report_with_briefing)
        ids = {r["id"]: r["action"] for r in results}

        # Approve + reject inside the candidate blocks must be found
        assert "arxiv:2606.08635v1" in ids, "Approved id must be found"
        assert ids["arxiv:2606.08635v1"] == "approve"
        assert "arxiv:2606.11111v1" in ids, "Rejected id must be found"
        assert ids["arxiv:2606.11111v1"] == "reject"

        # Stray approve inside ## Decision trace must be IGNORED
        assert "arxiv:9999.99999v1" not in ids, (
            "Id inside ## Decision trace must be ignored"
        )

        # No phantom entries from the briefing section itself
        for r in results:
            assert "arxiv_cs_dc" not in r["id"], (
                "Source id mentioned in briefing section must not appear as a decision"
            )


# ---------------------------------------------------------------------------
# 5. URL gate in apply() -- approve blocked without a valid URL
# ---------------------------------------------------------------------------

def _make_url_gate_vault(tmp_path: Path, url_for_approve: str = "") -> Path:
    """Vault with two waiting_approval rows: one with a URL, one without.

    APPROVE_WITH_URL_ID  -> has a valid https URL (should be approved).
    APPROVE_NO_URL_ID    -> has no URL (should be blocked).
    REJECT_NO_URL_ID     -> no URL; reject action (reject is unaffected by gate).
    """
    meta = tmp_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)

    def _row(ident, url=""):
        return {
            "id": ident,
            "id_type": "arxiv",
            "status": "waiting_approval",
            "title": f"Test {ident}",
            "filename": "",
            "url": url,
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

    data = {
        "version": 3,
        "vault": tmp_path.name,
        "sources": {
            "test:with-url": _row("test:with-url", url="https://arxiv.org/abs/2606.08635"),
            "test:no-url": _row("test:no-url", url=""),
            "test:reject-no-url": _row("test:reject-no-url", url=""),
        },
    }
    (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))
    return tmp_path


_URL_GATE_REPORT = """\
# Nightly Report URL Gate Test

## Candidates

### 1. Source with valid URL
- [x] approve · `test:with-url`
- [ ] reject · `test:with-url`
    - reason:

### 2. Source with no URL
- [x] approve · `test:no-url`
- [ ] reject · `test:no-url`
    - reason:

### 3. Reject without URL (should work fine)
- [ ] approve · `test:reject-no-url`
- [x] reject · `test:reject-no-url`
    - reason: not relevant
"""


class TestUrlGate:

    @pytest.fixture()
    def url_gate_vault(self, tmp_path):
        return _make_url_gate_vault(tmp_path)

    @pytest.fixture()
    def url_gate_report(self, tmp_path):
        p = tmp_path / "url_gate_report.md"
        p.write_text(_URL_GATE_REPORT, encoding="utf-8")
        return p

    def test_approve_with_valid_url_is_approved_dry_run(
        self, url_gate_vault, url_gate_report, monkeypatch
    ):
        """Ticked-approve with a valid URL appears in 'approved' on dry-run."""
        monkeypatch.setenv("VAULT_PATH", str(url_gate_vault))
        plan = apply(str(url_gate_report), str(url_gate_vault), dry_run=True)

        assert "test:with-url" in plan["approved"]

    def test_approve_without_url_is_blocked_dry_run(
        self, url_gate_vault, url_gate_report, monkeypatch
    ):
        """Ticked-approve with no URL appears in 'blocked' with reason 'no working URL' on dry-run."""
        monkeypatch.setenv("VAULT_PATH", str(url_gate_vault))
        plan = apply(str(url_gate_report), str(url_gate_vault), dry_run=True)

        assert "test:no-url" not in plan["approved"]
        assert any(b["id"] == "test:no-url" for b in plan["blocked"])
        blocked_entry = next(b for b in plan["blocked"] if b["id"] == "test:no-url")
        assert blocked_entry["reason"] == "no working URL"

    def test_approve_without_url_not_mutated_dry_run(
        self, url_gate_vault, url_gate_report, monkeypatch
    ):
        """The index is NOT mutated for a blocked approve row on dry-run."""
        monkeypatch.setenv("VAULT_PATH", str(url_gate_vault))
        index_path = url_gate_vault / "meta" / "ingest_index.json"
        before = json.loads(index_path.read_text())

        apply(str(url_gate_report), str(url_gate_vault), dry_run=True)

        after = json.loads(index_path.read_text())
        assert before == after, "Dry-run must not write the index at all"

    def test_approve_with_valid_url_flips_to_pending_apply(
        self, url_gate_vault, url_gate_report, monkeypatch
    ):
        """Ticked-approve with URL: waiting_approval -> pending on --apply."""
        monkeypatch.setenv("VAULT_PATH", str(url_gate_vault))
        apply(str(url_gate_report), str(url_gate_vault), dry_run=False)

        data = json.loads((url_gate_vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"]["test:with-url"]["status"] == "pending"

    def test_approve_without_url_stays_waiting_approval_apply(
        self, url_gate_vault, url_gate_report, monkeypatch
    ):
        """Ticked-approve with no URL: status stays waiting_approval on --apply."""
        monkeypatch.setenv("VAULT_PATH", str(url_gate_vault))
        apply(str(url_gate_report), str(url_gate_vault), dry_run=False)

        data = json.loads((url_gate_vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"]["test:no-url"]["status"] == "waiting_approval", (
            "A blocked approve must NOT change the row status"
        )

    def test_reject_without_url_succeeds(
        self, url_gate_vault, url_gate_report, monkeypatch
    ):
        """Ticked-reject with no URL: reject is unaffected by the URL gate -> rejected."""
        monkeypatch.setenv("VAULT_PATH", str(url_gate_vault))
        apply(str(url_gate_report), str(url_gate_vault), dry_run=False)

        data = json.loads((url_gate_vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"]["test:reject-no-url"]["status"] == "rejected"

    def test_blocked_in_plan_returned_key(
        self, url_gate_vault, url_gate_report, monkeypatch
    ):
        """The 'blocked' key is always present in the returned plan."""
        monkeypatch.setenv("VAULT_PATH", str(url_gate_vault))
        plan = apply(str(url_gate_report), str(url_gate_vault), dry_run=True)
        assert "blocked" in plan

    def test_applied_true_with_url_gate(
        self, url_gate_vault, url_gate_report, monkeypatch
    ):
        """applied=True on --apply even when some rows are blocked."""
        monkeypatch.setenv("VAULT_PATH", str(url_gate_vault))
        plan = apply(str(url_gate_report), str(url_gate_vault), dry_run=False)
        assert plan["applied"] is True


# ---------------------------------------------------------------------------
# 6. parse_candidates -- new function (classifies ALL blocks incl. deferred)
# ---------------------------------------------------------------------------

# 4-block fixture: approve / reject+reason / conflict / deferred
# Same structure as REPORT_MD but explicit for parse_candidates tests.
_CANDIDATES_MD = """\
### 1. Approved Paper
- [x] approve · `cand:approve-only`
- [ ] reject · `cand:approve-only`
    - reason:
- **url**: https://example.com/paper1

**Summary** -- Great paper.

### 2. Rejected Paper
- [ ] approve · `cand:reject-only`
- [x] reject · `cand:reject-only`
    - reason: Out of scope for this vault
- **url**: https://example.com/paper2

**Relevance** -- Not relevant.

### 3. Conflicted Paper
- [x] approve · `cand:both-ticked`
- [x] reject · `cand:both-ticked`
    - reason: Changed mind after approving
- **url**: https://example.com/paper3

### 4. Deferred Paper (neither ticked)
- [ ] approve · `cand:deferred`
- [ ] reject · `cand:deferred`
    - reason:
- **url**: https://example.com/paper4

## Decision trace

- [x] approve · `trace:should-be-ignored`
"""

_CAND_APPROVE = "cand:approve-only"
_CAND_REJECT = "cand:reject-only"
_CAND_CONFLICT = "cand:both-ticked"
_CAND_DEFERRED = "cand:deferred"
_CAND_TRACE = "trace:should-be-ignored"


class TestParseCandidates:

    def test_returns_four_entries(self):
        """All four candidate blocks are returned (including deferred)."""
        results = parse_candidates(_CANDIDATES_MD)
        ids = [r["id"] for r in results]
        assert len(results) == 4
        assert _CAND_APPROVE in ids
        assert _CAND_REJECT in ids
        assert _CAND_CONFLICT in ids
        assert _CAND_DEFERRED in ids

    def test_approve_action(self):
        results = parse_candidates(_CANDIDATES_MD)
        by_id = {r["id"]: r for r in results}
        assert by_id[_CAND_APPROVE]["action"] == "approve"
        assert by_id[_CAND_APPROVE]["reason"] == ""

    def test_reject_action_with_reason(self):
        results = parse_candidates(_CANDIDATES_MD)
        by_id = {r["id"]: r for r in results}
        assert by_id[_CAND_REJECT]["action"] == "reject"
        assert by_id[_CAND_REJECT]["reason"] == "Out of scope for this vault"

    def test_conflict_action(self):
        results = parse_candidates(_CANDIDATES_MD)
        by_id = {r["id"]: r for r in results}
        assert by_id[_CAND_CONFLICT]["action"] == "conflict"

    def test_deferred_action(self):
        """Neither box ticked -> action='deferred'."""
        results = parse_candidates(_CANDIDATES_MD)
        by_id = {r["id"]: r for r in results}
        assert by_id[_CAND_DEFERRED]["action"] == "deferred"

    def test_decision_trace_stops_parsing(self):
        """Candidates after ## Decision trace are NOT returned."""
        results = parse_candidates(_CANDIDATES_MD)
        ids = [r["id"] for r in results]
        assert _CAND_TRACE not in ids

    def test_block_text_captured_for_approve(self):
        """block_text for the approve candidate contains its ### heading and url line."""
        results = parse_candidates(_CANDIDATES_MD)
        by_id = {r["id"]: r for r in results}
        bt = by_id[_CAND_APPROVE]["block_text"]
        assert "### 1. Approved Paper" in bt
        assert "https://example.com/paper1" in bt

    def test_block_text_captured_for_deferred(self):
        """block_text for the deferred candidate contains its ### heading."""
        results = parse_candidates(_CANDIDATES_MD)
        by_id = {r["id"]: r for r in results}
        bt = by_id[_CAND_DEFERRED]["block_text"]
        assert "### 4. Deferred Paper" in bt
        assert "https://example.com/paper4" in bt

    def test_block_text_does_not_bleed_into_next_block(self):
        """block_text must not contain lines from the following block."""
        results = parse_candidates(_CANDIDATES_MD)
        by_id = {r["id"]: r for r in results}
        # Block 1 (approve) must NOT contain block 2's url.
        assert "paper2" not in by_id[_CAND_APPROVE]["block_text"]

    def test_block_text_stops_at_decision_trace(self):
        """The last block's block_text must not include the ## Decision trace heading."""
        results = parse_candidates(_CANDIDATES_MD)
        by_id = {r["id"]: r for r in results}
        assert "## Decision trace" not in by_id[_CAND_DEFERRED]["block_text"]

    def test_parse_report_filters_deferred(self):
        """parse_report returns only ticked entries (no deferred)."""
        report_results = parse_report(_CANDIDATES_MD)
        ids = [r["id"] for r in report_results]
        assert _CAND_DEFERRED not in ids
        assert _CAND_APPROVE in ids
        assert _CAND_REJECT in ids
        assert _CAND_CONFLICT in ids

    def test_empty_md_returns_empty(self):
        assert parse_candidates("") == []

    def test_only_decision_trace_returns_empty(self):
        md = "## Decision trace\n\n### 1. Phantom\n- [x] approve · `ph:1`\n"
        results = parse_candidates(md)
        assert results == []


# ---------------------------------------------------------------------------
# 7. report-mode apply -- deferred -> backlog.md
# ---------------------------------------------------------------------------

# Report with frontmatter date and one deferred block.
_REPORT_WITH_FM = """\
---
date: 2026-06-22
title: Nightly Report
---

## Candidates

### 1. Approved Source
- [x] approve · `good:src1`
- [ ] reject · `good:src1`
    - reason:
- **url**: https://arxiv.org/abs/2606.08635

### 2. Deferred Source
- [ ] approve · `deferred:src2`
- [ ] reject · `deferred:src2`
    - reason:
- **url**: https://arxiv.org/abs/2606.11111

## Decision trace
"""

_APPROVED_SRC = "good:src1"
_DEFERRED_SRC = "deferred:src2"


def _make_report_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Create a vault + report file for report-mode tests.

    Returns (vault_root, report_path).
    """
    meta = tmp_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    nightly = meta / "nightly_report"
    nightly.mkdir(parents=True, exist_ok=True)

    def _row(ident, url=""):
        return {
            "id": ident,
            "id_type": "test",
            "status": "waiting_approval",
            "title": f"Test {ident}",
            "filename": "",
            "url": url,
            "source_type": "arxiv",
            "content_hash": "",
            "first_seen": "2026-06-22T00:00:00+00:00",
            "ingested_at": None,
            "source_page": "",
            "relevance_score": None,
            "rationale": "",
            "discovered_by": "",
            "objective_ids": [],
            "proposed_at": "2026-06-22T00:00:00+00:00",
            "rejected_at": None,
            "rejection_reason": "",
            "published": None,
        }

    data = {
        "version": 3,
        "vault": tmp_path.name,
        "sources": {
            _APPROVED_SRC: _row(_APPROVED_SRC, url="https://arxiv.org/abs/2606.08635"),
            _DEFERRED_SRC: _row(_DEFERRED_SRC, url="https://arxiv.org/abs/2606.11111"),
        },
    }
    (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))

    report_path = nightly / "2026-06-22.md"
    report_path.write_text(_REPORT_WITH_FM, encoding="utf-8")
    return tmp_path, report_path


class TestReportModeBacklog:

    @pytest.fixture()
    def report_vault(self, tmp_path):
        return _make_report_vault(tmp_path)

    def test_dry_run_does_not_write_backlog(self, report_vault, monkeypatch):
        """dry_run=True must NOT create backlog.md."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(report), str(vault), dry_run=True, mode="report")
        backlog = vault / "meta" / "nightly_report" / "backlog.md"
        assert not backlog.exists(), "dry_run must not write backlog.md"

    def test_deferred_ids_in_plan(self, report_vault, monkeypatch):
        """apply(dry_run=True) returns the deferred id in the 'deferred' list."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        plan = apply(str(report), str(vault), dry_run=True, mode="report")
        assert _DEFERRED_SRC in plan["deferred"]

    def test_backlog_created_with_search_date(self, report_vault, monkeypatch):
        """apply(dry_run=False) creates backlog.md with the deferred block + search-date."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        plan = apply(str(report), str(vault), dry_run=False, mode="report")

        backlog = vault / "meta" / "nightly_report" / "backlog.md"
        assert backlog.exists(), "backlog.md must be created"
        text = backlog.read_text(encoding="utf-8")
        assert _DEFERRED_SRC in text, "deferred id must appear in backlog.md"
        assert "**search-date**: 2026-06-22" in text, "search-date must be written"

    def test_backlogged_list_contains_id(self, report_vault, monkeypatch):
        """The 'backlogged' key in the plan contains the newly written id."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        plan = apply(str(report), str(vault), dry_run=False, mode="report")
        assert _DEFERRED_SRC in plan["backlogged"]

    def test_deferred_stays_waiting_approval(self, report_vault, monkeypatch):
        """Deferred candidate must NOT have its ingest_index status changed."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(report), str(vault), dry_run=False, mode="report")
        data = json.loads((vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"][_DEFERRED_SRC]["status"] == "waiting_approval"

    def test_approved_still_flips_to_pending(self, report_vault, monkeypatch):
        """In report mode, approved sources still flip to pending."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(report), str(vault), dry_run=False, mode="report")
        data = json.loads((vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"][_APPROVED_SRC]["status"] == "pending"

    def test_no_duplicate_on_rerun(self, report_vault, monkeypatch):
        """Running apply twice does NOT duplicate the deferred block in backlog.md."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(report), str(vault), dry_run=False, mode="report")
        apply(str(report), str(vault), dry_run=False, mode="report")

        backlog = vault / "meta" / "nightly_report" / "backlog.md"
        text = backlog.read_text(encoding="utf-8")
        # Count occurrences of the id.
        assert text.count(_DEFERRED_SRC) == 2, (
            "Id appears exactly twice (one approve checkbox, one reject checkbox), "
            "never duplicated by a second run"
        )

    def test_rerun_backlogged_empty_on_second_run(self, report_vault, monkeypatch):
        """Second run: backlogged list is empty (id already in backlog)."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(report), str(vault), dry_run=False, mode="report")
        plan2 = apply(str(report), str(vault), dry_run=False, mode="report")
        assert plan2["backlogged"] == []

    def test_search_date_from_frontmatter(self, report_vault, monkeypatch):
        """Search-date is read from the report frontmatter date: field."""
        vault, report = report_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(report), str(vault), dry_run=False, mode="report")
        backlog = vault / "meta" / "nightly_report" / "backlog.md"
        text = backlog.read_text(encoding="utf-8")
        # Frontmatter says date: 2026-06-22; filename stem is also 2026-06-22 -> same.
        assert "**search-date**: 2026-06-22" in text

    def test_search_date_from_filename_fallback(self, tmp_path, monkeypatch):
        """When frontmatter has no date: field, fall back to filename stem."""
        vault = tmp_path / "vault"
        meta = vault / "meta"
        nightly = meta / "nightly_report"
        nightly.mkdir(parents=True, exist_ok=True)

        def _row(ident, url=""):
            return {
                "id": ident, "id_type": "test", "status": "waiting_approval",
                "title": f"Test {ident}", "filename": "", "url": url,
                "source_type": "arxiv", "content_hash": "",
                "first_seen": "2026-06-15T00:00:00+00:00", "ingested_at": None,
                "source_page": "", "relevance_score": None, "rationale": "",
                "discovered_by": "", "objective_ids": [],
                "proposed_at": "2026-06-15T00:00:00+00:00", "rejected_at": None,
                "rejection_reason": "", "published": None,
            }

        data = {
            "version": 3, "vault": vault.name,
            "sources": {"fn:deferred": _row("fn:deferred", url="https://example.com")},
        }
        (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))

        # Report with NO frontmatter date; filename stem = 2026-06-15.
        report_no_fm = nightly / "2026-06-15.md"
        report_no_fm.write_text(
            "## Candidates\n\n"
            "### 1. Fallback Test\n"
            "- [ ] approve · `fn:deferred`\n"
            "- [ ] reject · `fn:deferred`\n"
            "    - reason:\n"
            "- **url**: https://example.com\n\n"
            "## Decision trace\n",
            encoding="utf-8",
        )

        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(report_no_fm), str(vault), dry_run=False, mode="report")
        backlog = nightly / "backlog.md"
        assert backlog.exists()
        text = backlog.read_text(encoding="utf-8")
        assert "**search-date**: 2026-06-15" in text


# ---------------------------------------------------------------------------
# 8. backlog-mode apply
# ---------------------------------------------------------------------------

# Backlog with one deferred block (not yet decided) and one block about to be approved.
_BACKLOG_MD = """\
---
type: nightly_backlog
written_by: web
updated: 2026-06-22
---

<!-- Items here are undecided (deferred) candidates from nightly reports.
     Review with: python scripts/report_approve.py --mode backlog --vault <V> [--apply]
     Tick approve or reject for each item, then re-run with --apply. -->

### 1. Source to Approve
- [x] approve · `bl:approve-me`
- [ ] reject · `bl:approve-me`
    - reason:
- **url**: https://arxiv.org/abs/2606.08635
- **search-date**: 2026-06-20

### 2. Source to Reject
- [ ] approve · `bl:reject-me`
- [x] reject · `bl:reject-me`
    - reason: Stale content
- **url**: https://example.com/stale
- **search-date**: 2026-06-19

### 3. Still Undecided
- [ ] approve · `bl:still-waiting`
- [ ] reject · `bl:still-waiting`
    - reason:
- **url**: https://example.com/waiting
- **search-date**: 2026-06-18
"""

_BL_APPROVE = "bl:approve-me"
_BL_REJECT = "bl:reject-me"
_BL_WAIT = "bl:still-waiting"


def _make_backlog_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Create a vault + backlog.md for backlog-mode tests.

    Returns (vault_root, backlog_path).
    """
    meta = tmp_path / "meta"
    nightly = meta / "nightly_report"
    nightly.mkdir(parents=True, exist_ok=True)

    def _row(ident, url=""):
        return {
            "id": ident, "id_type": "test", "status": "waiting_approval",
            "title": f"Test {ident}", "filename": "", "url": url,
            "source_type": "arxiv", "content_hash": "",
            "first_seen": "2026-06-18T00:00:00+00:00", "ingested_at": None,
            "source_page": "", "relevance_score": None, "rationale": "",
            "discovered_by": "", "objective_ids": [],
            "proposed_at": "2026-06-18T00:00:00+00:00", "rejected_at": None,
            "rejection_reason": "", "published": None,
        }

    data = {
        "version": 3, "vault": tmp_path.name,
        "sources": {
            _BL_APPROVE: _row(_BL_APPROVE, url="https://arxiv.org/abs/2606.08635"),
            _BL_REJECT: _row(_BL_REJECT, url=""),
            _BL_WAIT: _row(_BL_WAIT, url="https://example.com/waiting"),
        },
    }
    (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))

    backlog_path = nightly / "backlog.md"
    backlog_path.write_text(_BACKLOG_MD, encoding="utf-8")
    return tmp_path, backlog_path


class TestBacklogMode:

    @pytest.fixture()
    def backlog_vault(self, tmp_path):
        return _make_backlog_vault(tmp_path)

    def test_dry_run_returns_plan_no_writes(self, backlog_vault, monkeypatch):
        """dry_run=True in backlog mode: returns plan, no file or index writes."""
        vault, backlog = backlog_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        index_before = (vault / "meta" / "ingest_index.json").read_text()
        backlog_before = backlog.read_text(encoding="utf-8")

        plan = apply(str(backlog), str(vault), dry_run=True, mode="backlog")

        assert plan["applied"] is False
        assert (vault / "meta" / "ingest_index.json").read_text() == index_before
        assert backlog.read_text(encoding="utf-8") == backlog_before

    def test_plan_contains_approve_reject_remaining(self, backlog_vault, monkeypatch):
        """dry_run plan correctly classifies approve/reject/remaining."""
        vault, backlog = backlog_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        plan = apply(str(backlog), str(vault), dry_run=True, mode="backlog")

        assert _BL_APPROVE in plan["approved"]
        assert any(r["id"] == _BL_REJECT for r in plan["rejected"])
        assert _BL_WAIT in plan["remaining"]

    def test_approve_flips_to_pending(self, backlog_vault, monkeypatch):
        """Backlog-mode --apply: approved source flips to pending."""
        vault, backlog = backlog_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(backlog), str(vault), dry_run=False, mode="backlog")

        data = json.loads((vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"][_BL_APPROVE]["status"] == "pending"

    def test_reject_flips_to_rejected(self, backlog_vault, monkeypatch):
        """Backlog-mode --apply: rejected source flips to rejected."""
        vault, backlog = backlog_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(backlog), str(vault), dry_run=False, mode="backlog")

        data = json.loads((vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"][_BL_REJECT]["status"] == "rejected"
        assert "Stale content" in data["sources"][_BL_REJECT]["rejection_reason"]

    def test_deferred_stays_waiting_approval(self, backlog_vault, monkeypatch):
        """Deferred (still-undecided) source must remain waiting_approval."""
        vault, backlog = backlog_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(backlog), str(vault), dry_run=False, mode="backlog")

        data = json.loads((vault / "meta" / "ingest_index.json").read_text())
        assert data["sources"][_BL_WAIT]["status"] == "waiting_approval"

    def test_backlog_rewritten_only_kept_blocks(self, backlog_vault, monkeypatch):
        """After --apply, backlog.md contains only the still-deferred block."""
        vault, backlog = backlog_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(backlog), str(vault), dry_run=False, mode="backlog")

        text = backlog.read_text(encoding="utf-8")
        # Approved and rejected blocks removed.
        assert _BL_APPROVE not in text, "Approved block must be dropped from backlog"
        assert _BL_REJECT not in text, "Rejected block must be dropped from backlog"
        # Remaining block kept.
        assert _BL_WAIT in text, "Still-deferred block must be kept in backlog"

    def test_search_date_preserved_in_kept_block(self, backlog_vault, monkeypatch):
        """The search-date bullet of the kept block must be preserved after rewrite."""
        vault, backlog = backlog_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        apply(str(backlog), str(vault), dry_run=False, mode="backlog")

        text = backlog.read_text(encoding="utf-8")
        assert "**search-date**: 2026-06-18" in text

    def test_url_gate_blocks_approve_in_backlog(self, tmp_path, monkeypatch):
        """Backlog-mode approve without a URL goes to 'blocked' and stays in backlog."""
        vault = tmp_path
        meta = vault / "meta"
        nightly = meta / "nightly_report"
        nightly.mkdir(parents=True, exist_ok=True)

        # Seed a source with no URL.
        data = {
            "version": 3, "vault": vault.name,
            "sources": {
                "nourl:src": {
                    "id": "nourl:src", "id_type": "test", "status": "waiting_approval",
                    "title": "No URL", "filename": "", "url": "",
                    "source_type": "arxiv", "content_hash": "",
                    "first_seen": "2026-06-18T00:00:00+00:00", "ingested_at": None,
                    "source_page": "", "relevance_score": None, "rationale": "",
                    "discovered_by": "", "objective_ids": [],
                    "proposed_at": "2026-06-18T00:00:00+00:00", "rejected_at": None,
                    "rejection_reason": "", "published": None,
                }
            },
        }
        (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))

        backlog_content = """\
---
type: nightly_backlog
written_by: web
updated: 2026-06-22
---

<!-- Items here are undecided (deferred) candidates from nightly reports.
     Review with: python scripts/report_approve.py --mode backlog --vault <V> [--apply]
     Tick approve or reject for each item, then re-run with --apply. -->

### 1. No-URL Source
- [x] approve · `nourl:src`
- [ ] reject · `nourl:src`
    - reason:
- **search-date**: 2026-06-18
"""
        backlog_path = nightly / "backlog.md"
        backlog_path.write_text(backlog_content, encoding="utf-8")

        monkeypatch.setenv("VAULT_PATH", str(vault))
        plan = apply(str(backlog_path), str(vault), dry_run=False, mode="backlog")

        # Should be in blocked, not approved.
        assert any(b["id"] == "nourl:src" for b in plan["blocked"])
        assert "nourl:src" not in plan["approved"]
        # Source must stay waiting_approval.
        data_after = json.loads((meta / "ingest_index.json").read_text())
        assert data_after["sources"]["nourl:src"]["status"] == "waiting_approval"
        # Block must remain in backlog.
        text = backlog_path.read_text(encoding="utf-8")
        assert "nourl:src" in text

    def test_mode_key_in_returned_plan(self, backlog_vault, monkeypatch):
        """Plan dict always includes 'mode': 'backlog'."""
        vault, backlog = backlog_vault
        monkeypatch.setenv("VAULT_PATH", str(vault))
        plan = apply(str(backlog), str(vault), dry_run=True, mode="backlog")
        assert plan["mode"] == "backlog"


# ---------------------------------------------------------------------------
# 9. Live-vault persistence: status flips persist to disk regardless of env vars.
#    This is the regression test for the bug where a registered named vault with
#    VAULT=<name> in the environment could cause load/save to diverge so the flip
#    was silently discarded.
# ---------------------------------------------------------------------------

# Report with one approve (has URL) and one reject.
_LIVE_REPORT_MD = """\
---
date: 2026-06-23
title: Live Vault Test
---

## Candidates

### 1. Source to Approve
- [x] approve · `live:approve-me`
- [ ] reject · `live:approve-me`
    - reason:
- **url**: https://arxiv.org/abs/2606.08635

### 2. Source to Reject
- [ ] approve · `live:reject-me`
- [x] reject · `live:reject-me`
    - reason: Stale, out-of-scope content
- **url**: https://example.com/stale

## Decision trace
"""

_LIVE_APPROVE = "live:approve-me"
_LIVE_REJECT = "live:reject-me"


def _make_live_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Simulate a registered named vault: vault dir with meta/ingest_index.json.

    Returns (vault_root, report_path).
    The vault dir name is set to 'My-Vault' to look like a registered named vault.
    """
    vault = tmp_path / "My-Vault"
    meta = vault / "meta"
    nightly = meta / "nightly_report"
    nightly.mkdir(parents=True, exist_ok=True)

    def _row(ident, url=""):
        return {
            "id": ident,
            "id_type": "test",
            "status": "waiting_approval",
            "title": f"Test {ident}",
            "filename": "",
            "url": url,
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

    data = {
        "version": 3,
        "vault": "My-Vault",
        "sources": {
            _LIVE_APPROVE: _row(_LIVE_APPROVE, url="https://arxiv.org/abs/2606.08635"),
            _LIVE_REJECT: _row(_LIVE_REJECT, url="https://example.com/stale"),
        },
    }
    (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))

    report_path = nightly / "2026-06-23.md"
    report_path.write_text(_LIVE_REPORT_MD, encoding="utf-8")
    return vault, report_path


class TestLiveVaultPersistence:
    """Verify that approve/reject status flips persist to the correct file on disk
    even when VAULT or VAULT_PATH env vars are set to something else (the live-vault
    regression).  The fix uses root=Path(vault_root) in all ingest_index calls so
    env vars are irrelevant."""

    @pytest.fixture()
    def live_vault(self, tmp_path):
        return _make_live_vault(tmp_path)

    def _reload_index(self, vault: Path) -> dict:
        """Read meta/ingest_index.json from vault_root/meta/ingest_index.json."""
        return json.loads((vault / "meta" / "ingest_index.json").read_text())

    # --- report mode: approve persists ---

    def test_report_approve_persists_no_env_set(self, live_vault):
        """Approve flip persists when neither VAULT nor VAULT_PATH is in the env."""
        vault, report = live_vault
        # No monkeypatch -- env is clean.
        plan = apply(str(report), str(vault), dry_run=False, mode="report")

        assert _LIVE_APPROVE in plan["approved"], "approved must be in plan"
        data = self._reload_index(vault)
        assert data["sources"][_LIVE_APPROVE]["status"] == "pending", (
            "approve flip must persist to disk"
        )

    def test_report_approve_persists_with_stale_vault_env(self, live_vault, monkeypatch):
        """Approve flip persists even when VAULT is set to a different (stale) name."""
        vault, report = live_vault
        # Simulate the live wiki-agent environment: VAULT points to a registered vault
        # name that differs from the tmp vault being tested.  The old env-var approach
        # would have tried to load from the registered vault_path instead of vault_root.
        monkeypatch.setenv("VAULT", "LLM-Inference")
        monkeypatch.delenv("VAULT_PATH", raising=False)

        plan = apply(str(report), str(vault), dry_run=False, mode="report")

        assert _LIVE_APPROVE in plan["approved"]
        data = self._reload_index(vault)
        assert data["sources"][_LIVE_APPROVE]["status"] == "pending", (
            "approve must persist despite VAULT=LLM-Inference in env"
        )

    def test_report_approve_persists_with_wrong_vault_path_env(self, live_vault, tmp_path,
                                                                monkeypatch):
        """Approve flip persists even when VAULT_PATH points to a different directory."""
        vault, report = live_vault
        other_dir = tmp_path / "other_vault"
        other_dir.mkdir()
        # VAULT_PATH points somewhere else entirely.
        monkeypatch.setenv("VAULT_PATH", str(other_dir))
        monkeypatch.delenv("VAULT", raising=False)

        plan = apply(str(report), str(vault), dry_run=False, mode="report")

        assert _LIVE_APPROVE in plan["approved"]
        data = self._reload_index(vault)
        assert data["sources"][_LIVE_APPROVE]["status"] == "pending", (
            "approve must persist to vault_root, not VAULT_PATH"
        )

    # --- report mode: reject persists ---

    def test_report_reject_persists_no_env_set(self, live_vault):
        """Reject flip persists when no env vars are set."""
        vault, report = live_vault
        plan = apply(str(report), str(vault), dry_run=False, mode="report")

        assert any(r["id"] == _LIVE_REJECT for r in plan["rejected"])
        data = self._reload_index(vault)
        row = data["sources"][_LIVE_REJECT]
        assert row["status"] == "rejected", "reject flip must persist to disk"
        assert "Stale" in row["rejection_reason"], "rejection_reason must be stored"

    def test_report_reject_persists_with_stale_vault_env(self, live_vault, monkeypatch):
        """Reject flip persists even when VAULT is set to a registered name."""
        vault, report = live_vault
        monkeypatch.setenv("VAULT", "LLM-Inference")
        monkeypatch.delenv("VAULT_PATH", raising=False)

        plan = apply(str(report), str(vault), dry_run=False, mode="report")

        assert any(r["id"] == _LIVE_REJECT for r in plan["rejected"])
        data = self._reload_index(vault)
        assert data["sources"][_LIVE_REJECT]["status"] == "rejected", (
            "reject must persist despite VAULT=LLM-Inference in env"
        )

    # --- backlog mode: approve persists and block removed ---

    _LIVE_BACKLOG_MD = """\
---
type: nightly_backlog
written_by: web
updated: 2026-06-23
---

<!-- Items here are undecided (deferred) candidates from nightly reports.
     Review with: python scripts/report_approve.py --mode backlog --vault <V> [--apply]
     Tick approve or reject for each item, then re-run with --apply. -->

### 1. Live Backlog Source
- [x] approve · `live:bl-approve`
- [ ] reject · `live:bl-approve`
    - reason:
- **url**: https://arxiv.org/abs/2606.08635
- **search-date**: 2026-06-20

### 2. Still Waiting
- [ ] approve · `live:bl-wait`
- [ ] reject · `live:bl-wait`
    - reason:
- **url**: https://example.com/wait
- **search-date**: 2026-06-19
"""

    def _make_backlog_live_vault(self, tmp_path: Path) -> tuple[Path, Path]:
        vault = tmp_path / "My-Vault"
        meta = vault / "meta"
        nightly = meta / "nightly_report"
        nightly.mkdir(parents=True, exist_ok=True)

        def _row(ident, url=""):
            return {
                "id": ident, "id_type": "test", "status": "waiting_approval",
                "title": f"Test {ident}", "filename": "", "url": url,
                "source_type": "arxiv", "content_hash": "",
                "first_seen": "2026-06-20T00:00:00+00:00", "ingested_at": None,
                "source_page": "", "relevance_score": None, "rationale": "",
                "discovered_by": "", "objective_ids": [],
                "proposed_at": "2026-06-20T00:00:00+00:00", "rejected_at": None,
                "rejection_reason": "", "published": None,
            }

        data = {
            "version": 3, "vault": "My-Vault",
            "sources": {
                "live:bl-approve": _row("live:bl-approve",
                                        url="https://arxiv.org/abs/2606.08635"),
                "live:bl-wait": _row("live:bl-wait", url="https://example.com/wait"),
            },
        }
        (meta / "ingest_index.json").write_text(json.dumps(data, indent=2))

        backlog_path = nightly / "backlog.md"
        backlog_path.write_text(self._LIVE_BACKLOG_MD, encoding="utf-8")
        return vault, backlog_path

    def test_backlog_approve_persists_with_stale_vault_env(self, tmp_path, monkeypatch):
        """Backlog-mode approve flip persists even when VAULT env var is set to another name."""
        vault, backlog = self._make_backlog_live_vault(tmp_path)
        monkeypatch.setenv("VAULT", "LLM-Inference")
        monkeypatch.delenv("VAULT_PATH", raising=False)

        plan = apply(str(backlog), str(vault), dry_run=False, mode="backlog")

        assert "live:bl-approve" in plan["approved"]
        data = self._reload_index(vault)
        assert data["sources"]["live:bl-approve"]["status"] == "pending", (
            "backlog approve must persist despite stale VAULT env"
        )
        # The approved block must be removed from backlog.md.
        text = backlog.read_text(encoding="utf-8")
        assert "live:bl-approve" not in text, "approved block must be dropped from backlog"
        assert "live:bl-wait" in text, "waiting block must remain in backlog"
