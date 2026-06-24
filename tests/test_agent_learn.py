#!/usr/bin/env python3
"""Hermetic tests for scripts/agent_learn.py + ingest_index source_id/engine fields.

Tests:
  1. ingest_index.enqueue stores source_id / engine when provided.
  2. Absent source_id / engine default to "".
  3. Re-enqueue WITHOUT source_id / engine does NOT clobber existing values.
  4. learn() reputation math:
       - fresh source (no decisions) -> 0.0 via _rep
       - rejected-only source -> negative but > -1 (smoothed)
       - accepted-only source -> positive
       - mixed source -> between -1 and +1
  5. learn() reject keyword extraction (stopwords/short tokens dropped).
  6. learn() calibration means populated.
  7. learn() processed_ids populated.
  8. Idempotency: second learn() with no new decisions -> n_new == 0, counts unchanged.
  9. rep_for() precedence: source over engine over 0.
  10. reject_penalty() hits on keyword match.
  11. reject_penalty() hits on source rep <= -0.5.
  12. reject_penalty() returns 0.0 when neither condition met.
  13. render_briefing(): non-empty delta lists changed sources.
  14. render_briefing(): zero-ever -> cold-start message.
  15. learn(apply=False) -> does NOT write learned.json (dry-run).
  16. learn(apply=True)  -> writes learned.json.
  17. Guard 1 (freq threshold): single-reason token NOT in keywords; two-reason token IS.
  18. Guard 1: keyword_counts accumulate across runs (1+1 -> 2 -> promoted).
  19. Guard 2 (PURPOSE guard): term in PURPOSE.md never becomes a keyword.
  20. keyword_counts persisted in learned.json; keywords is derived.
  21. Old-schema migration: only "keywords" -> seeded keyword_counts, no crash.
  22. keywords list cap at 50 (highest counts first).

No network, no paid API. $0.
Run:  .venv/bin/python -m pytest tests/test_agent_learn.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import agents.ingest_index as ii
from scripts.agent_learn import (
    _rep,
    learn,
    rep_for,
    reject_penalty,
    render_briefing,
    _extract_keywords,
    _learned_path,
    _derive_keywords,
    _build_protected_terms,
    _load_learned,
)


# ---------------------------------------------------------------------------
# Shared vault-seeding helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault skeleton (meta/ dir, no ingest_index yet)."""
    (tmp_path / "meta").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _seed_ingest_index(tmp_path: Path, rows: list[dict]) -> None:
    """Write rows directly into meta/ingest_index.json bypassing enqueue."""
    (tmp_path / "meta").mkdir(parents=True, exist_ok=True)
    data = {"version": 3, "vault": "TestVault", "sources": {}}
    for row in rows:
        # Ensure all _V3_DEFAULTS are present
        full_row = {
            "id": row["id"],
            "id_type": row.get("id_type", "url"),
            "status": row["status"],
            "title": row.get("title", ""),
            "filename": "",
            "url": row.get("url", ""),
            "source_type": row.get("source_type", "url"),
            "content_hash": "",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "ingested_at": None,
            "source_page": "",
            "relevance_score": row.get("relevance_score"),
            "rationale": row.get("rationale", ""),
            "discovered_by": row.get("discovered_by", "web"),
            "objective_ids": row.get("objective_ids", []),
            "proposed_at": "2026-01-01T00:00:00+00:00",
            "rejected_at": row.get("rejected_at"),
            "rejection_reason": row.get("rejection_reason", ""),
            "published": row.get("published"),
            "source_id": row.get("source_id", ""),
            "engine": row.get("engine", ""),
        }
        data["sources"][row["id"]] = full_row
    (tmp_path / "meta" / "ingest_index.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


# A minimal but rich set of test rows covering accept, reject, and neutral.
_TEST_ROWS = [
    # ingested (ACCEPT) from arxiv_cs_dc / arxiv
    {
        "id": "arxiv:2501.00001",
        "status": "ingested",
        "title": "Accept Paper A",
        "discovered_by": "web",
        "source_id": "arxiv_cs_dc",
        "engine": "arxiv",
        "relevance_score": 0.90,
        "rejection_reason": "",
    },
    # pending (ACCEPT) from arxiv_cs_dc / arxiv
    {
        "id": "arxiv:2501.00002",
        "status": "pending",
        "title": "Accept Paper B",
        "discovered_by": "web",
        "source_id": "arxiv_cs_dc",
        "engine": "arxiv",
        "relevance_score": 0.80,
        "rejection_reason": "",
    },
    # rejected from bad_source / rss (with keywords)
    {
        "id": "url:https://bad.example.com/post1",
        "status": "rejected",
        "title": "Reject Post C",
        "discovered_by": "web",
        "source_id": "bad_source",
        "engine": "rss",
        "relevance_score": 0.30,
        "rejection_reason": "unrelated topic about marketing sales funnel strategy",
    },
    # rejected from bad_source / rss (more keywords)
    {
        "id": "url:https://bad.example.com/post2",
        "status": "rejected",
        "title": "Reject Post D",
        "discovered_by": "web",
        "source_id": "bad_source",
        "engine": "rss",
        "relevance_score": 0.20,
        "rejection_reason": "clickbait advertisement product promotion sale",
    },
    # waiting_approval (NEUTRAL -- must be skipped)
    {
        "id": "arxiv:2501.99999",
        "status": "waiting_approval",
        "title": "Neutral Paper E",
        "discovered_by": "web",
        "source_id": "arxiv_cs_dc",
        "engine": "arxiv",
        "relevance_score": 0.70,
        "rejection_reason": "",
    },
    # ingested discovered by a DIFFERENT agent (must be skipped)
    {
        "id": "url:https://other.example.com/page",
        "status": "ingested",
        "title": "Other agent page",
        "discovered_by": "research",
        "source_id": "other_source",
        "engine": "perplexity",
        "relevance_score": 0.85,
        "rejection_reason": "",
    },
]


# ---------------------------------------------------------------------------
# 1-3: ingest_index.enqueue source_id / engine fields
# ---------------------------------------------------------------------------

class TestEnqueueSourceFields:

    def test_enqueue_stores_source_id_and_engine(self, tmp_path, monkeypatch):
        """enqueue with source_id + engine stores them on the row."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        row = ii.enqueue(
            "url:https://example.com/test-src",
            title="Test",
            source_id="arxiv_cs_dc",
            engine="arxiv",
        )
        assert row["source_id"] == "arxiv_cs_dc"
        assert row["engine"] == "arxiv"

        # Confirmed on disk
        data = json.loads((tmp_path / "meta" / "ingest_index.json").read_text())
        stored = data["sources"]["url:https://example.com/test-src"]
        assert stored["source_id"] == "arxiv_cs_dc"
        assert stored["engine"] == "arxiv"

    def test_enqueue_absent_source_id_engine_default_empty(self, tmp_path, monkeypatch):
        """enqueue without source_id / engine leaves them as empty strings."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        row = ii.enqueue("url:https://example.com/no-src", title="No Src")
        assert row["source_id"] == ""
        assert row["engine"] == ""

    def test_enqueue_no_clobber_source_id_engine(self, tmp_path, monkeypatch):
        """Re-enqueue without source_id / engine does NOT overwrite existing values."""
        _make_vault(tmp_path)
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))

        ii.enqueue(
            "url:https://example.com/keep-src",
            title="First",
            source_id="orig_source",
            engine="rss",
        )
        # Re-enqueue without them
        row = ii.enqueue("url:https://example.com/keep-src", title="Second")
        assert row["source_id"] == "orig_source", (
            f"source_id clobbered: {row['source_id']!r}"
        )
        assert row["engine"] == "rss", (
            f"engine clobbered: {row['engine']!r}"
        )

    def test_v3_defaults_has_source_id_engine(self):
        """_V3_DEFAULTS must contain source_id and engine."""
        assert "source_id" in ii._V3_DEFAULTS
        assert "engine" in ii._V3_DEFAULTS
        assert ii._V3_DEFAULTS["source_id"] == ""
        assert ii._V3_DEFAULTS["engine"] == ""


