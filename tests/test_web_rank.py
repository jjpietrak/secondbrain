"""Hermetic tests for scripts/web_rank.py.

ALL tests are offline -- no real ollama / network calls.

Ollama on/off control
---------------------
- Fallback path: monkeypatch `web_rank.ollama_alive` to return `(False, [])`
  so the module never reaches the embedding branch.
- Embedding path: monkeypatch `web_rank.ollama_alive` to return `(True, ["nomic-embed-text"])`
  AND `web_rank.embed_one` to a deterministic function that turns text into a
  canned small vector (based on character-level hash), making cosine scores
  predictable without any network call.

Usage:
  python -m pytest tests/test_web_rank.py -q
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Make scripts/ importable
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_rank  # noqa: E402  (after sys.path shim)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    title: str,
    snippet: str = "",
    source_id: str = "src-default",
    published: str = "",
    url: str = "https://example.com/x",
    engine: str = "arxiv",
) -> dict:
    """Build a minimal candidate dict matching the web_harvest.py pinned shape."""
    return {
        "title": title,
        "url": url,
        "source_id": source_id,
        "id_type": "url",
        "published": published,
        "snippet": snippet,
        "engine": engine,
    }


def _ollama_off(monkeypatch):
    """Patch ollama to appear unreachable -- forces deterministic fallback."""
    monkeypatch.setattr(web_rank, "ollama_alive", lambda url: (False, []))


def _canned_vector(text: str) -> list[float]:
    """Deterministic 4-dim vector derived from text content for cosine testing.

    Each dimension is the normalised count of a fixed character class.
    Two texts that share words will have similar vectors.
    """
    chars = text.lower()
    dims = [
        chars.count("a") + chars.count("e"),   # vowels ae
        chars.count("i") + chars.count("o"),   # vowels io
        chars.count("s") + chars.count("t"),   # consonants st
        chars.count("n") + chars.count("r"),   # consonants nr
    ]
    total = sum(dims) or 1
    return [d / total for d in dims]


def _ollama_on(monkeypatch):
    """Patch ollama to appear alive with nomic-embed-text, using canned vectors."""
    monkeypatch.setattr(
        web_rank, "ollama_alive",
        lambda url: (True, [web_rank.DEFAULT_MODEL]),
    )
    monkeypatch.setattr(web_rank, "embed_one", lambda url, model, text: _canned_vector(text))


# ---------------------------------------------------------------------------
# Registry fixture
# ---------------------------------------------------------------------------

_SAMPLE_REGISTRY = {
    "paper-publisher": {
        "sources": [
            {"id": "arxiv_cs_dc", "relevance": 5, "category": "preprint_repository"},
            {"id": "arxiv_cs_ar", "relevance": 3, "category": "preprint_repository"},
        ]
    },
    "blog-newsfeed": {
        "sources": [
            {"id": "vendor_blog_a", "relevance": 4, "category": "vendor_blogs"},
        ]
    },
    "github-repos": {
        "repositories": [
            {"id": "vllm-project/vllm", "relevance": 5},
        ]
    },
}


# ===========================================================================
# Fallback path tests
# ===========================================================================

class TestFallbackPath:
    """All tests monkeypatch ollama_alive -> (False, []) to force the deterministic fallback."""

    def test_high_overlap_outranks_low_overlap(self, monkeypatch):
        """A candidate whose title/snippet shares many query keywords ranks first."""
        _ollama_off(monkeypatch)
        query = "disaggregated KV cache inference memory"
        high = _make_candidate(
            title="Disaggregated KV Cache for Efficient LLM Inference",
            snippet="Memory-efficient inference through disaggregated KV cache management.",
            source_id="arxiv:2401.00001",
        )
        low = _make_candidate(
            title="A novel cookie recipe",
            snippet="Baking tips for chocolate chip cookies.",
            source_id="blog:cookies",
        )
        ranked = web_rank.score_candidates([low, high], query)
        assert len(ranked) == 2
        assert ranked[0]["source_id"] == "arxiv:2401.00001"
        assert ranked[0]["score"] > ranked[1]["score"]

    def test_deterministic_order_on_repeat_calls(self, monkeypatch):
        """Calling score_candidates twice with the same inputs returns identical order."""
        _ollama_off(monkeypatch)
        query = "inference disaggregation"
        cands = [
            _make_candidate("Alpha: inference disaggregation study", source_id="alpha"),
            _make_candidate("Beta: unrelated cooking blog post", source_id="beta"),
            _make_candidate("Gamma: memory disaggregation systems", source_id="gamma"),
        ]
        first = web_rank.score_candidates(cands, query)
        second = web_rank.score_candidates(cands, query)
        assert [c["source_id"] for c in first] == [c["source_id"] for c in second]
        assert [c["score"] for c in first] == [c["score"] for c in second]

    def test_registry_relevance_bonus_changes_order(self, monkeypatch):
        """Registry bonus should promote a lower-overlap candidate when relevance is high."""
        _ollama_off(monkeypatch)
        # Both candidates have ZERO keyword overlap with the query.
        # arxiv_cs_dc has relevance=5 -> bonus = (5/5)*0.25 = 0.25
        # unknown_src has no registry entry -> bonus = 0
        query = "xxxxxxxxxxxxxxxxxxxxxxxxxx"  # No overlap with anything
        high_rel = _make_candidate(
            title="AAAA BBBB CCCC",
            snippet="DDDD EEEE FFFF",
            source_id="arxiv_cs_dc",
        )
        no_rel = _make_candidate(
            title="AAAA BBBB CCCC",
            snippet="DDDD EEEE FFFF",
            source_id="unknown_src_xyz",
        )
        ranked = web_rank.score_candidates(
            [no_rel, high_rel], query, registry=_SAMPLE_REGISTRY
        )
        assert ranked[0]["source_id"] == "arxiv_cs_dc"
        assert ranked[0]["score"] > ranked[1]["score"]

    def test_recency_bonus_orders_equal_otherwise_candidates(self, monkeypatch):
        """More recent published date wins when keyword overlap and registry are equal."""
        _ollama_off(monkeypatch)
        today = date.today().isoformat()
        recent_pub = today
        old_pub = (date.today() - timedelta(days=180)).isoformat()

        # Identical title/snippet/source_id except published date
        recent = _make_candidate(
            title="Unique inference disaggregation topic",
            snippet="",
            source_id="src-same",
            published=recent_pub,
            url="https://example.com/recent",
        )
        old = _make_candidate(
            title="Unique inference disaggregation topic",
            snippet="",
            source_id="src-same",
            published=old_pub,
            url="https://example.com/old",
        )
        # Use a query that has no overlap so keyword component is 0 for both,
        # and both have the same source_id so registry bonus is equal.
        query = "xxxxxxxxxxxxxxxxxx"
        ranked = web_rank.score_candidates([old, recent], query)
        assert ranked[0]["url"] == "https://example.com/recent"
        assert ranked[0]["score"] > ranked[1]["score"]

    def test_missing_published_gets_zero_recency(self, monkeypatch):
        """A candidate with no published date should still score (0 recency bonus)."""
        _ollama_off(monkeypatch)
        query = "disaggregated inference"
        cand = _make_candidate(
            title="disaggregated inference systems",
            published="",
        )
        ranked = web_rank.score_candidates([cand], query)
        assert len(ranked) == 1
        assert ranked[0]["score"] >= 0

    def test_missing_snippet_key_handled(self, monkeypatch):
        """Candidate missing 'snippet' key entirely should not raise."""
        _ollama_off(monkeypatch)
        query = "inference"
        cand = {
            "title": "Inference at scale",
            "url": "https://example.com",
            "source_id": "s1",
            "id_type": "url",
            "published": "",
            "engine": "arxiv",
            # "snippet" intentionally missing
        }
        result = web_rank.score_candidates([cand], query)
        assert len(result) == 1
        assert "score" in result[0]

    def test_no_registry_is_fine(self, monkeypatch):
        """registry=None should not raise; relevance bonus is 0 for all."""
        _ollama_off(monkeypatch)
        query = "disaggregation"
        cands = [
            _make_candidate("disaggregation paper", source_id="a"),
            _make_candidate("unrelated post", source_id="b"),
        ]
        ranked = web_rank.score_candidates(cands, query, registry=None)
        assert len(ranked) == 2
        # Top candidate should still be the one with overlap
        assert ranked[0]["source_id"] == "a"

    def test_tie_broken_by_source_id_alphabetically(self, monkeypatch):
        """Equal scores should be broken by source_id (ascending) for determinism."""
        _ollama_off(monkeypatch)
        # Make three candidates with NO overlap to query and NO registry/recency bonus
        # They should all score 0.0 and be ordered alphabetically by source_id.
        query = "xxxxxxxxxxxxxxxxxxxx"
        cands = [
            _make_candidate("AAAA BBBB", source_id="zzz", published=""),
            _make_candidate("AAAA BBBB", source_id="aaa", published=""),
            _make_candidate("AAAA BBBB", source_id="mmm", published=""),
        ]
        ranked = web_rank.score_candidates(cands, query)
        source_ids = [c["source_id"] for c in ranked]
        assert source_ids == sorted(source_ids)

    def test_score_key_added_preserves_existing_keys(self, monkeypatch):
        """score_candidates adds 'score' without removing any existing keys."""
        _ollama_off(monkeypatch)
        query = "inference"
        cand = _make_candidate("inference paper", source_id="s1")
        cand["lane"] = "gap"
        cand["origin_ids"] = ["GAP-01"]
        result = web_rank.score_candidates([cand], query)
        c = result[0]
        assert "score" in c
        assert c.get("lane") == "gap"
        assert c.get("origin_ids") == ["GAP-01"]
        # All original web_harvest shape keys present
        for key in ("title", "url", "source_id", "id_type", "published", "snippet", "engine"):
            assert key in c

    def test_does_not_mutate_input_candidates(self, monkeypatch):
        """score_candidates must not mutate the caller's dicts."""
        _ollama_off(monkeypatch)
        query = "inference"
        cand = _make_candidate("inference paper", source_id="s1")
        original_keys = set(cand.keys())
        web_rank.score_candidates([cand], query)
        assert set(cand.keys()) == original_keys
        assert "score" not in cand


