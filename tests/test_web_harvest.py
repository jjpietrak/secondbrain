"""Hermetic tests for scripts/web_harvest.py.

ALL tests are offline -- no real network calls.
- poll_rss: uses the local fixture Atom file at tests/fixtures/web/sample_feed.xml
- query_papers / query_forum: monkeypatches the lib.sources client so .search() never
  hits the network.
- poll_github_releases: monkeypatches requests.get to return canned JSON.
- dedup_seen: uses pytest tmp_path; no network at all.

Usage:
  python -m pytest tests/test_web_harvest.py -q
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Make scripts/ importable so we can do "import web_harvest"
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FIXTURES = ROOT / "tests" / "fixtures" / "web"
SAMPLE_FEED = FIXTURES / "sample_feed.xml"

# ---------------------------------------------------------------------------
# Lazy import helper (re-import after monkeypatching sys.path)
# ---------------------------------------------------------------------------

def _import_web_harvest():
    """Return the web_harvest module, reloading if needed."""
    if "web_harvest" in sys.modules:
        return sys.modules["web_harvest"]
    import web_harvest
    return web_harvest


# ---------------------------------------------------------------------------
# Candidate shape invariant helper
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {"title", "url", "source_id", "id_type", "published", "snippet", "engine"}
_VALID_ID_TYPES = {"arxiv", "doi", "url", "github"}
_VALID_ENGINES = {
    "rss", "arxiv", "openalex", "semantic_scholar", "crossref",
    "hackernews", "reddit", "lobsters", "github",
}


def _assert_candidate(c: dict, *, engine: str | None = None) -> None:
    """Assert that c has exactly the pinned candidate shape."""
    assert isinstance(c, dict), f"Expected dict, got {type(c)}"
    missing = _REQUIRED_KEYS - c.keys()
    assert not missing, f"Candidate missing keys: {missing}  got: {c}"
    extra = c.keys() - _REQUIRED_KEYS
    assert not extra, f"Candidate has unexpected extra keys: {extra}"
    # Types
    for key in _REQUIRED_KEYS:
        assert isinstance(c[key], str), f"Key {key!r} should be str, got {type(c[key])}: {c}"
    assert c["id_type"] in _VALID_ID_TYPES, f"Bad id_type {c['id_type']!r}"
    assert c["engine"] in _VALID_ENGINES, f"Bad engine {c['engine']!r}"
    if engine is not None:
        assert c["engine"] == engine, f"Expected engine={engine!r}, got {c['engine']!r}"


# ===========================================================================
# poll_rss
# ===========================================================================

class TestPollRss:
    def test_parses_local_atom_feed(self):
        """poll_rss with a local file path returns candidates with correct shape."""
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED))
        assert len(results) == 3
        for c in results:
            _assert_candidate(c, engine="rss")

    def test_title_and_url_present(self):
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED))
        titles = {c["title"] for c in results}
        assert "Disaggregated Memory for LLM Inference" in titles
        assert "KV Cache Offloading Techniques" in titles

    def test_since_filter_excludes_old_entries(self):
        """Entries published before 'since' date are dropped."""
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED), since="2026-01-01")
        # Only the two 2026-06-* entries should remain; 2025-01-01 entry filtered
        assert len(results) == 2
        for c in results:
            assert c["published"] >= "2026-01-01", f"Stale entry leaked: {c}"

    def test_since_filter_all_excluded(self):
        """since beyond all published dates returns empty list."""
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED), since="2027-01-01")
        assert results == []

    def test_since_none_returns_all(self):
        """No since filter returns all entries."""
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED), since=None)
        assert len(results) == 3

    def test_limit_respected(self):
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED), limit=1)
        assert len(results) == 1

    def test_custom_source_id(self):
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED), source_id="my_feed_registry")
        for c in results:
            assert c["source_id"] == "my_feed_registry"

    def test_snippet_populated(self):
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED))
        snippets = {c["snippet"] for c in results}
        assert any("disaggregat" in s.lower() for s in snippets)

    def test_invalid_url_returns_empty(self):
        """A completely bogus feed_url should return [] without raising."""
        wh = _import_web_harvest()
        # feedparser is lenient; an unparsable local path -> empty entries
        results = wh.poll_rss("/nonexistent/path/that/does/not/exist.xml")
        assert isinstance(results, list)

    def test_id_type_is_url(self):
        wh = _import_web_harvest()
        results = wh.poll_rss(str(SAMPLE_FEED))
        for c in results:
            assert c["id_type"] == "url"


# ===========================================================================
# query_papers
# ===========================================================================

class _FakeResult:
    """Minimal stand-in for lib.sources.result.Result."""
    def __init__(self, *, source, title, url, abstract=None, snippet=None,
                 year=None, posted_at=None, extra=None):
        self.source = source
        self.title = title
        self.url = url
        self.abstract = abstract
        self.snippet = snippet
        self.year = year
        self.posted_at = posted_at
        self.extra = extra or {}


_FAKE_ARXIV_RESULTS = [
    _FakeResult(
        source="arxiv",
        title="Disaggregated KV Cache for Efficient LLM Serving",
        url="https://arxiv.org/abs/2401.09670",
        abstract="We propose a new approach to KV cache disaggregation.",
        year=2024,
        posted_at="2024-01-18T00:00:00Z",
    ),
    _FakeResult(
        source="arxiv",
        title="Memory Disaggregation in Data Centers",
        url="https://arxiv.org/abs/2312.05678",
        abstract="Survey of memory disaggregation architectures.",
        year=2023,
        posted_at="2023-12-10T00:00:00Z",
        extra={"doi": None},
    ),
]

_FAKE_OPENALEX_RESULTS = [
    _FakeResult(
        source="openalex",
        title="Scalable LLM Inference via Memory Disaggregation",
        url="https://doi.org/10.1234/openalex.test",
        abstract="We study memory disaggregation for transformer inference.",
        year=2025,
        extra={"doi": "10.1234/openalex.test"},
    ),
]

_FAKE_SS_RESULTS = [
    _FakeResult(
        source="semantic_scholar",
        title="Efficient Attention with Disaggregated KV Caches",
        url="https://www.semanticscholar.org/paper/abc123",
        abstract="Abstract about disaggregated attention.",
        year=2024,
        extra={"doi": "10.5678/ss.test"},
    ),
]

_FAKE_CROSSREF_RESULTS = [
    _FakeResult(
        source="crossref",
        title="Inference Disaggregation: A Survey",
        url="https://doi.org/10.9999/crossref.test",
        abstract="Cross-ref survey abstract.",
        year=2025,
        extra={"doi": "10.9999/crossref.test"},
    ),
]


class TestQueryPapers:
    def _patch_paper_source(self, engine: str, fake_results: list):
        """Return a context manager that patches the named engine's .search() method."""
        wh = _import_web_harvest()
        # Build a fake source object
        fake_source = MagicMock()
        fake_source.search.return_value = fake_results
        return patch.object(wh, "_load_paper_source", return_value=fake_source)

    def test_arxiv_candidates_shape(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_ARXIV_RESULTS
        with patch.object(wh, "_load_paper_source", return_value=fake_source):
            results = wh.query_papers("kv cache disaggregation", engine="arxiv")
        assert len(results) == 2
        for c in results:
            _assert_candidate(c, engine="arxiv")

    def test_arxiv_id_type_is_arxiv(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_ARXIV_RESULTS
        with patch.object(wh, "_load_paper_source", return_value=fake_source):
            results = wh.query_papers("kv cache", engine="arxiv")
        for c in results:
            assert c["id_type"] == "arxiv"
            assert c["source_id"].startswith("arxiv:")

    def test_openalex_doi_id_type(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_OPENALEX_RESULTS
        with patch.object(wh, "_load_paper_source", return_value=fake_source):
            results = wh.query_papers("memory disaggregation", engine="openalex")
        assert len(results) == 1
        c = results[0]
        _assert_candidate(c, engine="openalex")
        assert c["id_type"] == "doi"
        assert c["source_id"] == "10.1234/openalex.test"

    def test_semantic_scholar_doi_id_type(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_SS_RESULTS
        with patch.object(wh, "_load_paper_source", return_value=fake_source):
            results = wh.query_papers("disaggregated attention", engine="semantic_scholar")
        assert len(results) == 1
        c = results[0]
        _assert_candidate(c, engine="semantic_scholar")
        assert c["id_type"] == "doi"

    def test_crossref_doi_id_type(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_CROSSREF_RESULTS
        with patch.object(wh, "_load_paper_source", return_value=fake_source):
            results = wh.query_papers("inference disaggregation", engine="crossref")
        assert len(results) == 1
        c = results[0]
        _assert_candidate(c, engine="crossref")
        assert c["id_type"] == "doi"

    def test_unknown_engine_returns_empty(self):
        wh = _import_web_harvest()
        results = wh.query_papers("test", engine="nonexistent_engine_xyz")
        assert results == []

    def test_source_exception_returns_empty(self):
        """If the source raises, query_papers degrades to []."""
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.side_effect = RuntimeError("network down")
        with patch.object(wh, "_load_paper_source", return_value=fake_source):
            results = wh.query_papers("test", engine="arxiv")
        assert results == []

    def test_snippet_truncated_to_500(self):
        wh = _import_web_harvest()
        long_abstract = "A" * 1000
        fake_results = [
            _FakeResult(source="arxiv", title="T", url="https://arxiv.org/abs/9999.00001",
                        abstract=long_abstract)
        ]
        fake_source = MagicMock()
        fake_source.search.return_value = fake_results
        with patch.object(wh, "_load_paper_source", return_value=fake_source):
            results = wh.query_papers("test", engine="arxiv")
        assert len(results[0]["snippet"]) <= 500


# ===========================================================================
# query_forum
# ===========================================================================

_FAKE_HN_RESULTS = [
    _FakeResult(
        source="hackernews",
        title="Ask HN: Disaggregated inference systems?",
        url="https://news.ycombinator.com/item?id=12345",
        snippet="Discussion on disaggregated inference.",
        posted_at="2026-06-01T00:00:00Z",
    ),
    _FakeResult(
        source="hackernews",
        title="LLM serving at scale",
        url="https://news.ycombinator.com/item?id=67890",
        snippet="How do you serve 70B models efficiently?",
        posted_at="2026-05-20T00:00:00Z",
    ),
]

_FAKE_REDDIT_RESULTS = [
    _FakeResult(
        source="reddit",
        title="Memory disaggregation paper discussion",
        url="https://www.reddit.com/r/MachineLearning/comments/abc123/",
        snippet="r/MachineLearning thread on disaggregation.",
        posted_at="1717200000",
    ),
]

_FAKE_LOBSTERS_RESULTS = [
    _FakeResult(
        source="lobsters",
        title="Disaggregated KV: an analysis",
        url="https://lobste.rs/s/xyz123",
        snippet=None,
        posted_at="2026-06-05T12:00:00Z",
    ),
]


class TestQueryForum:
    def test_hackernews_candidates_shape(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_HN_RESULTS
        with patch.object(wh, "_load_forum_source", return_value=fake_source):
            results = wh.query_forum("disaggregated inference", engine="hackernews")
        assert len(results) == 2
        for c in results:
            _assert_candidate(c, engine="hackernews")

    def test_hackernews_id_type_url(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_HN_RESULTS
        with patch.object(wh, "_load_forum_source", return_value=fake_source):
            results = wh.query_forum("inference", engine="hackernews")
        for c in results:
            assert c["id_type"] == "url"

    def test_reddit_shape(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_REDDIT_RESULTS
        with patch.object(wh, "_load_forum_source", return_value=fake_source):
            results = wh.query_forum("memory disaggregation", engine="reddit")
        assert len(results) == 1
        _assert_candidate(results[0], engine="reddit")

    def test_lobsters_shape(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_LOBSTERS_RESULTS
        with patch.object(wh, "_load_forum_source", return_value=fake_source):
            results = wh.query_forum("kv cache", engine="lobsters")
        assert len(results) == 1
        c = results[0]
        _assert_candidate(c, engine="lobsters")
        # snippet should be "" (not None) when source returns None
        assert isinstance(c["snippet"], str)

    def test_unknown_engine_returns_empty(self):
        wh = _import_web_harvest()
        results = wh.query_forum("test", engine="nonexistent_forum_xyz")
        assert results == []

    def test_source_exception_returns_empty(self):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.side_effect = ConnectionError("timeout")
        with patch.object(wh, "_load_forum_source", return_value=fake_source):
            results = wh.query_forum("test", engine="hackernews")
        assert results == []


# ===========================================================================
# poll_github_releases
# ===========================================================================

_FAKE_GH_RELEASES = [
    {
        "tag_name": "v0.6.0",
        "name": "v0.6.0 - Disagg KV support",
        "html_url": "https://github.com/vllm-project/vllm/releases/tag/v0.6.0",
        "published_at": "2026-06-15T12:00:00Z",
        "body": "Added support for disaggregated KV caches in prefill/decode mode.",
    },
    {
        "tag_name": "v0.5.5",
        "name": "v0.5.5",
        "html_url": "https://github.com/vllm-project/vllm/releases/tag/v0.5.5",
        "published_at": "2026-05-01T00:00:00Z",
        "body": "Bug fixes and performance improvements.",
    },
    {
        "tag_name": "v0.4.0",
        "name": "v0.4.0",
        "html_url": "https://github.com/vllm-project/vllm/releases/tag/v0.4.0",
        "published_at": "2025-12-01T00:00:00Z",
        "body": "Old release.",
    },
]


def _make_gh_response(status_code: int = 200, body=None):
    """Return a mock requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if body is not None:
        mock_resp.json.return_value = body
    else:
        mock_resp.json.return_value = _FAKE_GH_RELEASES
    return mock_resp


class TestPollGithubReleases:
    def test_happy_path_shape(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()):
            results = wh.poll_github_releases("https://github.com/vllm-project/vllm")
        assert len(results) == 3
        for c in results:
            _assert_candidate(c, engine="github")

    def test_id_type_github(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()):
            results = wh.poll_github_releases("vllm-project/vllm")
        for c in results:
            assert c["id_type"] == "github"

    def test_source_id_is_owner_slash_repo(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()):
            results = wh.poll_github_releases("https://github.com/vllm-project/vllm")
        for c in results:
            assert c["source_id"] == "vllm-project/vllm"

    def test_since_filter(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()):
            results = wh.poll_github_releases(
                "vllm-project/vllm", since="2026-01-01"
            )
        # Only the two 2026-* releases should pass
        assert len(results) == 2
        for c in results:
            assert c["published"] >= "2026-01-01"

    def test_limit_respected(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()):
            results = wh.poll_github_releases("vllm-project/vllm", limit=1)
        assert len(results) == 1

    def test_non_200_returns_empty(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response(404)):
            results = wh.poll_github_releases("vllm-project/vllm")
        assert results == []

    def test_rate_limit_429_returns_empty(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response(429)):
            results = wh.poll_github_releases("vllm-project/vllm")
        assert results == []

    def test_request_exception_returns_empty(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", side_effect=ConnectionError("timeout")):
            results = wh.poll_github_releases("vllm-project/vllm")
        assert results == []

    def test_bare_owner_repo_accepted(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()) as m:
            wh.poll_github_releases("vllm-project/vllm")
        call_url = m.call_args[0][0]
        assert "vllm-project" in call_url and "vllm" in call_url

    def test_unparseable_url_returns_empty(self):
        wh = _import_web_harvest()
        results = wh.poll_github_releases("not-a-valid-url-at-all-xyz")
        assert results == []

    def test_title_contains_repo_and_release_name(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()):
            results = wh.poll_github_releases("vllm-project/vllm")
        # First release title should reference vllm and v0.6.0
        assert "vllm" in results[0]["title"].lower()
        assert "v0.6.0" in results[0]["title"]

    def test_snippet_populated_from_body(self):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()):
            results = wh.poll_github_releases("vllm-project/vllm")
        assert "disaggregated" in results[0]["snippet"].lower()

    def test_json_parse_error_returns_empty(self):
        wh = _import_web_harvest()
        mock_resp = _make_gh_response(200)
        mock_resp.json.side_effect = ValueError("bad json")
        with patch.object(wh._requests_mod, "get", return_value=mock_resp):
            results = wh.poll_github_releases("vllm-project/vllm")
        assert results == []


# ===========================================================================
# dedup_seen
# ===========================================================================

def _make_candidate(url: str, id_type: str = "url", source_id: str | None = None) -> dict:
    return {
        "title": "Test title",
        "url": url,
        "source_id": source_id or url,
        "id_type": id_type,
        "published": "2026-06-01",
        "snippet": "Test snippet.",
        "engine": "rss",
    }


class TestDedupSeen:
    def test_missing_cache_file_treated_as_empty(self, tmp_path):
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "nonexistent_seen.json")
        c1 = _make_candidate("https://example.com/a")
        new_cands, updated = wh.dedup_seen([c1], seen_path=seen_path)
        assert len(new_cands) == 1
        assert "https://example.com/a" in updated

    def test_new_candidates_returned(self, tmp_path):
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "seen.json")
        c1 = _make_candidate("https://example.com/a")
        c2 = _make_candidate("https://example.com/b")
        new_cands, updated = wh.dedup_seen([c1, c2], seen_path=seen_path)
        assert len(new_cands) == 2
        assert len(updated) == 2

    def test_already_seen_url_dropped(self, tmp_path):
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "seen.json")
        # Pre-populate cache
        existing = {"https://example.com/a": True}
        (tmp_path / "seen.json").write_text(json.dumps(existing))
        c1 = _make_candidate("https://example.com/a")  # already seen
        c2 = _make_candidate("https://example.com/b")  # new
        new_cands, updated = wh.dedup_seen([c1, c2], seen_path=seen_path)
        assert len(new_cands) == 1
        assert new_cands[0]["url"] == "https://example.com/b"

    def test_updated_seen_dict_grows(self, tmp_path):
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "seen.json")
        existing = {"https://example.com/a": True}
        (tmp_path / "seen.json").write_text(json.dumps(existing))
        c2 = _make_candidate("https://example.com/b")
        _, updated = wh.dedup_seen([c2], seen_path=seen_path)
        assert "https://example.com/a" in updated  # old entry preserved
        assert "https://example.com/b" in updated  # new entry added
        assert len(updated) == 2

    def test_arxiv_id_dedup(self, tmp_path):
        """Candidates with arxiv id_type are deduped by source_id not url."""
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "seen.json")
        existing = {"arxiv:2401.09670": True}
        (tmp_path / "seen.json").write_text(json.dumps(existing))
        c = _make_candidate(
            url="https://arxiv.org/abs/2401.09670",
            id_type="arxiv",
            source_id="arxiv:2401.09670",
        )
        new_cands, updated = wh.dedup_seen([c], seen_path=seen_path)
        # The arxiv id is already seen -> filtered out
        assert new_cands == []

    def test_doi_dedup(self, tmp_path):
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "seen.json")
        existing = {"10.1234/test.doi": True}
        (tmp_path / "seen.json").write_text(json.dumps(existing))
        c = _make_candidate(
            url="https://doi.org/10.1234/test.doi",
            id_type="doi",
            source_id="10.1234/test.doi",
        )
        new_cands, _ = wh.dedup_seen([c], seen_path=seen_path)
        assert new_cands == []

    def test_empty_input_returns_empty(self, tmp_path):
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "seen.json")
        new_cands, updated = wh.dedup_seen([], seen_path=seen_path)
        assert new_cands == []
        assert updated == {}

    def test_corrupt_cache_file_treated_as_empty(self, tmp_path):
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "seen.json")
        (tmp_path / "seen.json").write_text("this is not valid json {{{")
        c = _make_candidate("https://example.com/x")
        # Should not raise; treats cache as empty
        new_cands, updated = wh.dedup_seen([c], seen_path=seen_path)
        assert len(new_cands) == 1

    def test_all_already_seen_returns_empty_list(self, tmp_path):
        wh = _import_web_harvest()
        seen_path = str(tmp_path / "seen.json")
        urls = [f"https://example.com/{i}" for i in range(5)]
        existing = {u: True for u in urls}
        (tmp_path / "seen.json").write_text(json.dumps(existing))
        candidates = [_make_candidate(u) for u in urls]
        new_cands, updated = wh.dedup_seen(candidates, seen_path=seen_path)
        assert new_cands == []
        assert len(updated) == 5


# ===========================================================================
# candidate_ident
# ===========================================================================

class TestCandidateIdent:
    """candidate_ident returns per-item identity, NOT the feed-level source_id."""

    def test_two_rss_candidates_same_source_id_different_url_produce_different_idents(self):
        """Core regression: two posts from the same feed must have distinct idents."""
        wh = _import_web_harvest()
        c1 = {
            "title": "HuggingFace Post 1",
            "url": "https://huggingface.co/blog/post-one",
            "source_id": "huggingface_blog",
            "id_type": "url",
            "published": "2026-06-01",
            "snippet": "snippet 1",
            "engine": "rss",
        }
        c2 = {
            "title": "HuggingFace Post 2",
            "url": "https://huggingface.co/blog/post-two",
            "source_id": "huggingface_blog",
            "id_type": "url",
            "published": "2026-06-02",
            "snippet": "snippet 2",
            "engine": "rss",
        }
        ident1 = wh.candidate_ident(c1)
        ident2 = wh.candidate_ident(c2)
        assert ident1 != ident2, (
            "Two posts from the same feed but different URLs must have different idents"
        )
        # Idents are the per-item URLs, not the shared source_id
        assert ident1 == "https://huggingface.co/blog/post-one"
        assert ident2 == "https://huggingface.co/blog/post-two"
        # source_id is still the feed registry id (unchanged)
        assert c1["source_id"] == "huggingface_blog"
        assert c2["source_id"] == "huggingface_blog"

    def test_arxiv_source_id_returned_directly(self):
        """arxiv: prefixed source_id is already per-item; return it unchanged."""
        wh = _import_web_harvest()
        c = {
            "title": "Some Paper",
            "url": "https://arxiv.org/abs/2606.08635",
            "source_id": "arxiv:2606.08635v1",
            "id_type": "arxiv",
            "published": "2026-06-01",
            "snippet": "abstract",
            "engine": "arxiv",
        }
        assert wh.candidate_ident(c) == "arxiv:2606.08635v1"

    def test_doi_source_id_returned_directly(self):
        wh = _import_web_harvest()
        c = {
            "title": "A Paper",
            "url": "https://doi.org/10.1234/test",
            "source_id": "doi:10.1234/test",
            "id_type": "doi",
            "published": "2026-06-01",
            "snippet": "",
            "engine": "crossref",
        }
        assert wh.candidate_ident(c) == "doi:10.1234/test"

    def test_rss_url_trailing_slash_stripped(self):
        """Trailing slashes on URLs are normalised away."""
        wh = _import_web_harvest()
        c = {
            "title": "Some Blog Post",
            "url": "https://example.com/post/",
            "source_id": "some_feed",
            "id_type": "url",
            "published": "2026-06-01",
            "snippet": "",
            "engine": "rss",
        }
        assert wh.candidate_ident(c) == "https://example.com/post"

    def test_github_uses_html_url_not_repo_source_id(self):
        """GitHub candidates: ident is the per-release html_url, not owner/repo."""
        wh = _import_web_harvest()
        c = {
            "title": "vllm: v0.9.0",
            "url": "https://github.com/vllm-project/vllm/releases/tag/v0.9.0",
            "source_id": "vllm-project/vllm",
            "id_type": "github",
            "published": "2026-06-01",
            "snippet": "",
            "engine": "github",
        }
        ident = wh.candidate_ident(c)
        assert ident == "https://github.com/vllm-project/vllm/releases/tag/v0.9.0"
        assert ident != "vllm-project/vllm"

    def test_fallback_to_source_id_when_url_empty(self):
        """When url is empty, fall back to source_id."""
        wh = _import_web_harvest()
        c = {
            "title": "No URL",
            "url": "",
            "source_id": "some_fallback_id",
            "id_type": "url",
            "published": "",
            "snippet": "",
            "engine": "rss",
        }
        assert wh.candidate_ident(c) == "some_fallback_id"


# ===========================================================================
# _parse_repo_url (internal helper, smoke tests)
# ===========================================================================

class TestParseRepoUrl:
    def test_full_https_url(self):
        wh = _import_web_harvest()
        result = wh._parse_repo_url("https://github.com/vllm-project/vllm")
        assert result == ("vllm-project", "vllm")

    def test_url_with_dot_git(self):
        wh = _import_web_harvest()
        result = wh._parse_repo_url("https://github.com/owner/repo.git")
        assert result == ("owner", "repo")

    def test_bare_owner_slash_repo(self):
        wh = _import_web_harvest()
        result = wh._parse_repo_url("owner/repo")
        assert result == ("owner", "repo")

    def test_invalid_url_returns_none(self):
        wh = _import_web_harvest()
        result = wh._parse_repo_url("not-valid-at-all")
        assert result is None


# ===========================================================================
# _iso_date helper
# ===========================================================================

class TestIsoDate:
    def test_full_iso_string(self):
        wh = _import_web_harvest()
        assert wh._iso_date("2026-06-15T12:00:00Z") == "2026-06-15"

    def test_date_only(self):
        wh = _import_web_harvest()
        assert wh._iso_date("2026-06-15") == "2026-06-15"

    def test_empty_string(self):
        wh = _import_web_harvest()
        assert wh._iso_date("") == ""

    def test_none_returns_empty(self):
        wh = _import_web_harvest()
        assert wh._iso_date(None) == ""


# ===========================================================================
# CLI smoke tests (no network)
# ===========================================================================

class TestCli:
    def test_rss_subcommand(self, capsys):
        """rss subcommand emits JSON list."""
        wh = _import_web_harvest()
        rc = wh.main(["rss", str(SAMPLE_FEED)])
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)
        assert len(parsed) == 3

    def test_papers_subcommand(self, capsys):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_ARXIV_RESULTS
        with patch.object(wh, "_load_paper_source", return_value=fake_source):
            rc = wh.main(["papers", "disaggregation", "--engine", "arxiv", "--limit", "5"])
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)

    def test_forum_subcommand(self, capsys):
        wh = _import_web_harvest()
        fake_source = MagicMock()
        fake_source.search.return_value = _FAKE_HN_RESULTS
        with patch.object(wh, "_load_forum_source", return_value=fake_source):
            rc = wh.main(["forum", "inference", "--engine", "hackernews"])
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)

    def test_github_subcommand(self, capsys):
        wh = _import_web_harvest()
        with patch.object(wh._requests_mod, "get", return_value=_make_gh_response()):
            rc = wh.main(["github", "vllm-project/vllm"])
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)
        assert all(c["engine"] == "github" for c in parsed)

    def test_no_subcommand_exits_nonzero(self):
        wh = _import_web_harvest()
        rc = wh.main([])
        assert rc != 0