# ---------------------------------------------------------------------------
# 4: _rep formula
# ---------------------------------------------------------------------------

class TestRepFormula:

    def test_rep_cold_start(self):
        """(0, 0) -> 0.0 (cold-start neutral)."""
        r = _rep(0, 0, 1.0)
        assert r == pytest.approx(0.0, abs=1e-5)

    def test_rep_one_reject(self):
        """(0, 1) -> negative but strictly > -1."""
        r = _rep(0, 1, 1.0)
        assert r < 0.0
        assert r > -1.0
        # p = 1/3 -> rep = 2*(1/3) - 1 = -1/3
        assert r == pytest.approx(-1 / 3, abs=1e-4)

    def test_rep_three_accepts(self):
        """(3, 0) -> ~+0.6 (positive, smoothed)."""
        r = _rep(3, 0, 1.0)
        assert r > 0.0
        assert r < 1.0
        # p = 4/5 -> rep = 8/5 - 1 = 0.6
        assert r == pytest.approx(0.6, abs=1e-4)

    def test_rep_one_accept(self):
        """(1, 0) -> ~+0.33."""
        r = _rep(1, 0, 1.0)
        # p = 2/3 -> rep = 1/3
        assert r == pytest.approx(1 / 3, abs=1e-4)

    def test_rep_symmetric(self):
        """_rep is antisymmetric: _rep(a, r, s) == -_rep(r, a, s)."""
        assert _rep(2, 3, 1.0) == pytest.approx(-_rep(3, 2, 1.0), abs=1e-5)

    def test_rep_bounded(self):
        """Rep is always strictly between -1 and +1 for any non-negative a/r."""
        for a, r in [(0, 10), (10, 0), (5, 5), (0, 100), (100, 0)]:
            val = _rep(a, r, 1.0)
            assert -1.0 < val < 1.0, f"_rep({a},{r}) = {val} out of range"


# ---------------------------------------------------------------------------
# 5: keyword extraction
# ---------------------------------------------------------------------------