# ===========================================================================
# Embedding path tests
# ===========================================================================

class TestEmbeddingPath:
    """Monkeypatch ollama_alive -> True + embed_one -> canned vectors."""

    def test_cosine_similar_text_ranks_higher(self, monkeypatch):
        """Candidate textually similar to query should outscore a dissimilar one."""
        _ollama_on(monkeypatch)
        # The canned vector function uses character-class counts.
        # "aerate season" is heavy on vowels ae+io -> [1,0,...] type vector
        # "inference disaggregation" is a different mix
        # We control the query to be text-similar to the high candidate.
        query = "aerate season ionic ease"  # heavy ae/io vowels
        high = _make_candidate(
            title="aerate ease season",
            snippet="ionic ease season aerate",
            source_id="high",
        )
        low = _make_candidate(
            title="runtime nntr strong",
            snippet="construct strong nntr trnr",
            source_id="low",
        )
        ranked = web_rank.score_candidates(
            [low, high], query, allow_remote_ollama=False
        )
        # high candidate should rank first (its character mix matches query)
        assert ranked[0]["source_id"] == "high"

    def test_embedding_path_adds_score_key(self, monkeypatch):
        """Each candidate in the embedding path gets a 'score' float."""
        _ollama_on(monkeypatch)
        query = "inference"
        cands = [
            _make_candidate("inference disaggregation", source_id="a"),
            _make_candidate("cookie recipe", source_id="b"),
        ]
        ranked = web_rank.score_candidates(cands, query)
        for c in ranked:
            assert "score" in c
            assert isinstance(c["score"], float)

    def test_embedding_path_fallback_on_embed_failure(self, monkeypatch):
        """If embed_one raises for a candidate, it falls back to deterministic score."""
        _ollama_on(monkeypatch)
        call_count = {"n": 0}

        def _flaky_embed(url, model, text):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call is the query embed -- succeed
                return _canned_vector(text)
            # All candidate embeds fail
            raise RuntimeError("embed failed")

        monkeypatch.setattr(web_rank, "embed_one", _flaky_embed)

        query = "inference"
        cands = [
            _make_candidate("inference disaggregation", source_id="a"),
            _make_candidate("cookie recipe", source_id="b"),
        ]
        # Should not raise; all candidates fall back to deterministic
        ranked = web_rank.score_candidates(cands, query)
        assert len(ranked) == 2
        for c in ranked:
            assert "score" in c
            assert isinstance(c["score"], float)

    def test_embedding_path_no_network(self, monkeypatch):
        """Embedding path must not make real network calls (embed_one is patched)."""
        import urllib.request
        original_urlopen = urllib.request.urlopen

        def _should_not_call(*args, **kwargs):
            raise AssertionError("Real network call made in embedding test")

        monkeypatch.setattr(urllib.request, "urlopen", _should_not_call)
        _ollama_on(monkeypatch)

        query = "disaggregation"
        cands = [_make_candidate("disaggregation paper")]
        # Should complete without real network
        ranked = web_rank.score_candidates(cands, query)
        assert len(ranked) == 1

        # Restore
        monkeypatch.setattr(urllib.request, "urlopen", original_urlopen)