class TestKeywordExtraction:

    def test_drops_short_tokens(self):
        """Tokens shorter than 4 chars are dropped."""
        kws = _extract_keywords("is a bad one")
        assert kws == [], f"Expected empty, got {kws}"

    def test_drops_english_stopwords(self):
        """Standard English stopwords are dropped."""
        kws = _extract_keywords("this is about the learning method")
        # 'this', 'is', 'about', 'the', 'learning'? 'learning' >= 4 and not in stops
        # But 'method' is in domain stops, 'learning' is in domain stops
        for tok in ("this", "about", "the"):
            assert tok not in kws

    def test_drops_domain_stopwords(self):
        """Domain-specific stopwords (arxiv, paper, model, etc.) are dropped."""
        kws = _extract_keywords("arxiv paper about optical inference model")
        for tok in ("arxiv", "paper", "optical", "inference", "model"):
            assert tok not in kws

    def test_extracts_meaningful_tokens(self):
        """Meaningful domain-irrelevant tokens survive."""
        kws = _extract_keywords("unrelated topic about marketing sales funnel strategy")
        # 'unrelated', 'topic', 'marketing', 'sales', 'funnel', 'strategy' are candidates
        # 'topic' >= 4, not in stops -> should appear
        # 'marketing', 'sales', 'funnel', 'strategy' should appear
        assert "marketing" in kws or "funnel" in kws or "strategy" in kws, (
            f"Expected some meaningful tokens, got: {kws}"
        )

    def test_dedup(self):
        """Duplicate tokens appear only once."""
        kws = _extract_keywords("duplicate duplicate duplicate testing")
        count = kws.count("duplicate")
        assert count == 1

    def test_empty_reason(self):
        """Empty reason returns empty list."""
        assert _extract_keywords("") == []
        assert _extract_keywords(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 6-8: learn() core behaviour
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_learned(tmp_path_factory):
    """Patch _learned_path to a fresh temp file for the duration of one test.

    Yields the temp file path so tests can inspect it if needed.
    Using this fixture ensures learn() never reads from / writes to the real
    .claude/memory/web/learned.json in the code repo, making every test hermetic.
    """
    import scripts.agent_learn as al
    write_dir = tmp_path_factory.mktemp("learned_isolated")
    learned_file = write_dir / "learned.json"
    original_fn = al._learned_path
    al._learned_path = lambda agent: learned_file  # type: ignore[assignment]
    yield learned_file
    al._learned_path = original_fn


class TestLearnCore:

    def test_learn_reputation_math(self, tmp_path, isolated_learned):
        """
        arxiv_cs_dc: 2 accepts, 0 rejects -> positive rep
        bad_source:  0 accepts, 2 rejects -> negative rep
        """
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        result = learn(str(tmp_path), "web", apply=False)
        learned = result["learned"]

        src = learned["sources"]
        assert "arxiv_cs_dc" in src, "arxiv_cs_dc not in sources"
        assert "bad_source" in src, "bad_source not in sources"

        dc_rep = src["arxiv_cs_dc"]["rep"]
        bad_rep = src["bad_source"]["rep"]

        assert dc_rep > 0.0, f"arxiv_cs_dc rep should be positive, got {dc_rep}"
        assert bad_rep < 0.0, f"bad_source rep should be negative, got {bad_rep}"
        assert -1.0 < bad_rep, f"bad_source rep should be > -1 (smoothed), got {bad_rep}"

        # arxiv_cs_dc: accept=2, reject=0 -> _rep(2, 0, 1.0) = 2*(3/4)-1 = 0.5
        assert dc_rep == pytest.approx(_rep(2, 0, 1.0), abs=1e-4)
        # bad_source: accept=0, reject=2 -> _rep(0, 2, 1.0) = 2*(1/4)-1 = -0.5
        assert bad_rep == pytest.approx(_rep(0, 2, 1.0), abs=1e-4)

    def test_learn_engine_rep(self, tmp_path, isolated_learned):
        """
        arxiv engine: 2 accepts, 0 rejects -> positive rep
        rss engine:   0 accepts, 2 rejects -> negative rep
        """
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        result = learn(str(tmp_path), "web", apply=False)
        learned = result["learned"]

        eng = learned["engines"]
        assert "arxiv" in eng
        assert "rss" in eng
        assert eng["arxiv"]["rep"] > 0.0
        assert eng["rss"]["rep"] < 0.0

    def test_learn_neutral_rows_skipped(self, tmp_path, isolated_learned):
        """waiting_approval rows must not affect counts."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        result = learn(str(tmp_path), "web", apply=False)
        learned = result["learned"]

        # The neutral row (arxiv:2501.99999) should NOT be in processed_ids
        assert "arxiv:2501.99999" not in learned["processed_ids"]

    def test_learn_other_agent_skipped(self, tmp_path, isolated_learned):
        """Rows discovered by a different agent must be skipped."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        result = learn(str(tmp_path), "web", apply=False)
        learned = result["learned"]

        # other_source was from "research" agent, should not appear in web learned
        assert "other_source" not in learned["sources"]
        assert "perplexity" not in learned["engines"]

    def test_learn_reject_keywords_extracted(self, tmp_path, isolated_learned):
        """keyword_counts are populated; keywords (derived) respects freq threshold.

        _TEST_ROWS has two distinct rejection reasons with no overlapping non-stop tokens,
        so with min_reject_freq=2 (default), keywords is EMPTY -- each token only appears
        in one reason (count=1 < 2). keyword_counts must still be populated.
        """
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        result = learn(str(tmp_path), "web", apply=False)
        learned = result["learned"]

        # keyword_counts should have entries from both rejection reasons
        kc = learned["reject_patterns"].get("keyword_counts", {})
        assert len(kc) > 0, "Expected keyword_counts to be populated"
        # Each count is exactly 1 (each token seen in only one distinct reason)
        for kw, cnt in kc.items():
            assert cnt == 1, f"Expected count=1 for single-reason token {kw!r}, got {cnt}"

        # keywords (derived) must be EMPTY because no token reached min_freq=2
        kws = learned["reject_patterns"]["keywords"]
        assert kws == [], (
            f"Expected empty keywords list (freq threshold), got: {kws}"
        )
        # Counts are non-empty so tokens are not dropped -- they just haven't graduated yet
        assert len(kc) >= 3, f"Expected at least 3 keyword_counts entries, got {len(kc)}"

    def test_learn_calibration_populated(self, tmp_path, isolated_learned):
        """Calibration means are updated with actual scores."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        result = learn(str(tmp_path), "web", apply=False)
        cal = result["learned"]["calibration"]

        assert cal["n"] > 0, "Calibration n should be > 0"
        # accept scores: 0.90 + 0.80; reject scores: 0.30 + 0.20
        # accepted_score_mean should be around (0.90+0.80)/2 = 0.85
        # rejected_score_mean should be around (0.30+0.20)/2 = 0.25
        # Note: the running mean is mixed (not split perfectly), but we assert bounds
        assert 0.0 < cal["accepted_score_mean"] <= 1.0
        assert 0.0 <= cal["rejected_score_mean"] <= 1.0

    def test_learn_processed_ids_populated(self, tmp_path, isolated_learned):
        """processed_ids contains ids of all decided rows."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        result = learn(str(tmp_path), "web", apply=False)
        learned = result["learned"]

        pids = set(learned["processed_ids"])
        # The four decided rows (2 accept + 2 reject)
        assert "arxiv:2501.00001" in pids
        assert "arxiv:2501.00002" in pids
        assert "url:https://bad.example.com/post1" in pids
        assert "url:https://bad.example.com/post2" in pids
        # Neutral row must NOT be in processed_ids
        assert "arxiv:2501.99999" not in pids

    def test_learn_n_new(self, tmp_path, isolated_learned):
        """n_new equals the number of new decided rows (4 here)."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        result = learn(str(tmp_path), "web", apply=False)
        assert result["n_new"] == 4


# ---------------------------------------------------------------------------
# 8: Idempotency
# ---------------------------------------------------------------------------

class TestLearnIdempotency:

    def test_second_learn_no_double_count(self, tmp_path, isolated_learned):
        """A second learn() with no new decisions -> n_new == 0, counts unchanged."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)

        # First run: apply=True so learned.json is written
        first = learn(str(tmp_path), "web", apply=True)
        assert first["n_new"] == 4

        src_after_first = json.loads(
            json.dumps(first["learned"]["sources"])  # deep copy
        )

        # Second run: same rows, nothing new
        second = learn(str(tmp_path), "web", apply=True)
        assert second["n_new"] == 0, (
            f"Expected 0 new decisions on second run, got {second['n_new']}"
        )

        # Counts must not change
        for src_id, info in second["learned"]["sources"].items():
            first_info = src_after_first.get(src_id, {})
            assert info["accept"] == first_info.get("accept"), (
                f"accept count changed for {src_id}"
            )
            assert info["reject"] == first_info.get("reject"), (
                f"reject count changed for {src_id}"
            )

    def test_new_decision_after_idempotent_run(self, tmp_path, isolated_learned):
        """Adding a new decided row after the first run -> n_new == 1 on second run."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)

        first = learn(str(tmp_path), "web", apply=True)
        assert first["n_new"] == 4

        # Add a new decided row
        existing_rows = list(_TEST_ROWS)
        existing_rows.append({
            "id": "arxiv:2501.00003",
            "status": "rejected",
            "title": "New Reject Paper",
            "discovered_by": "web",
            "source_id": "new_source",
            "engine": "arxiv",
            "relevance_score": 0.25,
            "rejection_reason": "completely irrelevant advertisement clickbait",
        })
        _seed_ingest_index(tmp_path, existing_rows)

        second = learn(str(tmp_path), "web", apply=True)
        assert second["n_new"] == 1, (
            f"Expected 1 new decision, got {second['n_new']}"
        )


# ---------------------------------------------------------------------------
# 9: rep_for() precedence
# ---------------------------------------------------------------------------

class TestRepFor:

    def _make_learned(self) -> dict:
        return {
            "sources": {
                "arxiv_cs_dc": {"accept": 3, "reject": 0, "rep": 0.6},
                "bad_src": {"accept": 0, "reject": 3, "rep": -0.6},
            },
            "engines": {
                "arxiv": {"accept": 2, "reject": 1, "rep": 0.2},
                "rss": {"accept": 0, "reject": 2, "rep": -0.4},
            },
            "reject_patterns": {
                "keyword_counts": {"marketing": 3, "clickbait": 2},
                "keywords": ["marketing", "clickbait"],
            },
            "calibration": {"accepted_score_mean": 0.85, "rejected_score_mean": 0.25, "n": 5},
            "processed_ids": [],
        }

    def test_source_over_engine(self):
        """Source rep takes precedence over engine rep."""
        learned = self._make_learned()
        # source arxiv_cs_dc rep=0.6, engine arxiv rep=0.2 -> should return 0.6
        r = rep_for(learned, "arxiv_cs_dc", "arxiv")
        assert r == pytest.approx(0.6, abs=1e-5)

    def test_engine_when_no_source(self):
        """Engine rep returned when source_id is not in sources."""
        learned = self._make_learned()
        r = rep_for(learned, "unknown_source", "arxiv")
        assert r == pytest.approx(0.2, abs=1e-5)

    def test_zero_when_neither_known(self):
        """0.0 returned when neither source nor engine is known."""
        learned = self._make_learned()
        r = rep_for(learned, "unknown_source", "unknown_engine")
        assert r == pytest.approx(0.0, abs=1e-5)

    def test_empty_source_falls_to_engine(self):
        """Empty source_id falls through to engine rep."""
        learned = self._make_learned()
        r = rep_for(learned, "", "rss")
        assert r == pytest.approx(-0.4, abs=1e-5)

    def test_empty_both_returns_zero(self):
        """Both empty -> 0.0."""
        learned = self._make_learned()
        r = rep_for(learned, "", "")
        assert r == pytest.approx(0.0, abs=1e-5)


# ---------------------------------------------------------------------------
# 10-12: reject_penalty()
# ---------------------------------------------------------------------------

class TestRejectPenalty:

    def _make_learned(self) -> dict:
        return {
            "sources": {
                "spam_source": {"accept": 0, "reject": 5, "rep": -0.75},
                "good_source": {"accept": 5, "reject": 0, "rep": 0.75},
            },
            "engines": {"rss": {"accept": 0, "reject": 3, "rep": -0.5}},
            "reject_patterns": {
                "keyword_counts": {"marketing": 3, "clickbait": 2, "funnel": 2},
                "keywords": ["marketing", "clickbait", "funnel"],
            },
            "calibration": {"accepted_score_mean": 0.8, "rejected_score_mean": 0.2, "n": 8},
            "processed_ids": [],
        }

    def test_penalty_on_keyword_in_title(self):
        """Keyword match in title -> 1.0."""
        learned = self._make_learned()
        cand = {"title": "Best marketing strategies for 2026", "snippet": "", "source_id": "good_source"}
        assert reject_penalty(learned, cand) == 1.0

    def test_penalty_on_keyword_in_snippet(self):
        """Keyword match in snippet -> 1.0."""
        learned = self._make_learned()
        cand = {"title": "Generic title", "snippet": "This is clickbait content here", "source_id": "good_source"}
        assert reject_penalty(learned, cand) == 1.0

    def test_penalty_on_low_rep_source(self):
        """Source rep <= -0.5 -> 1.0 (even without keyword match)."""
        learned = self._make_learned()
        cand = {"title": "Normal title about inference", "snippet": "Normal content", "source_id": "spam_source"}
        assert reject_penalty(learned, cand) == 1.0

    def test_no_penalty_clean_candidate(self):
        """No keyword, no low-rep source -> 0.0."""
        learned = self._make_learned()
        cand = {"title": "Disaggregated inference with photonic links", "snippet": "Novel approach to P/D", "source_id": "good_source"}
        assert reject_penalty(learned, cand) == 0.0

    def test_no_penalty_empty_keywords(self):
        """Empty keyword list -> 0.0 regardless of text content."""
        learned = self._make_learned()
        learned["reject_patterns"]["keywords"] = []
        cand = {"title": "marketing funnel clickbait", "snippet": "advertising sales", "source_id": "good_source"}
        assert reject_penalty(learned, cand) == 0.0

    def test_no_penalty_unknown_source(self):
        """Unknown source_id -> no source-rep penalty; falls through to keyword check."""
        learned = self._make_learned()
        cand = {"title": "Normal title", "snippet": "Normal content", "source_id": "unknown"}
        assert reject_penalty(learned, cand) == 0.0

    def test_exactly_minus_half_rep_triggers_penalty(self):
        """rep == -0.5 (boundary) -> triggers penalty."""
        learned = self._make_learned()
        learned["sources"]["boundary_src"] = {"accept": 0, "reject": 1, "rep": -0.5}
        cand = {"title": "Normal", "snippet": "Normal", "source_id": "boundary_src"}
        assert reject_penalty(learned, cand) == 1.0


# ---------------------------------------------------------------------------
# 13-14: render_briefing()
# ---------------------------------------------------------------------------

class TestRenderBriefing:

    def test_non_empty_delta_lists_sources(self):
        """Non-empty delta includes changed source names in the briefing."""
        delta = {
            "sources": {
                "arxiv_cs_dc": {"accept": 2, "reject": 0, "rep": 0.5, "prev_rep": None},
            },
            "engines": {},
            "new_keywords": ["marketing", "clickbait"],
            "calibration": {"accepted_score_mean": 0.85, "rejected_score_mean": 0.25, "n": 4},
            "n_new": 2,
        }
        briefing = render_briefing(delta, prev_updated="2026-06-20")
        assert "arxiv_cs_dc" in briefing
        assert "Learning briefing" in briefing
        assert "marketing" in briefing or "clickbait" in briefing

    def test_cold_start_when_zero_ever(self):
        """When n_new == 0, the cold-start message is returned."""
        delta = {
            "sources": {},
            "engines": {},
            "new_keywords": [],
            "calibration": {"accepted_score_mean": 0.0, "rejected_score_mean": 0.0, "n": 0},
            "n_new": 0,
        }
        briefing = render_briefing(delta, prev_updated=None)
        assert "No prior outcomes yet" in briefing
        assert "cold-start" in briefing.lower() or "No prior outcomes" in briefing

    def test_briefing_has_header(self):
        """Briefing always starts with the ## Learning briefing header."""
        delta = {
            "sources": {"src": {"accept": 1, "reject": 0, "rep": 0.33, "prev_rep": None}},
            "engines": {},
            "new_keywords": [],
            "calibration": {"accepted_score_mean": 0.9, "rejected_score_mean": 0.0, "n": 1},
            "n_new": 1,
        }
        briefing = render_briefing(delta, prev_updated="2026-06-23")
        assert briefing.startswith("## Learning briefing")

    def test_briefing_direction_arrows(self):
        """When prev_rep is given, up/down is shown."""
        delta = {
            "sources": {
                "improving_src": {"accept": 3, "reject": 0, "rep": 0.6, "prev_rep": 0.33},
                "declining_src": {"accept": 0, "reject": 3, "rep": -0.6, "prev_rep": -0.33},
            },
            "engines": {},
            "new_keywords": [],
            "calibration": {"accepted_score_mean": 0.8, "rejected_score_mean": 0.2, "n": 6},
            "n_new": 6,
        }
        briefing = render_briefing(delta, prev_updated="2026-06-01")
        assert "up" in briefing
        assert "down" in briefing


# ---------------------------------------------------------------------------
# 15-16: apply / dry-run
# ---------------------------------------------------------------------------

class TestLearnApply:

    def test_dry_run_does_not_write(self, tmp_path, isolated_learned):
        """learn(apply=False) does NOT create learned.json."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        learned_file = isolated_learned  # the patched path

        result = learn(str(tmp_path), "web", apply=False)
        assert result["n_new"] == 4

        # apply=False must NOT write learned.json
        assert not learned_file.exists(), (
            "learned.json was written despite apply=False (dry-run)"
        )

        # A second dry-run with no prior persisted state still sees 4 new
        result2 = learn(str(tmp_path), "web", apply=False)
        assert result2["n_new"] == 4, (
            "apply=False should not persist; second dry-run should still see 4 new"
        )

    def test_apply_true_writes_learned_json(self, tmp_path, isolated_learned):
        """learn(apply=True) writes learned.json."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        learned_file = isolated_learned

        result = learn(str(tmp_path), "web", apply=True)
        assert result["n_new"] == 4
        assert learned_file.exists(), "learned.json was not written"
        data = json.loads(learned_file.read_text())
        assert "sources" in data
        assert len(data["processed_ids"]) == 4

    def test_apply_true_idempotent_on_disk(self, tmp_path, isolated_learned):
        """Two apply=True runs -> second has n_new==0, counts same as first."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)

        first = learn(str(tmp_path), "web", apply=True)
        assert first["n_new"] == 4

        second = learn(str(tmp_path), "web", apply=True)
        assert second["n_new"] == 0

        # counts unchanged
        for sid in first["learned"]["sources"]:
            a1 = first["learned"]["sources"][sid]["accept"]
            r1 = first["learned"]["sources"][sid]["reject"]
            a2 = second["learned"]["sources"][sid]["accept"]
            r2 = second["learned"]["sources"][sid]["reject"]
            assert a1 == a2, f"accept changed for {sid}"
            assert r1 == r2, f"reject changed for {sid}"

    def test_keyword_counts_persisted_in_json(self, tmp_path, isolated_learned):
        """keyword_counts is written to learned.json; keywords is derived."""
        _seed_ingest_index(tmp_path, _TEST_ROWS)
        learn(str(tmp_path), "web", apply=True)

        data = json.loads(isolated_learned.read_text())
        rp = data["reject_patterns"]
        assert "keyword_counts" in rp, "keyword_counts missing from persisted learned.json"
        assert isinstance(rp["keyword_counts"], dict)
        assert "keywords" in rp
        assert isinstance(rp["keywords"], list)
        # With _TEST_ROWS (each token appears in only 1 reason), keywords must be empty
        assert rp["keywords"] == [], (
            f"keywords should be empty (freq=1 < 2), got: {rp['keywords']}"
        )


# ---------------------------------------------------------------------------
# 17: Guard 1 -- frequency threshold
# ---------------------------------------------------------------------------

class TestFrequencyThreshold:
    """A token must appear in >= min_reject_freq distinct reasons to become a keyword."""

    def _rows_with_shared_token(self) -> list[dict]:
        """Two rejection reasons both containing 'clickbait'; one unique token each."""
        return [
            {
                "id": "url:freq-test-1",
                "status": "rejected",
                "title": "Spam A",
                "discovered_by": "web",
                "source_id": "spam_src",
                "engine": "rss",
                "relevance_score": 0.2,
                "rejection_reason": "clickbait garbage article nothing useful here",
            },
            {
                "id": "url:freq-test-2",
                "status": "rejected",
                "title": "Spam B",
                "discovered_by": "web",
                "source_id": "spam_src",
                "engine": "rss",
                "relevance_score": 0.1,
                "rejection_reason": "clickbait tabloid nonsense completely wrong direction",
            },
        ]

    def test_single_reason_token_not_in_keywords(self, tmp_path, isolated_learned):
        """Token in only ONE rejection reason is NOT in keywords (count 1 < 2)."""
        rows = [
            {
                "id": "url:single-reason",
                "status": "rejected",
                "title": "Single",
                "discovered_by": "web",
                "source_id": "src",
                "engine": "rss",
                "relevance_score": 0.2,
                "rejection_reason": "marketing garbage advertisement",
            },
        ]
        _seed_ingest_index(tmp_path, rows)
        result = learn(str(tmp_path), "web", apply=False)
        kws = result["learned"]["reject_patterns"]["keywords"]
        assert "marketing" not in kws, (
            f"'marketing' should not be in keywords (count=1 < 2), got: {kws}"
        )
        assert kws == [], f"Expected empty keywords, got: {kws}"
        # But keyword_counts should track it
        kc = result["learned"]["reject_patterns"]["keyword_counts"]
        assert kc.get("marketing", 0) == 1

    def test_two_reason_token_in_keywords(self, tmp_path, isolated_learned):
        """Token in TWO distinct rejection reasons IS in keywords (count 2 >= 2)."""
        rows = self._rows_with_shared_token()
        _seed_ingest_index(tmp_path, rows)
        result = learn(str(tmp_path), "web", apply=False)
        kws = result["learned"]["reject_patterns"]["keywords"]
        kc = result["learned"]["reject_patterns"]["keyword_counts"]
        assert kc.get("clickbait", 0) == 2, f"Expected clickbait count=2, got {kc}"
        assert "clickbait" in kws, (
            f"'clickbait' should be in keywords (count=2 >= 2), got: {kws}"
        )
        # Unique tokens (count=1) must NOT be in keywords
        assert "garbage" not in kws
        assert "tabloid" not in kws

    def test_counts_accumulate_across_runs(self, tmp_path, isolated_learned):
        """Token seen once in run-1 and once in run-2 reaches count=2 -> promoted."""
        row_run1 = [
            {
                "id": "url:accum-1",
                "status": "rejected",
                "title": "Run 1",
                "discovered_by": "web",
                "source_id": "src",
                "engine": "rss",
                "relevance_score": 0.2,
                "rejection_reason": "clickbait nonsense article",
            },
        ]
        row_run2 = row_run1 + [
            {
                "id": "url:accum-2",
                "status": "rejected",
                "title": "Run 2",
                "discovered_by": "web",
                "source_id": "src",
                "engine": "rss",
                "relevance_score": 0.1,
                "rejection_reason": "clickbait tabloid garbage",
            },
        ]

        # Run 1: seed with only the first row, apply=True so state persists
        _seed_ingest_index(tmp_path, row_run1)
        r1 = learn(str(tmp_path), "web", apply=True)
        assert r1["n_new"] == 1
        assert r1["learned"]["reject_patterns"]["keyword_counts"].get("clickbait", 0) == 1
        # count=1 < 2 -> not yet in keywords
        assert "clickbait" not in r1["learned"]["reject_patterns"]["keywords"]

        # Run 2: add the second row (first is already processed)
        _seed_ingest_index(tmp_path, row_run2)
        r2 = learn(str(tmp_path), "web", apply=True)
        assert r2["n_new"] == 1  # only the new row is new
        kc = r2["learned"]["reject_patterns"]["keyword_counts"]
        assert kc.get("clickbait", 0) == 2, f"Expected clickbait count=2 after 2 runs, got {kc}"
        # Now count=2 >= 2 -> promoted to keywords
        assert "clickbait" in r2["learned"]["reject_patterns"]["keywords"], (
            f"'clickbait' should appear in keywords after 2 runs: {r2['learned']['reject_patterns']['keywords']}"
        )


# ---------------------------------------------------------------------------
# 19: Guard 2 -- PURPOSE/topic guard
# ---------------------------------------------------------------------------

class TestPurposeGuard:
    """Terms from the vault PURPOSE/topic files must never become reject keywords."""

    def _make_vault_with_purpose(self, tmp_path: Path, purpose_text: str) -> None:
        """Seed a minimal vault with a PURPOSE.md containing purpose_text."""
        purpose_dir = tmp_path / "objective" / "purpose"
        purpose_dir.mkdir(parents=True, exist_ok=True)
        (purpose_dir / "PURPOSE.md").write_text(purpose_text, encoding="utf-8")

    def test_purpose_term_never_in_keywords(self, tmp_path, isolated_learned):
        """A term in PURPOSE.md never appears in keywords even with count >= 2."""
        self._make_vault_with_purpose(
            tmp_path,
            "This vault tracks disaggregation of inference workloads.",
        )
        # Two distinct rejection reasons both mentioning 'disaggregation'
        rows = [
            {
                "id": "url:purpose-guard-1",
                "status": "rejected",
                "title": "Rej 1",
                "discovered_by": "web",
                "source_id": "src",
                "engine": "rss",
                "relevance_score": 0.2,
                "rejection_reason": "disaggregation paper irrelevant marketing junk",
            },
            {
                "id": "url:purpose-guard-2",
                "status": "rejected",
                "title": "Rej 2",
                "discovered_by": "web",
                "source_id": "src",
                "engine": "rss",
                "relevance_score": 0.1,
                "rejection_reason": "disaggregation clickbait garbage nonsense article",
            },
        ]
        _seed_ingest_index(tmp_path, rows)
        result = learn(str(tmp_path), "web", apply=False)
        kws = result["learned"]["reject_patterns"]["keywords"]
        kc = result["learned"]["reject_patterns"]["keyword_counts"]

        # 'disaggregation' is protected; must not be in keyword_counts OR keywords
        assert "disaggregation" not in kws, (
            f"'disaggregation' must not be a reject keyword (PURPOSE guard): {kws}"
        )
        assert "disaggregation" not in kc, (
            f"'disaggregation' must not be counted (PURPOSE guard): {kc}"
        )

    def test_purpose_guard_missing_file_no_crash(self, tmp_path, isolated_learned):
        """Missing objective/purpose/PURPOSE.md -> empty protected set, no crash."""
        # No purpose file created -- should not raise
        rows = [
            {
                "id": "url:no-purpose",
                "status": "rejected",
                "title": "Rej",
                "discovered_by": "web",
                "source_id": "src",
                "engine": "rss",
                "relevance_score": 0.2,
                "rejection_reason": "marketing spam garbage junk",
            },
        ]
        _seed_ingest_index(tmp_path, rows)
        result = learn(str(tmp_path), "web", apply=False)
        # Should complete without error; keyword_counts populated normally
        kc = result["learned"]["reject_patterns"]["keyword_counts"]
        assert "marketing" in kc or "spam" in kc or "garbage" in kc, (
            f"Expected some keyword_counts without PURPOSE file, got: {kc}"
        )

    def test_build_protected_terms_reads_purpose(self, tmp_path):
        """_build_protected_terms tokenizes PURPOSE.md correctly."""
        purpose_dir = tmp_path / "objective" / "purpose"
        purpose_dir.mkdir(parents=True, exist_ok=True)
        (purpose_dir / "PURPOSE.md").write_text(
            "Disaggregated LLM inference with optical accelerators for Iris Tetra.",
            encoding="utf-8",
        )
        terms = _build_protected_terms(str(tmp_path))
        assert "disaggregated" in terms
        assert "inference" in terms
        assert "optical" in terms
        assert "accelerators" in terms
        # Short tokens dropped (len < 4)
        assert "for" not in terms  # len=3, dropped
        assert "llm" not in terms  # len=3, dropped
        # Tokens >= 4 chars are included (including common words like "with")
        assert "iris" in terms
        assert "tetra" in terms

    def test_build_protected_terms_reads_topics(self, tmp_path):
        """_build_protected_terms also reads topic/*.md files."""
        topic_dir = tmp_path / "objective" / "topic"
        topic_dir.mkdir(parents=True, exist_ok=True)
        (topic_dir / "T-0001-test.md").write_text(
            "KV-cache pipeline photonic interconnect prefill disaggregation.",
            encoding="utf-8",
        )
        terms = _build_protected_terms(str(tmp_path))
        assert "pipeline" in terms
        assert "photonic" in terms
        assert "prefill" in terms
        assert "disaggregation" in terms

    def test_build_protected_terms_skips_template(self, tmp_path):
        """_template.md is NOT read (prefix _ convention)."""
        topic_dir = tmp_path / "objective" / "topic"
        topic_dir.mkdir(parents=True, exist_ok=True)
        (topic_dir / "_template.md").write_text(
            "templateonlyword unique placeholder text for template files.",
            encoding="utf-8",
        )
        terms = _build_protected_terms(str(tmp_path))
        # 'templateonlyword' would appear if _template.md were read; it must not
        assert "templateonlyword" not in terms

    def test_build_protected_terms_empty_when_no_vault_files(self, tmp_path):
        """Returns empty set gracefully when vault has no objective/ directory."""
        terms = _build_protected_terms(str(tmp_path))
        assert isinstance(terms, set)
        # May be empty or have nothing from objective/ dir
        assert "disaggregation" not in terms


# ---------------------------------------------------------------------------
# 21: Old-schema migration
# ---------------------------------------------------------------------------

class TestOldSchemaMigration:
    """An existing learned.json with only 'keywords' (no 'keyword_counts') migrates safely."""

    def test_old_schema_migrates_without_crash(self, tmp_path, isolated_learned):
        """Old-schema file (keywords only) is loaded and keyword_counts seeded."""
        old_schema = {
            "updated": "2026-06-01",
            "sources": {"old_src": {"accept": 1, "reject": 2, "rep": -0.2}},
            "engines": {},
            "reject_patterns": {"keywords": ["marketing", "funnel"]},
            "calibration": {"accepted_score_mean": 0.5, "rejected_score_mean": 0.3, "n": 3},
            "processed_ids": ["url:old-1", "url:old-2"],
        }
        isolated_learned.write_text(json.dumps(old_schema), encoding="utf-8")

        # _load_learned should migrate without crashing
        loaded = _load_learned("web", min_freq=2)
        rp = loaded["reject_patterns"]
        assert "keyword_counts" in rp, "keyword_counts should be seeded on migration"
        # Each old keyword should be seeded with min_freq so it survives the threshold
        assert rp["keyword_counts"].get("marketing", 0) == 2
        assert rp["keyword_counts"].get("funnel", 0) == 2
        # keywords key preserved
        assert "marketing" in rp.get("keywords", [])
        assert "funnel" in rp.get("keywords", [])
        # Existing sources untouched
        assert loaded["sources"]["old_src"]["rep"] == -0.2

    def test_old_schema_learn_continues_without_double_counting(
        self, tmp_path, isolated_learned
    ):
        """After migration, a new learn() run does not double-count old decisions."""
        old_schema = {
            "updated": "2026-06-01",
            "sources": {},
            "engines": {},
            "reject_patterns": {"keywords": ["marketing"]},
            "calibration": {"accepted_score_mean": 0.0, "rejected_score_mean": 0.0, "n": 0},
            "processed_ids": [],
        }
        isolated_learned.write_text(json.dumps(old_schema), encoding="utf-8")

        # New rows with a shared keyword
        rows = [
            {
                "id": "url:migrate-1",
                "status": "rejected",
                "title": "M1",
                "discovered_by": "web",
                "source_id": "src",
                "engine": "rss",
                "relevance_score": 0.2,
                "rejection_reason": "clickbait spam garbage article",
            },
            {
                "id": "url:migrate-2",
                "status": "rejected",
                "title": "M2",
                "discovered_by": "web",
                "source_id": "src",
                "engine": "rss",
                "relevance_score": 0.1,
                "rejection_reason": "clickbait tabloid nonsense junk",
            },
        ]
        _seed_ingest_index(tmp_path, rows)
        result = learn(str(tmp_path), "web", apply=True)

        kc = result["learned"]["reject_patterns"]["keyword_counts"]
        # 'clickbait' appears in 2 new reasons -> count=2 -> in keywords
        assert kc.get("clickbait", 0) == 2
        assert "clickbait" in result["learned"]["reject_patterns"]["keywords"]
        # 'marketing' was seeded at count=2 from migration -> still in keywords
        assert kc.get("marketing", 0) == 2
        assert "marketing" in result["learned"]["reject_patterns"]["keywords"]


# ---------------------------------------------------------------------------
# 22: _derive_keywords cap
# ---------------------------------------------------------------------------

class TestDeriveKeywords:
    """_derive_keywords: sorted by count desc, cap at 50, only >= min_freq."""

    def test_sorted_by_count_desc(self):
        counts = {"rare": 2, "common": 5, "medium": 3}
        kws = _derive_keywords(counts, min_freq=2)
        assert kws[0] == "common"
        assert kws[1] == "medium"
        assert kws[2] == "rare"

    def test_below_min_freq_excluded(self):
        counts = {"below": 1, "above": 2, "equal": 2}
        kws = _derive_keywords(counts, min_freq=2)
        assert "below" not in kws
        assert "above" in kws
        assert "equal" in kws

    def test_cap_at_50(self):
        counts = {f"kw{i}": 5 for i in range(100)}
        kws = _derive_keywords(counts, min_freq=1, cap=50)
        assert len(kws) == 50

    def test_tie_broken_alphabetically(self):
        counts = {"zebra": 3, "apple": 3, "mango": 3}
        kws = _derive_keywords(counts, min_freq=1)
        assert kws == ["apple", "mango", "zebra"]

    def test_empty_input(self):
        assert _derive_keywords({}, min_freq=2) == []