# ===========================================================================
# Edge-case / input-validation tests
# ===========================================================================

class TestEdgeCases:
    def test_empty_input_returns_empty_output(self, monkeypatch):
        _ollama_off(monkeypatch)
        assert web_rank.score_candidates([], "some query") == []

    def test_empty_query_scores_all_zero_kw(self, monkeypatch):
        """Empty query has no tokens; keyword overlap is 0 for all candidates."""
        _ollama_off(monkeypatch)
        cands = [
            _make_candidate("something interesting", source_id="a"),
            _make_candidate("another thing", source_id="b"),
        ]
        ranked = web_rank.score_candidates(cands, "")
        # Keyword component is 0 for all; order determined by registry+recency+tiebreak
        assert len(ranked) == 2

    def test_candidate_missing_published_key(self, monkeypatch):
        """Candidate completely lacking 'published' key should not raise."""
        _ollama_off(monkeypatch)
        cand = {
            "title": "Inference at scale",
            "url": "https://example.com",
            "source_id": "s1",
            "id_type": "url",
            "snippet": "some snippet",
            "engine": "arxiv",
            # "published" intentionally missing
        }
        result = web_rank.score_candidates([cand], "inference")
        assert len(result) == 1
        assert result[0]["score"] >= 0

    def test_single_candidate_returned(self, monkeypatch):
        """Single candidate should be returned wrapped in a list with score added."""
        _ollama_off(monkeypatch)
        cand = _make_candidate("disaggregation inference memory")
        result = web_rank.score_candidates([cand], "disaggregation inference")
        assert len(result) == 1
        assert "score" in result[0]

    def test_output_sorted_descending(self, monkeypatch):
        """Output list must be sorted by score descending."""
        _ollama_off(monkeypatch)
        query = "inference disaggregation memory"
        cands = [
            _make_candidate("cookie recipe blog post", source_id="z"),
            _make_candidate("inference memory disaggregation paper", source_id="a"),
            _make_candidate("somewhat related inference note", source_id="m"),
        ]
        ranked = web_rank.score_candidates(cands, query)
        scores = [c["score"] for c in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_today_parameter_affects_recency(self, monkeypatch):
        """Passing today= parameter should shift recency computation."""
        _ollama_off(monkeypatch)
        # Two candidates: one published 2026-01-01, one published 2026-06-01
        # With today=2026-06-01, the June one should be more recent.
        query = "xxxxxxxxxxxxxxxxxxxx"  # no keyword overlap
        jan = _make_candidate(
            "AAAA BBBB", source_id="jan", published="2026-01-01"
        )
        jun = _make_candidate(
            "AAAA BBBB", source_id="jun", published="2026-06-01"
        )
        ranked = web_rank.score_candidates([jan, jun], query, today="2026-06-22")
        assert ranked[0]["source_id"] == "jun"


# ===========================================================================
# Tokenizer unit tests
# ===========================================================================

class TestTokenize:
    def test_drops_short_tokens(self):
        tokens = web_rank._tokenize("is an on by to at")
        assert tokens == []

    def test_drops_stopwords(self):
        tokens = web_rank._tokenize("that this with from")
        assert tokens == []

    def test_keeps_long_non_stop_tokens(self):
        tokens = web_rank._tokenize("disaggregation inference cache")
        assert "disaggregation" in tokens
        assert "inference" in tokens
        assert "cache" in tokens

    def test_case_insensitive(self):
        tokens = web_rank._tokenize("Disaggregation INFERENCE Memory")
        assert "disaggregation" in tokens
        assert "inference" in tokens
        assert "memory" in tokens


# ===========================================================================
# Recency score unit tests
# ===========================================================================

class TestRecencyScore:
    def test_today_returns_one(self):
        today = date.today().isoformat()
        score = web_rank._recency_score(today, today)
        assert abs(score - 1.0) < 1e-6

    def test_half_life_returns_half(self):
        today = "2026-06-22"
        half_life_ago = (date.fromisoformat(today) - timedelta(days=int(web_rank.RECENCY_HALF_LIFE))).isoformat()
        score = web_rank._recency_score(half_life_ago, today)
        assert abs(score - 0.5) < 0.01

    def test_empty_published_returns_zero(self):
        assert web_rank._recency_score("", "2026-06-22") == 0.0

    def test_future_date_treated_as_today(self):
        today = "2026-06-22"
        future = "2027-01-01"
        score = web_rank._recency_score(future, today)
        # age_days clamped to 0, score = 1.0
        assert abs(score - 1.0) < 1e-6

    def test_iso_datetime_prefix_accepted(self):
        score = web_rank._recency_score("2026-06-22T12:00:00Z", "2026-06-22")
        assert abs(score - 1.0) < 1e-6


# ===========================================================================
# Registry relevance unit tests
# ===========================================================================

class TestRegistryRelevance:
    def test_known_source_id(self):
        score = web_rank._registry_relevance("arxiv_cs_dc", _SAMPLE_REGISTRY)
        assert abs(score - 1.0) < 1e-6  # relevance=5 -> 5/5=1.0

    def test_lower_relevance(self):
        score = web_rank._registry_relevance("arxiv_cs_ar", _SAMPLE_REGISTRY)
        assert abs(score - 0.6) < 1e-6  # relevance=3 -> 3/5=0.6

    def test_unknown_source_id_returns_zero(self):
        score = web_rank._registry_relevance("nonexistent_xyz", _SAMPLE_REGISTRY)
        assert score == 0.0

    def test_empty_registry_returns_zero(self):
        assert web_rank._registry_relevance("anything", {}) == 0.0
        assert web_rank._registry_relevance("anything", None) == 0.0

    def test_github_repos_section(self):
        score = web_rank._registry_relevance("vllm-project/vllm", _SAMPLE_REGISTRY)
        assert abs(score - 1.0) < 1e-6  # relevance=5


# ===========================================================================
# CLI tests
# ===========================================================================

class TestCli:
    def test_cli_emits_json_array(self, monkeypatch, capsys):
        """CLI reads stdin JSON, scores, and emits a JSON array."""
        _ollama_off(monkeypatch)
        cands = [
            _make_candidate("disaggregation inference", source_id="a"),
            _make_candidate("cookie recipe", source_id="b"),
        ]
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(cands)))
        rc = web_rank.main(["--query", "disaggregation inference"])
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert "score" in parsed[0]

    def test_cli_no_query_exits_nonzero(self, monkeypatch):
        """Missing --query should return 2."""
        rc = web_rank.main([])
        assert rc == 2

    def test_cli_invalid_json_stdin_exits_two(self, monkeypatch, capsys):
        """Non-JSON stdin should return 2."""
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json }{"))
        rc = web_rank.main(["--query", "test"])
        assert rc == 2

    def test_cli_non_list_json_stdin_exits_two(self, monkeypatch, capsys):
        """JSON object (not array) on stdin should return 2."""
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"key": "val"}'))
        rc = web_rank.main(["--query", "test"])
        assert rc == 2

    def test_cli_empty_candidates_returns_empty_list(self, monkeypatch, capsys):
        """Empty JSON array on stdin should emit [] and exit 0."""
        _ollama_off(monkeypatch)
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("[]"))
        rc = web_rank.main(["--query", "inference"])
        assert rc == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == []

    def test_cli_registry_dir_loads(self, monkeypatch, capsys, tmp_path):
        """--registry pointing at a directory with sources/ loads registry correctly."""
        _ollama_off(monkeypatch)
        # Build a mini registry directory
        sources_dir = tmp_path / "sources"
        sources_dir.mkdir()
        reg = {"sources": [{"id": "test_src", "relevance": 5}]}
        (sources_dir / "paper-publisher.json").write_text(json.dumps(reg))

        cands = [_make_candidate("inference", source_id="test_src")]
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(cands)))
        rc = web_rank.main(["--query", "inference", "--registry", str(tmp_path)])
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert len(parsed) == 1
        assert "score" in parsed[0]


# ===========================================================================
# Phase-4A learned-prior tests
# ===========================================================================

def _make_learned(source_id: str, rep: float, keywords: list | None = None) -> dict:
    """Build a minimal learned dict seeded with one source reputation."""
    return {
        "updated": "2026-06-24",
        "sources": {
            source_id: {"accept": 3, "reject": 0, "rep": rep},
        },
        "engines": {},
        "reject_patterns": {"keywords": keywords or []},
        "calibration": {"accepted_score_mean": 0.7, "rejected_score_mean": 0.3, "n": 3},
        "processed_ids": [],
    }


class TestLearnedPrior:
    """score_candidates with a learned dict applies rep and reject adjustments."""

    def test_positive_rep_boosts_score(self, monkeypatch):
        """A source with positive rep scores higher than the same source with no learned."""
        _ollama_off(monkeypatch)
        query = "xxxxxxxxxxxxxxxxxxxx"  # no keyword overlap -> base score ~0
        cand = _make_candidate("AAAA", source_id="good_source")

        no_learned = web_rank.score_candidates([cand], query)
        with_learned = web_rank.score_candidates(
            [cand], query, learned=_make_learned("good_source", rep=+0.6)
        )
        assert with_learned[0]["score"] > no_learned[0]["score"]

    def test_high_rep_source_outranks_neutral(self, monkeypatch):
        """With learned, a high-rep source ranks above an otherwise-equal neutral source."""
        _ollama_off(monkeypatch)
        query = "xxxxxxxxxxxxxxxxxxxx"  # identical zero-overlap query for both
        high_rep = _make_candidate("AAAA", source_id="good_src", url="https://example.com/a")
        neutral = _make_candidate("AAAA", source_id="neutral_src", url="https://example.com/b")

        learned = _make_learned("good_src", rep=+0.8)
        ranked = web_rank.score_candidates([neutral, high_rep], query, learned=learned)
        assert ranked[0]["source_id"] == "good_src"
        assert ranked[0]["score"] > ranked[1]["score"]

    def test_reject_keyword_lowers_score(self, monkeypatch):
        """A candidate whose title hits a reject keyword scores lower than without learned."""
        _ollama_off(monkeypatch)
        query = "disaggregated inference"
        cand = _make_candidate(
            title="clickbait guide to inference",
            source_id="any_src",
        )
        learned = _make_learned("other_src", rep=0.0, keywords=["clickbait"])

        no_learned = web_rank.score_candidates([cand], query)
        with_learned = web_rank.score_candidates([cand], query, learned=learned)
        assert with_learned[0]["score"] < no_learned[0]["score"]

    def test_reject_keyword_candidate_outranked_by_clean(self, monkeypatch):
        """A clean candidate ranks above a keyword-penalised one when learned is active."""
        _ollama_off(monkeypatch)
        query = "xxxxxxxxxxxxxxxxxxxx"
        dirty = _make_candidate("clickbait inference", source_id="s1", url="https://example.com/d")
        clean = _make_candidate("AAAA BBBB", source_id="s2", url="https://example.com/c")

        learned = _make_learned("s1", rep=0.0, keywords=["clickbait"])
        ranked = web_rank.score_candidates([dirty, clean], query, learned=learned)
        # clean should rank above dirty (dirty is penalised)
        clean_idx = next(i for i, c in enumerate(ranked) if c["source_id"] == "s2")
        dirty_idx = next(i for i, c in enumerate(ranked) if c["source_id"] == "s1")
        assert clean_idx < dirty_idx, (
            f"Expected clean (idx {clean_idx}) before dirty (idx {dirty_idx})"
        )

    def test_learned_none_identical_to_no_learned(self, monkeypatch):
        """learned=None produces identical scores to omitting the argument."""
        _ollama_off(monkeypatch)
        query = "inference disaggregation"
        cands = [
            _make_candidate("inference disaggregation paper", source_id="a"),
            _make_candidate("cookie recipe blog post", source_id="b"),
        ]
        without = web_rank.score_candidates(cands, query)
        with_none = web_rank.score_candidates(cands, query, learned=None)
        assert [c["score"] for c in without] == [c["score"] for c in with_none]
        assert [c["source_id"] for c in without] == [c["source_id"] for c in with_none]

    def test_weights_override_defaults(self, monkeypatch):
        """weights dict overrides default w_rep/w_rej values."""
        _ollama_off(monkeypatch)
        query = "xxxxxxxxxxxxxxxxxxxx"
        cand = _make_candidate("AAAA", source_id="good_src")
        learned = _make_learned("good_src", rep=+0.5)

        # Zero weight: reputation has no effect
        zero_weights = {"w_rep": 0.0, "w_rej": 0.0}
        no_weight_score = web_rank.score_candidates([cand], query, learned=learned, weights=zero_weights)[0]["score"]

        # High weight: rep has large effect
        high_weights = {"w_rep": 1.0, "w_rej": 0.0}
        high_weight_score = web_rank.score_candidates([cand], query, learned=learned, weights=high_weights)[0]["score"]

        assert high_weight_score > no_weight_score

    def test_base_score_preserved_in_output(self, monkeypatch):
        """When learned is used, base_score key is present on each candidate."""
        _ollama_off(monkeypatch)
        query = "inference"
        cand = _make_candidate("inference paper", source_id="s1")
        learned = _make_learned("s1", rep=+0.3)
        ranked = web_rank.score_candidates([cand], query, learned=learned)
        assert "base_score" in ranked[0], "base_score should be present when learned is used"

    def test_does_not_crash_without_agent_learn_import(self, monkeypatch):
        """If agent_learn cannot be imported, score_candidates falls back gracefully."""
        _ollama_off(monkeypatch)
        # Temporarily break agent_learn import
        import sys
        original = sys.modules.get("agent_learn")
        sys.modules["agent_learn"] = None  # type: ignore[assignment]
        try:
            query = "inference"
            cand = _make_candidate("inference paper", source_id="s1")
            learned = _make_learned("s1", rep=+0.5)
            # Should not raise
            ranked = web_rank.score_candidates([cand], query, learned=learned)
            assert len(ranked) == 1
            assert "score" in ranked[0]
        finally:
            if original is None:
                sys.modules.pop("agent_learn", None)
            else:
                sys.modules["agent_learn"] = original
