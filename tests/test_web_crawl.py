"""tests/test_web_crawl.py -- hermetic, offline tests for scripts/web_crawl.py.

ALL tests are offline:
- web_harvest functions (poll_rss / query_papers / query_forum / poll_github_releases)
  are monkeypatched to return canned candidates -- NO network calls.
- web_rank.score_candidates is monkeypatched to return input with deterministic scores.
- ingest_index is pointed at a temporary vault directory.

Test coverage:
  1. dry_run returns selected <=new_sources_total, valid lane mix, writes nothing.
  2. live run enqueues each selected id in ingest index AND writes nightly report.
  3. Graceful degradation: a harvest function that raises still lets crawl complete.
  4. PURPOSE loading: from objective/purpose/PURPOSE.md, vault.yaml fallback, and ''.
  5. seen-cache dedup: already-seen candidates are filtered before selection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import importlib

import pytest

# ---------------------------------------------------------------------------
# Make scripts/ and repo root importable
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import web_crawl  # noqa: E402


# ---------------------------------------------------------------------------
# Shared vault/config fixtures
# ---------------------------------------------------------------------------

_GAPS_MD = """\
---
type: gaps_report
created: 2026-06-01
vault: test-vault
---

## Knowledge Gaps

### GAP-01: Disaggregated inference scheduling gaps
- shows_up_in: [[wiki/concepts/inference]]
- missing: survey of P/D disaggregation scheduling algorithms
- fillable_by: arxiv (papers on disaggregated serving)
- topic: T-0001
- priority: high

### GAP-02: Optical interconnect prior art
- shows_up_in: [[wiki/sources/photons-to-tokens]]
- missing: four cited optical AI papers not yet ingested
- fillable_by: arxiv, github
- topic: T-0002
- priority: medium
"""

_DIR_0001_MD = """\
---
type: direction
id: DIR-0001
created: 2026-06-01
updated: 2026-06-01
serves_question: Q-0001
topics: T-0001
targets_gap: disaggregated inference P/D scheduling
priority: high
status: open
written_by: research
---

## reasoning_pattern

Investigate disaggregated serving approaches.

## seed_queries

- arxiv: disaggregated LLM inference prefill decode
- disaggregated serving heterogeneous GPU

## expected_evidence

Papers describing P/D disaggregation with latency measurements.

## solves_when

Have ingested 3+ primary sources on disaggregated serving.
"""

_PURPOSE_TEXT = (
    "Build a comprehensive knowledge base on disaggregated LLM inference "
    "and heterogeneous AI accelerator architectures."
)

# Minimal web-config.json (mirrors real structure)
_WEB_CONFIG = {
    "new_sources_total": 5,
    "lanes": {
        "gap": {"quota": 3},
        "research": {"quota": 1, "min": 1},
        "news": {"quota": 1},
    },
    "spillover_order": ["gap", "research", "news"],
    "registry_boost": 0.25,
    "paid_scrape": {"enabled": False, "max_calls_per_crawl": 0, "engines": []},
}

# Minimal registries
_PAPER_PUBLISHER_REG = {
    "sources": [
        {
            "id": "arxiv_cs_dc",
            "name": "arXiv cs.DC",
            "url": "https://arxiv.org/list/cs.DC/recent",
            "rss_feed": "https://arxiv.org/list/cs.DC/rss",
            "api_endpoint": "https://api.arxiv.org/query",
            "category": "preprint_repository",
            "focus": "Distributed inference",
            "relevance": 5,
            "priority": "critical",
        },
    ]
}

_BLOG_NEWSFEED_REG = {
    "sources": [
        {
            "id": "nvidia_developer_blog",
            "name": "NVIDIA Developer Blog",
            "url": "https://developer.nvidia.com/blog/",
            "rss_feed": "https://developer.nvidia.com/blog/feed",
            "category": "vendor_blogs",
            "focus": "CUDA, TensorRT, GPU architectures",
            "relevance": 5,
            "priority": "critical",
        },
        {
            "id": "semianalysis_newsletter",
            "name": "SemiAnalysis Newsletter",
            "url": "https://newsletter.semianalysis.com/",
            "rss_feed": "https://semianalysis.substack.com/feed",
            "category": "hardware_analysis",
            "focus": "AI accelerator landscape",
            "relevance": 5,
            "priority": "critical",
        },
    ]
}

_GITHUB_REPOS_REG = {
    "repositories": [
        {
            "id": "vllm",
            "name": "vLLM",
            "url": "https://github.com/vllm-project/vllm",
            "category": "inference_frameworks",
            "focus": "Inference server",
            "relevance": 5,
            "priority": "critical",
        },
    ]
}


def _make_registry() -> dict:
    return {
        "paper-publisher": _PAPER_PUBLISHER_REG,
        "blog-newsfeed": _BLOG_NEWSFEED_REG,
        "github-repos": _GITHUB_REPOS_REG,
    }


# ---------------------------------------------------------------------------
# Canned candidate builders
# ---------------------------------------------------------------------------

def _make_candidate(
    title: str,
    source_id: str = "arxiv:2401.00001",
    url: str = "https://arxiv.org/abs/2401.00001",
    engine: str = "arxiv",
    lane: str = "gap",
    origin_ids: list | None = None,
    published: str = "2026-06-01",
    score: float = 0.5,
) -> dict:
    return {
        "title": title,
        "url": url,
        "source_id": source_id,
        "id_type": "arxiv" if "arxiv" in source_id else "url",
        "published": published,
        "snippet": f"Snippet for {title}",
        "engine": engine,
        "lane": lane,
        "origin_ids": origin_ids or ["GAP-01"],
        "score": score,
    }


CANNED_ARXIV = [
    _make_candidate(
        "Disaggregated LLM Inference Survey",
        source_id="arxiv:2401.10001",
        url="https://arxiv.org/abs/2401.10001",
        lane="gap",
        origin_ids=["GAP-01"],
        score=0.85,
    ),
    _make_candidate(
        "Prefill-Decode Disaggregation Systems",
        source_id="arxiv:2401.10002",
        url="https://arxiv.org/abs/2401.10002",
        lane="gap",
        origin_ids=["GAP-01"],
        score=0.80,
    ),
]

CANNED_RSS = [
    _make_candidate(
        "NVIDIA Blackwell Inference Benchmark",
        source_id="nvidia_developer_blog",
        url="https://developer.nvidia.com/blog/blackwell-inference",
        engine="rss",
        lane="news",
        origin_ids=["news"],
        score=0.60,
    ),
]

CANNED_GITHUB = [
    _make_candidate(
        "vllm: v0.9.0 release",
        source_id="vllm-project/vllm",
        url="https://github.com/vllm-project/vllm/releases/tag/v0.9.0",
        engine="github",
        lane="gap",
        origin_ids=["GAP-02"],
        score=0.55,
    ),
]

CANNED_FORUM = [
    _make_candidate(
        "Ask HN: Best approach for disaggregated serving?",
        source_id="https://news.ycombinator.com/item?id=12345",
        url="https://news.ycombinator.com/item?id=12345",
        engine="hackernews",
        lane="gap",
        origin_ids=["GAP-01"],
        score=0.40,
    ),
]

# All canned candidates together (deduplicated view used in most tests)
ALL_CANNED = CANNED_ARXIV + CANNED_RSS + CANNED_GITHUB + CANNED_FORUM


# ---------------------------------------------------------------------------
# Vault + config setup helpers
# ---------------------------------------------------------------------------

def _make_tmp_vault(tmp_path: Path) -> Path:
    """Create a minimal tmp vault with gaps.md, DIR-0001.md, PURPOSE.md."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # wiki/gaps.md
    gaps_dir = vault / "wiki"
    gaps_dir.mkdir(parents=True)
    (gaps_dir / "gaps.md").write_text(_GAPS_MD, encoding="utf-8")

    # objective/direction/DIR-0001.md
    dir_dir = vault / "objective" / "direction"
    dir_dir.mkdir(parents=True)
    (dir_dir / "DIR-0001.md").write_text(_DIR_0001_MD, encoding="utf-8")

    # objective/purpose/PURPOSE.md
    purp_dir = vault / "objective" / "purpose"
    purp_dir.mkdir(parents=True)
    (purp_dir / "PURPOSE.md").write_text(_PURPOSE_TEXT, encoding="utf-8")

    # meta/ dir for ingest index
    (vault / "meta").mkdir(parents=True)

    return vault


def _patch_loaders(monkeypatch, tmp_path: Path, vault: Path):
    """Monkeypatch web_crawl's config/registry/ingest loaders to return in-memory fixtures.

    Patches at the web_crawl module level to avoid any vault_config lru_cache
    cross-test contamination.  The ingest index is wired directly to the tmp vault
    via a thin shim that bypasses vault_config entirely.
    """
    # Patch _load_config
    monkeypatch.setattr(web_crawl, "_load_config", lambda: dict(_WEB_CONFIG))

    # Patch _load_registry
    monkeypatch.setattr(web_crawl, "_load_registry", lambda: _make_registry())

    # Patch _seen_path to a temp location per test
    seen_path = str(tmp_path / "seen.json")
    monkeypatch.setattr(web_crawl, "_seen_path", lambda: seen_path)

    # Patch _load_ingest_rows to read directly from vault/meta/ingest_index.json
    # (bypasses vault_config completely -- avoids lru_cache cross-test contamination).
    def _tmp_load_ingest_rows(vault_root):
        p = Path(vault_root) / "meta" / "ingest_index.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
                return list(data.get("sources", {}).values())
            except (OSError, json.JSONDecodeError):
                pass
        return []

    monkeypatch.setattr(web_crawl, "_load_ingest_rows", _tmp_load_ingest_rows)

    # Patch _import_ingest_index to return a shim that writes directly to vault.
    # This replaces the module-level enqueue() with a version that uses vault's
    # meta/ingest_index.json directly, bypassing vault_config path resolution.
    import agents.ingest_index as _real_ii

    class _IngestShim:
        """Thin shim: enqueue / _load / _save wired to the tmp vault path."""

        def enqueue(self, ident, *, title="", rationale="", score=None,
                    discovered_by="", objective_ids=None, name=None,
                    published=None):
            # Load existing data from vault directly
            index_path = vault / "meta" / "ingest_index.json"
            if index_path.exists():
                try:
                    data = json.loads(index_path.read_text())
                except (OSError, json.JSONDecodeError):
                    data = {"version": 3, "vault": vault.name, "sources": {}}
            else:
                data = {"version": 3, "vault": vault.name, "sources": {}}
            data.setdefault("sources", {})

            # Normalize id (simplified: use _real_ii helpers)
            sid, id_type, url = _real_ii._normalize_enqueue_id(ident)
            row = data["sources"].get(sid)
            if row is not None and row.get("status") == "rejected":
                return row
            if row is None:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                row = {
                    "id": sid, "id_type": id_type, "status": "waiting_approval",
                    "title": title, "filename": "", "url": url,
                    "source_type": id_type, "content_hash": "",
                    "first_seen": now, "ingested_at": None, "source_page": "",
                    "relevance_score": None, "rationale": "", "discovered_by": "",
                    "objective_ids": [], "proposed_at": now, "rejected_at": None,
                    "rejection_reason": "",
                }
                data["sources"][sid] = row
            if title:
                row["title"] = title
            if rationale:
                row["rationale"] = rationale
            if score is not None:
                row["relevance_score"] = score
            if discovered_by:
                row["discovered_by"] = discovered_by
            if objective_ids:
                merged = list(row.get("objective_ids") or [])
                for oid in objective_ids:
                    if oid not in merged:
                        merged.append(oid)
                row["objective_ids"] = merged
            if url and not row.get("url"):
                row["url"] = url
            if published is not None:
                row["published"] = published
            # Save
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
            return row

    shim_instance = _IngestShim()
    monkeypatch.setattr(web_crawl, "_import_ingest_index", lambda: shim_instance)


def _patch_harvest(monkeypatch):
    """Monkeypatch all four harvest functions to return canned data (offline)."""
    import web_harvest as wh

    monkeypatch.setattr(wh, "poll_rss", lambda *a, **kw: list(CANNED_RSS))
    monkeypatch.setattr(wh, "query_papers", lambda *a, **kw: list(CANNED_ARXIV))
    monkeypatch.setattr(wh, "query_forum", lambda *a, **kw: list(CANNED_FORUM))
    monkeypatch.setattr(wh, "poll_github_releases", lambda *a, **kw: list(CANNED_GITHUB))


def _patch_rank(monkeypatch):
    """Monkeypatch score_candidates to return input with canned scores (offline)."""
    import web_rank as wr

    def _fake_score(candidates, query, *, registry=None, allow_remote_ollama=False, today=None):
        # Assign scores from the candidate's existing score field or use index-based
        result = []
        for i, c in enumerate(candidates):
            cp = dict(c)
            # Preserve pre-set score or assign descending scores
            if "score" not in cp:
                cp["score"] = 1.0 - i * 0.1
            result.append(cp)
        # Sort descending by score
        result.sort(key=lambda x: -x.get("score", 0.0))
        return result

    monkeypatch.setattr(wr, "score_candidates", _fake_score)


# ===========================================================================
# Tests: PURPOSE loading
# ===========================================================================

class TestPurposeLoading:
    """Tests for _load_purpose helper."""

    def test_loads_from_purpose_md(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "objective" / "purpose").mkdir(parents=True)
        (vault / "objective" / "purpose" / "PURPOSE.md").write_text(
            _PURPOSE_TEXT, encoding="utf-8"
        )
        result = web_crawl._load_purpose(str(vault))
        assert _PURPOSE_TEXT in result

    def test_falls_back_to_vault_yaml(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "vault.yaml").write_text(
            "purpose: fallback purpose text\n", encoding="utf-8"
        )
        result = web_crawl._load_purpose(str(vault))
        assert "fallback purpose text" in result

    def test_returns_empty_when_no_files(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        result = web_crawl._load_purpose(str(vault))
        assert result == ""

    def test_purpose_md_takes_priority_over_vault_yaml(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "objective" / "purpose").mkdir(parents=True)
        (vault / "objective" / "purpose" / "PURPOSE.md").write_text(
            "Primary purpose", encoding="utf-8"
        )
        (vault / "vault.yaml").write_text(
            "purpose: secondary purpose\n", encoding="utf-8"
        )
        result = web_crawl._load_purpose(str(vault))
        assert "Primary purpose" in result
        assert "secondary purpose" not in result


# ===========================================================================
# Tests: routing helpers
# ===========================================================================

class TestRoutingHelpers:
    """Tests for _is_github_entry and _registry_entry_for_id."""

    def setup_method(self):
        self.registry = _make_registry()

    def test_github_entry_found(self):
        is_gh, url = web_crawl._is_github_entry("vllm", self.registry)
        assert is_gh is True
        assert "vllm-project/vllm" in url

    def test_github_entry_not_found(self):
        is_gh, url = web_crawl._is_github_entry("arxiv_cs_dc", self.registry)
        assert is_gh is False
        assert url == ""

    def test_registry_entry_lookup_paper_publisher(self):
        entry = web_crawl._registry_entry_for_id("arxiv_cs_dc", self.registry)
        assert entry is not None
        assert entry["category"] == "preprint_repository"

    def test_registry_entry_lookup_blog(self):
        entry = web_crawl._registry_entry_for_id("nvidia_developer_blog", self.registry)
        assert entry is not None
        assert entry["name"] == "NVIDIA Developer Blog"

    def test_registry_entry_lookup_missing(self):
        entry = web_crawl._registry_entry_for_id("nonexistent_id", self.registry)
        assert entry is None


# ===========================================================================
# Tests: _arxiv_category helper
# ===========================================================================

class TestArxivCategory:
    """_arxiv_category derives the arXiv subject category from a registry entry."""

    def test_arxiv_cs_ar_by_id(self):
        entry = {"id": "arxiv_cs_ar", "category": "preprint_repository",
                 "url": "", "rss_feed": ""}
        assert web_crawl._arxiv_category(entry) == "cs.AR"

    def test_arxiv_cs_dc_by_id(self):
        entry = {"id": "arxiv_cs_dc", "category": "preprint_repository",
                 "url": "", "rss_feed": ""}
        assert web_crawl._arxiv_category(entry) == "cs.DC"

    def test_arxiv_cs_lg_by_id(self):
        entry = {"id": "arxiv_cs_lg", "category": "preprint_repository",
                 "url": "", "rss_feed": ""}
        assert web_crawl._arxiv_category(entry) == "cs.LG"

    def test_arxiv_eess_sp_by_id(self):
        entry = {"id": "arxiv_eess_sp", "category": "preprint_repository",
                 "url": "", "rss_feed": ""}
        assert web_crawl._arxiv_category(entry) == "eess.SP"

    def test_derivation_from_rss_feed_url(self):
        """When id is unknown, parse the rss_feed URL for the category path segment."""
        entry = {
            "id": "arxiv_cs_ar_custom",
            "category": "preprint_repository",
            "url": "https://arxiv.org/list/cs.AR/recent",
            "rss_feed": "https://arxiv.org/list/cs.AR/rss",
        }
        assert web_crawl._arxiv_category(entry) == "cs.AR"

    def test_derivation_from_url_field(self):
        """Falls back to url field when rss_feed is absent."""
        entry = {
            "id": "arxiv_cs_dc_custom",
            "category": "preprint_repository",
            "url": "https://arxiv.org/list/cs.DC/recent",
            "rss_feed": "",
        }
        assert web_crawl._arxiv_category(entry) == "cs.DC"

    def test_eess_sp_from_rss_url(self):
        entry = {
            "id": "arxiv_eess_sp_custom",
            "category": "preprint_repository",
            "url": "https://arxiv.org/list/eess.SP/recent",
            "rss_feed": "https://arxiv.org/list/eess.SP/rss",
        }
        assert web_crawl._arxiv_category(entry) == "eess.SP"

    def test_non_arxiv_entry_returns_none(self):
        """A non-arxiv registry entry (e.g. blog) returns None."""
        entry = {
            "id": "nvidia_developer_blog",
            "category": "vendor_blogs",
            "url": "https://developer.nvidia.com/blog/",
            "rss_feed": "https://developer.nvidia.com/blog/feed",
        }
        assert web_crawl._arxiv_category(entry) is None

    def test_unknown_id_no_url_returns_none(self):
        entry = {"id": "some_other_source", "category": "paper_api",
                 "url": "", "rss_feed": ""}
        assert web_crawl._arxiv_category(entry) is None

    def test_openalex_entry_returns_none(self):
        entry = {"id": "openalex", "category": "paper_api",
                 "url": "https://api.openalex.org/", "rss_feed": ""}
        assert web_crawl._arxiv_category(entry) is None


# ===========================================================================
# Tests: per-category query_papers routing in _harvest_target
# ===========================================================================

class TestArxivCategoryRouting:
    """_harvest_target passes the correct category kwarg to query_papers."""

    def _make_two_category_registry(self) -> dict:
        """Registry with arxiv_cs_ar and arxiv_cs_dc entries."""
        return {
            "paper-publisher": {
                "sources": [
                    {
                        "id": "arxiv_cs_ar",
                        "name": "arXiv cs.AR",
                        "url": "https://arxiv.org/list/cs.AR/recent",
                        "rss_feed": "https://arxiv.org/list/cs.AR/rss",
                        "category": "preprint_repository",
                        "focus": "Hardware architectures",
                        "relevance": 5,
                        "priority": "critical",
                    },
                    {
                        "id": "arxiv_cs_dc",
                        "name": "arXiv cs.DC",
                        "url": "https://arxiv.org/list/cs.DC/recent",
                        "rss_feed": "https://arxiv.org/list/cs.DC/rss",
                        "category": "preprint_repository",
                        "focus": "Distributed computing",
                        "relevance": 5,
                        "priority": "critical",
                    },
                ]
            },
            "blog-newsfeed": {"sources": []},
            "github-repos": {"repositories": []},
        }

    def test_two_arxiv_category_sources_one_query_two_calls(self):
        """cs.AR + cs.DC routed for the same query -> 2 distinct category calls."""
        registry = self._make_two_category_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["disaggregated inference scheduling"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_ar", "arxiv_cs_dc"],
        }

        call_log: list[tuple] = []

        class _SpyHarvest:
            def query_papers(self, q, *, engine, limit, category=None):
                call_log.append((engine, q, category))
                return list(CANNED_ARXIV)

            def poll_rss(self, *a, **kw):
                return []

            def query_forum(self, *a, **kw):
                return []

            def poll_github_releases(self, *a, **kw):
                return []

        web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5,
            _harvest_mod=_SpyHarvest()
        )

        # 2 categories x 1 query = 2 distinct calls
        assert len(call_log) == 2, (
            f"Expected 2 category-specific calls, got {len(call_log)}: {call_log}"
        )
        categories_seen = {c[2] for c in call_log}
        assert "cs.AR" in categories_seen
        assert "cs.DC" in categories_seen

    def test_category_passed_as_kwarg_to_query_papers(self):
        """category kwarg is explicitly passed (not None) for arxiv-category entries."""
        registry = self._make_two_category_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["kv cache scheduling"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_ar"],
        }

        received_category: list = []

        class _SpyHarvest:
            def query_papers(self, q, *, engine, limit, category=None):
                received_category.append(category)
                return []

            def poll_rss(self, *a, **kw):
                return []

            def query_forum(self, *a, **kw):
                return []

            def poll_github_releases(self, *a, **kw):
                return []

        web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5,
            _harvest_mod=_SpyHarvest()
        )

        assert len(received_category) == 1
        assert received_category[0] == "cs.AR", (
            f"Expected category='cs.AR', got {received_category[0]!r}"
        )

    def test_trace_query_string_includes_cat_annotation(self):
        """Trace query strings include [cat:cs.AR] annotation for arxiv-category calls."""
        from web_decision import DecisionTrace

        registry = self._make_two_category_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["memory disaggregation"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_ar", "arxiv_cs_dc"],
        }

        class _NopHarvest:
            def query_papers(self, q, *, engine, limit, category=None):
                return []

            def poll_rss(self, *a, **kw):
                return []

            def query_forum(self, *a, **kw):
                return []

            def poll_github_releases(self, *a, **kw):
                return []

        trace = DecisionTrace()
        web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT,
            per_source_limit=5, _harvest_mod=_NopHarvest(), trace=trace
        )

        harvest_recs = trace.find("harvest", "target")
        assert len(harvest_recs) == 1
        queries_in_trace = harvest_recs[0]["data"]["queries"]

        # Both trace entries should carry [cat:...] annotations
        annotated = [q["query"] for q in queries_in_trace if "[cat:" in q["query"]]
        assert len(annotated) == 2, (
            f"Expected 2 [cat:...]-annotated trace entries, "
            f"got {len(annotated)}: {queries_in_trace}"
        )
        assert any("cs.AR" in q for q in annotated)
        assert any("cs.DC" in q for q in annotated)


# ===========================================================================
# Tests: dry_run mode
# ===========================================================================

class TestDryRun:
    """dry_run=True: returns plan+selected, writes nothing."""

    def test_dry_run_returns_dict_with_required_keys(
        self, tmp_path, monkeypatch
    ):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")

        assert isinstance(result, dict)
        assert "plan" in result
        assert "selected" in result
        assert "would_write" in result

    def test_dry_run_would_write_has_date(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        assert result["would_write"] == "2026-06-22.md"

    def test_dry_run_does_not_write_nightly_report(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")

        report_path = vault / "meta" / "nightly_report" / "2026-06-22.md"
        assert not report_path.exists(), "dry_run must NOT write nightly report"

    def test_dry_run_does_not_enqueue(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")

        # Ingest index should not have any waiting_approval rows
        index_path = vault / "meta" / "ingest_index.json"
        if index_path.exists():
            data = json.loads(index_path.read_text())
            waiting = [
                r for r in data.get("sources", {}).values()
                if r.get("status") == "waiting_approval"
            ]
            assert len(waiting) == 0, "dry_run must NOT enqueue anything"

    def test_dry_run_selected_within_total_cap(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        total_cap = _WEB_CONFIG["new_sources_total"]
        assert len(result["selected"]) <= total_cap

    def test_dry_run_selected_have_valid_lanes(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        valid_lanes = {"gap", "research", "news"}
        for c in result["selected"]:
            assert c.get("lane") in valid_lanes, (
                f"Unexpected lane {c.get('lane')!r} in candidate {c}"
            )

    def test_dry_run_seen_cache_not_written(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        seen_path = tmp_path / "seen.json"
        assert not seen_path.exists()

        web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")

        assert not seen_path.exists(), "dry_run must NOT write seen cache"

    def test_dry_run_plan_has_targets(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        plan = result["plan"]
        assert "targets" in plan
        # Should have at least a news target
        assert len(plan["targets"]) >= 1


# ===========================================================================
# Tests: live (non-dry-run) mode
# ===========================================================================

class TestLiveRun:
    """dry_run=False: enqueues candidates AND writes nightly report."""

    def test_live_run_returns_required_keys(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        assert "selected" in result
        assert "report_path" in result
        assert "enqueued" in result

    def test_live_run_writes_nightly_report(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")

        report_path = Path(result["report_path"])
        assert report_path.exists(), "live run must write nightly report"
        assert report_path.suffix == ".md"
        assert "2026-06-22" in report_path.name

    def test_live_run_report_contains_candidate_titles(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        selected = result["selected"]

        if not selected:
            pytest.skip("No candidates selected; skipping title check")

        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        for c in selected:
            title = c.get("title", "")
            if title:
                assert title in report_text, (
                    f"Title {title!r} not found in nightly report"
                )

    def test_live_run_report_has_approval_instructions(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "approve" in report_text.lower()
        assert "ingest_index" in report_text

    def test_live_run_enqueues_selected_ids(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        selected = result["selected"]
        enqueued = result["enqueued"]

        if not selected:
            pytest.skip("No candidates selected; skipping enqueue check")

        # Number of enqueued should match number of selected (ignoring empty idents)
        assert len(enqueued) == len(selected), (
            f"Expected {len(selected)} enqueued ids, got {len(enqueued)}: {enqueued}"
        )

    def test_live_run_waiting_approval_rows_appear(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        selected = result["selected"]

        if not selected:
            pytest.skip("No candidates selected; skipping waiting_approval check")

        index_path = vault / "meta" / "ingest_index.json"
        assert index_path.exists(), "ingest_index.json should be created"
        data = json.loads(index_path.read_text())
        waiting = [
            r for r in data.get("sources", {}).values()
            if r.get("status") == "waiting_approval"
        ]
        assert len(waiting) == len(selected), (
            f"Expected {len(selected)} waiting_approval rows, found {len(waiting)}"
        )

    def test_live_run_enqueued_have_discovered_by_web(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        if not result["selected"]:
            pytest.skip("No candidates selected")

        index_path = vault / "meta" / "ingest_index.json"
        data = json.loads(index_path.read_text())
        for row in data.get("sources", {}).values():
            if row.get("status") == "waiting_approval":
                assert row.get("discovered_by") == "web", (
                    f"Expected discovered_by='web', got {row.get('discovered_by')!r}"
                )

    def test_live_run_seen_cache_written(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        seen_path = tmp_path / "seen.json"
        assert not seen_path.exists()

        web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")

        # Seen cache should be written when live crawl finds new candidates
        # (may be absent if harvest returns nothing -- check conditionally)
        # The test verifies that if candidates were harvested, cache was written.
        import web_harvest as wh
        # We patched harvest to return non-empty lists, so candidates must exist
        assert seen_path.exists(), (
            "live run must write seen cache when candidates are harvested"
        )
        data = json.loads(seen_path.read_text())
        assert isinstance(data, dict)
        assert len(data) > 0


# ===========================================================================
# Tests: graceful degradation
# ===========================================================================

class TestGracefulDegradation:
    """One harvest function raising must not crash the whole crawl."""

    def test_query_papers_raises_crawl_completes(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_rank(monkeypatch)

        import web_harvest as wh

        def _raise(*a, **kw):
            raise RuntimeError("simulated network failure")

        monkeypatch.setattr(wh, "poll_rss", lambda *a, **kw: list(CANNED_RSS))
        monkeypatch.setattr(wh, "query_papers", _raise)  # this one raises
        monkeypatch.setattr(wh, "query_forum", lambda *a, **kw: list(CANNED_FORUM))
        monkeypatch.setattr(wh, "poll_github_releases", lambda *a, **kw: list(CANNED_GITHUB))

        # Should NOT raise
        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        assert isinstance(result, dict)
        assert "selected" in result

    def test_poll_rss_raises_crawl_completes(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_rank(monkeypatch)

        import web_harvest as wh

        def _raise(*a, **kw):
            raise OSError("simulated RSS timeout")

        monkeypatch.setattr(wh, "poll_rss", _raise)  # this one raises
        monkeypatch.setattr(wh, "query_papers", lambda *a, **kw: list(CANNED_ARXIV))
        monkeypatch.setattr(wh, "query_forum", lambda *a, **kw: [])
        monkeypatch.setattr(wh, "poll_github_releases", lambda *a, **kw: [])

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        assert isinstance(result, dict)
        assert "selected" in result

    def test_all_harvest_raises_returns_empty_selected(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_rank(monkeypatch)

        import web_harvest as wh

        def _raise(*a, **kw):
            raise RuntimeError("all down")

        monkeypatch.setattr(wh, "poll_rss", _raise)
        monkeypatch.setattr(wh, "query_papers", _raise)
        monkeypatch.setattr(wh, "query_forum", _raise)
        monkeypatch.setattr(wh, "poll_github_releases", _raise)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        assert isinstance(result, dict)
        assert result["selected"] == []

    def test_poll_github_releases_raises_crawl_completes(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_rank(monkeypatch)

        import web_harvest as wh

        monkeypatch.setattr(wh, "poll_rss", lambda *a, **kw: list(CANNED_RSS))
        monkeypatch.setattr(wh, "query_papers", lambda *a, **kw: list(CANNED_ARXIV))
        monkeypatch.setattr(wh, "query_forum", lambda *a, **kw: list(CANNED_FORUM))
        monkeypatch.setattr(wh, "poll_github_releases",
                            lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("gh down")))

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        assert isinstance(result, dict)


# ===========================================================================
# Tests: seen-cache dedup
# ===========================================================================

class TestSeenCacheDedup:
    """Already-seen candidates are filtered before selection."""

    def test_seen_candidates_deduped(self, tmp_path, monkeypatch):
        """Pre-populate seen.json with all canned candidate ids; expect 0 selected."""
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_rank(monkeypatch)

        import web_harvest as wh
        monkeypatch.setattr(wh, "poll_rss", lambda *a, **kw: list(CANNED_RSS))
        monkeypatch.setattr(wh, "query_papers", lambda *a, **kw: list(CANNED_ARXIV))
        monkeypatch.setattr(wh, "query_forum", lambda *a, **kw: list(CANNED_FORUM))
        monkeypatch.setattr(wh, "poll_github_releases", lambda *a, **kw: list(CANNED_GITHUB))

        # Pre-populate seen cache with all stable ids from ALL_CANNED
        seen_data = {}
        import web_harvest as wh2
        for c in ALL_CANNED:
            from web_harvest import _stable_id
            sid = _stable_id(c)
            if sid:
                seen_data[sid] = True

        seen_path = tmp_path / "seen.json"
        seen_path.write_text(json.dumps(seen_data), encoding="utf-8")

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        # All candidates were seen -> nothing passes through dedup -> 0 selected
        assert result["selected"] == []

    def test_new_candidates_not_in_seen_pass_through(self, tmp_path, monkeypatch):
        """Empty seen cache -> all canned candidates are new -> up to cap selected."""
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        # Ensure seen.json does not exist
        seen_path = tmp_path / "seen.json"
        assert not seen_path.exists()

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        total_cap = _WEB_CONFIG["new_sources_total"]
        # With fresh seen cache, we should select up to the cap
        assert len(result["selected"]) <= total_cap
        assert len(result["selected"]) >= 1, "At least one candidate should be selected"


# ===========================================================================
# Tests: routing logic
# ===========================================================================

class TestHarvestTargetRouting:
    """_harvest_target routing tests using monkeypatched harvest module."""

    def _make_harvest_mock(self):
        """Return a mock harvest module with call-tracking lists."""
        mock = MagicMock()
        mock.poll_rss.return_value = list(CANNED_RSS)
        mock.query_papers.return_value = list(CANNED_ARXIV)
        mock.query_forum.return_value = list(CANNED_FORUM)
        mock.poll_github_releases.return_value = list(CANNED_GITHUB)
        return mock

    def test_arxiv_source_routes_to_query_papers(self):
        registry = _make_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["disaggregated LLM inference"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_dc"],
        }
        mock_wh = self._make_harvest_mock()
        cands = web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=mock_wh
        )
        mock_wh.query_papers.assert_called()
        assert len(cands) > 0

    def test_blog_source_with_rss_routes_to_poll_rss(self):
        registry = _make_registry()
        target = {
            "target_id": "news",
            "lane": "news",
            "origin_ids": ["news"],
            "queries": [],
            "fillable_by": ["web"],
            "routed_sources": ["nvidia_developer_blog"],
        }
        mock_wh = self._make_harvest_mock()
        cands = web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=mock_wh
        )
        mock_wh.poll_rss.assert_called()
        assert len(cands) > 0

    def test_github_source_routes_to_poll_github_releases(self):
        registry = _make_registry()
        target = {
            "target_id": "GAP-02",
            "lane": "gap",
            "origin_ids": ["GAP-02"],
            "queries": ["vllm disaggregation"],
            "fillable_by": ["github"],
            "routed_sources": ["vllm"],
        }
        mock_wh = self._make_harvest_mock()
        cands = web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=mock_wh
        )
        mock_wh.poll_github_releases.assert_called()
        assert len(cands) > 0

    def test_forum_fillable_routes_to_query_forum(self):
        registry = _make_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["disaggregated serving"],
            "fillable_by": ["arxiv", "forum"],
            "routed_sources": [],
        }
        mock_wh = self._make_harvest_mock()
        cands = web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=mock_wh
        )
        mock_wh.query_forum.assert_called_with(
            "disaggregated serving", engine="hackernews", limit=5
        )

    def test_research_lane_empty_sources_falls_back_to_arxiv(self):
        registry = _make_registry()
        target = {
            "target_id": "DIR-0001",
            "lane": "research",
            "origin_ids": ["DIR-0001"],
            "queries": ["disaggregated LLM inference prefill decode"],
            "fillable_by": ["arxiv"],
            "routed_sources": [],  # empty
        }
        mock_wh = self._make_harvest_mock()
        cands = web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=mock_wh
        )
        mock_wh.query_papers.assert_called_with(
            "disaggregated LLM inference prefill decode",
            engine="arxiv",
            limit=5,
        )
        assert len(cands) > 0

    def test_candidates_tagged_with_lane_and_origin_ids(self):
        registry = _make_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01", "DIR-0001"],
            "queries": ["test query"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_dc"],
        }
        mock_wh = self._make_harvest_mock()
        cands = web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=mock_wh
        )
        for c in cands:
            assert c.get("lane") == "gap"
            assert "GAP-01" in c.get("origin_ids", [])
            assert "DIR-0001" in c.get("origin_ids", [])

    def test_harvest_error_does_not_propagate(self):
        registry = _make_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["test query"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_dc"],
        }
        mock_wh = MagicMock()
        mock_wh.poll_rss.side_effect = RuntimeError("boom")
        mock_wh.query_papers.side_effect = RuntimeError("boom")
        mock_wh.query_forum.side_effect = RuntimeError("boom")
        mock_wh.poll_github_releases.side_effect = RuntimeError("boom")

        # Should not raise
        cands = web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=mock_wh
        )
        assert cands == []


# ===========================================================================
# Tests: report format
# ===========================================================================

class TestNightlyReport:
    """_write_nightly_report produces a well-formed markdown file."""

    def test_report_created_in_correct_path(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        report_path = web_crawl._write_nightly_report(
            str(vault), "2026-06-22", [CANNED_ARXIV[0]]
        )
        assert report_path.exists()
        assert report_path.parent == vault / "meta" / "nightly_report"
        assert report_path.name == "2026-06-22.md"

    def test_report_contains_title(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        c = _make_candidate("Test Paper Title", score=0.75)
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        assert "Test Paper Title" in text

    def test_report_contains_score_and_url(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        c = _make_candidate("Test Paper", score=0.876)
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        assert "0.8760" in text
        assert c["url"] in text

    def test_report_has_approval_footer(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [])
        text = report_path.read_text(encoding="utf-8")
        assert "approve" in text.lower()
        assert "ingest_index" in text

    def test_report_empty_selection(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [])
        text = report_path.read_text(encoding="utf-8")
        assert "No candidates" in text


# ===========================================================================
# Tests: CLI
# ===========================================================================

class TestCLI:
    """Smoke-tests for the CLI main() function (does not run real crawl)."""

    def test_cli_requires_vault(self, monkeypatch):
        """Without --vault and without env/config, main() exits non-zero."""
        import os
        monkeypatch.delenv("VAULT_ROOT", raising=False)
        monkeypatch.delenv("VAULT_PATH", raising=False)
        monkeypatch.delenv("VAULT", raising=False)

        # Patch vault_config to raise SystemExit
        import agents.vault_config as vc
        monkeypatch.setattr(vc, "vault_path", lambda name=None: (_ for _ in ()).throw(
            SystemExit("no vault")
        ))

        with pytest.raises(SystemExit):
            web_crawl.main(["--dry-run"])

    def test_cli_json_flag_dry_run(self, tmp_path, monkeypatch, capsys):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        # Patch vault resolution to return our tmp vault
        monkeypatch.setattr(
            web_crawl, "_resolve_vault_root", lambda arg: str(vault)
        )

        ret = web_crawl.main(["--vault", str(vault), "--dry-run", "--json"])
        assert ret == 0

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "selected" in data
        assert "plan" in data


# ===========================================================================
# Tests: per-item identity fix (same-feed collision regression)
# ===========================================================================

class TestPerItemIdentity:
    """Regression: two posts from the same RSS feed must enqueue as two distinct rows.

    Before the fix, source_id (the feed-level registry id, e.g. "huggingface_blog")
    was used as the enqueue key, so two posts from the same feed collapsed to one row.
    After the fix, candidate_ident (the per-item URL) is used.
    """

    def _make_five_candidates(self) -> list[dict]:
        """Return 5 distinct candidates: 2 from huggingface_blog (same source_id,
        different URLs), 2 from nvidia_developer_blog (same source_id, different URLs),
        and 1 arXiv paper -- replicating the live collision scenario."""
        def _rss(title, source_id, url, score, lane="news"):
            return {
                "title": title,
                "url": url,
                "source_id": source_id,
                "id_type": "url",
                "published": "2026-06-01",
                "snippet": "snippet",
                "engine": "rss",
                "lane": lane,
                "origin_ids": ["news"],
                "score": score,
            }

        def _arxiv(title, arxiv_id, score):
            return {
                "title": title,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "source_id": f"arxiv:{arxiv_id}",
                "id_type": "arxiv",
                "published": "2026-06-01",
                "snippet": "abstract",
                "engine": "arxiv",
                "lane": "gap",
                "origin_ids": ["GAP-01"],
                "score": score,
            }

        return [
            _rss("HuggingFace Post A", "huggingface_blog",
                 "https://huggingface.co/blog/post-a", 0.9),
            _rss("HuggingFace Post B", "huggingface_blog",
                 "https://huggingface.co/blog/post-b", 0.85),
            _rss("NVIDIA Post A", "nvidia_developer_blog",
                 "https://developer.nvidia.com/blog/post-a", 0.8),
            _rss("NVIDIA Post B", "nvidia_developer_blog",
                 "https://developer.nvidia.com/blog/post-b", 0.75),
            _arxiv("Disaggregated Inference Survey", "2606.08635v1", 0.95),
        ]

    def test_five_distinct_items_enqueue_five_distinct_rows(self, tmp_path, monkeypatch):
        """Live-run with 5 candidates (incl. 2 same-feed pairs) -> 5 distinct ingest rows.

        This is the exact scenario from the observed bug: two huggingface_blog posts and
        two nvidia_developer_blog posts previously collapsed to 2 rows (one per feed).
        The fix ensures all 5 post distinct candidate_ident values -> 5 rows.
        """
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_rank(monkeypatch)

        five_cands = self._make_five_candidates()

        import web_harvest as wh
        # Return our 5 distinct canned candidates from every harvest call so they
        # all land in the pool regardless of routing.
        monkeypatch.setattr(wh, "poll_rss", lambda *a, **kw: list(five_cands))
        monkeypatch.setattr(wh, "query_papers", lambda *a, **kw: list(five_cands))
        monkeypatch.setattr(wh, "query_forum", lambda *a, **kw: [])
        monkeypatch.setattr(wh, "poll_github_releases", lambda *a, **kw: [])

        # Use a large enough quota so all 5 pass selection
        import web_crawl as wc
        big_config = {
            "new_sources_total": 5,
            "lanes": {
                "gap": {"quota": 3},
                "research": {"quota": 1, "min": 0},
                "news": {"quota": 3},
            },
            "spillover_order": ["gap", "research", "news"],
            "registry_boost": 0.25,
            "paid_scrape": {"enabled": False, "max_calls_per_crawl": 0, "engines": []},
        }
        monkeypatch.setattr(wc, "_load_config", lambda: big_config)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")

        index_path = vault / "meta" / "ingest_index.json"
        assert index_path.exists(), "ingest_index.json must be written"
        data = json.loads(index_path.read_text())
        waiting = [
            r for r in data.get("sources", {}).values()
            if r.get("status") == "waiting_approval"
        ]

        # Collect the enqueued ids
        enqueued_ids = [r["id"] for r in waiting]
        assert len(enqueued_ids) == len(set(enqueued_ids)), (
            "All enqueued ids must be distinct (no intra-feed collision)"
        )

        # The enqueued ids must be per-item identities (URLs or arxiv:), NOT feed ids
        for rid in enqueued_ids:
            assert rid not in ("huggingface_blog", "nvidia_developer_blog"), (
                f"Enqueue id {rid!r} is a feed-level source_id, not a per-item ident"
            )

        # We expect 5 distinct rows (the full set -- no collision)
        assert len(waiting) == 5, (
            f"Expected 5 distinct waiting_approval rows, got {len(waiting)}: {enqueued_ids}"
        )

    def test_candidate_ident_is_url_for_rss_not_source_id(self, tmp_path, monkeypatch):
        """candidate_ident for an RSS candidate must be the item URL, not source_id."""
        from web_harvest import candidate_ident
        rss_cand = {
            "title": "A post",
            "url": "https://huggingface.co/blog/some-post",
            "source_id": "huggingface_blog",
            "id_type": "url",
            "published": "2026-06-01",
            "snippet": "text",
            "engine": "rss",
        }
        ident = candidate_ident(rss_cand)
        assert ident == "https://huggingface.co/blog/some-post"
        assert ident != "huggingface_blog"

    def test_candidate_ident_is_source_id_for_arxiv(self):
        """candidate_ident for an arXiv candidate is its arxiv: source_id."""
        from web_harvest import candidate_ident
        arxiv_cand = {
            "title": "Paper",
            "url": "https://arxiv.org/abs/2401.09670",
            "source_id": "arxiv:2401.09670v2",
            "id_type": "arxiv",
            "published": "2024-01-18",
            "snippet": "abstract",
            "engine": "arxiv",
        }
        ident = candidate_ident(arxiv_cand)
        assert ident == "arxiv:2401.09670v2"


# ===========================================================================
# Tests: Decision trace wired through crawl
# ===========================================================================

class TestDecisionTrace:
    """crawl() builds a DecisionTrace; assert it has harvest/rank/select records."""

    def test_dry_run_returns_trace(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        assert "trace" in result
        trace = result["trace"]
        assert trace is not None

    def test_harvest_target_records_present(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        trace = result["trace"]
        harvest_recs = trace.find("harvest", "target")
        # Should have at least one harvest/target record (one per target in plan)
        assert len(harvest_recs) >= 1

    def test_harvest_records_have_queries_with_engine_and_n_returned(
        self, tmp_path, monkeypatch
    ):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        trace = result["trace"]
        harvest_recs = trace.find("harvest", "target")
        for rec in harvest_recs:
            d = rec["data"]
            assert "target_id" in d
            assert "lane" in d
            assert "queries" in d
            assert "n_candidates" in d
            assert "seen_dropped" in d
            # queries is a list; each entry should have engine, query, n_returned
            for q_entry in d["queries"]:
                assert "engine" in q_entry
                assert "query" in q_entry
                assert "n_returned" in q_entry

    def test_harvest_records_n_candidates_is_nonneg(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        trace = result["trace"]
        for rec in trace.find("harvest", "target"):
            assert rec["data"]["n_candidates"] >= 0
            assert rec["data"]["seen_dropped"] >= 0

    def test_rank_scores_record_present(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        trace = result["trace"]
        rank_rec = trace.first("rank", "scores")
        assert rank_rec is not None

    def test_rank_scores_record_has_path_and_candidates(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        trace = result["trace"]
        rank_rec = trace.first("rank", "scores")
        d = rank_rec["data"]
        assert "path" in d
        assert d["path"] in ("embedding", "fallback")
        assert "candidates" in d
        # Each candidate entry has ident and score
        for item in d["candidates"]:
            assert "ident" in item
            assert "score" in item

    def test_select_final_record_present(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        trace = result["trace"]
        final_rec = trace.first("select", "final")
        assert final_rec is not None

    def test_live_run_nightly_report_contains_decision_trace_section(
        self, tmp_path, monkeypatch
    ):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "## Decision trace" in report_text

    def test_live_run_report_contains_merge_scoring(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "Merge scoring" in report_text

    def test_live_run_report_contains_selection(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "Selection" in report_text

    def test_trace_out_flag_writes_file(self, tmp_path, monkeypatch, capsys):
        """--trace-out PATH writes the rendered trace to the given path."""
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        # Patch vault resolution to return our tmp vault
        monkeypatch.setattr(
            web_crawl, "_resolve_vault_root", lambda arg: str(vault)
        )

        trace_out_path = str(tmp_path / "trace_out.md")
        ret = web_crawl.main([
            "--vault", str(vault),
            "--dry-run",
            "--trace-out", trace_out_path,
        ])
        assert ret == 0

        # File should exist and contain key sections
        assert Path(trace_out_path).exists(), f"trace-out file not written to {trace_out_path}"
        content = Path(trace_out_path).read_text(encoding="utf-8")
        assert "Web Decision Trace" in content
        assert "Merge scoring" in content
        assert "Selection" in content

    def test_trace_out_contains_harvest_section(self, tmp_path, monkeypatch):
        """--trace-out file includes ## Harvest section."""
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        monkeypatch.setattr(
            web_crawl, "_resolve_vault_root", lambda arg: str(vault)
        )

        trace_out_path = str(tmp_path / "trace_out2.md")
        web_crawl.main([
            "--vault", str(vault),
            "--dry-run",
            "--trace-out", trace_out_path,
        ])

        content = Path(trace_out_path).read_text(encoding="utf-8")
        assert "## Harvest" in content

    def test_harvest_error_recorded_in_trace(self, tmp_path, monkeypatch):
        """When a harvest function raises, the error is captured in the trace queries."""
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_rank(monkeypatch)

        import web_harvest as wh

        def _raise_papers(*a, **kw):
            raise RuntimeError("simulated arxiv down")

        monkeypatch.setattr(wh, "poll_rss", lambda *a, **kw: list(CANNED_RSS))
        monkeypatch.setattr(wh, "query_papers", _raise_papers)
        monkeypatch.setattr(wh, "query_forum", lambda *a, **kw: [])
        monkeypatch.setattr(wh, "poll_github_releases", lambda *a, **kw: [])

        result = web_crawl.crawl(str(vault), dry_run=True, today="2026-06-22")
        trace = result["trace"]
        harvest_recs = trace.find("harvest", "target")
        # At least one harvest record should have an error entry in queries
        all_queries = [
            q for rec in harvest_recs for q in rec["data"].get("queries", [])
        ]
        # Some query entries should carry an 'error' key from the raised exception
        error_entries = [q for q in all_queries if "error" in q]
        assert len(error_entries) >= 1, (
            "Expected at least one error-marked query in harvest trace records"
        )


# ===========================================================================
# Tests: duplicate-harvest-call dedup within a target
# ===========================================================================

# Three arxiv-category registry sources that all map to engine="arxiv".
# In the old code, 3 sources x 2 queries = 6 query_papers calls.
# With the dedup fix, (engine, query) uniqueness -> exactly 2 calls.
_THREE_ARXIV_SOURCES_REG = {
    "sources": [
        {
            "id": "arxiv_cs_ar",
            "name": "arXiv cs.AR",
            "url": "https://arxiv.org/list/cs.AR/recent",
            "rss_feed": "https://arxiv.org/list/cs.AR/rss",
            "api_endpoint": "https://api.arxiv.org/query",
            "category": "preprint_repository",
            "focus": "Hardware architectures",
            "relevance": 5,
            "priority": "critical",
        },
        {
            "id": "arxiv_cs_dc",
            "name": "arXiv cs.DC",
            "url": "https://arxiv.org/list/cs.DC/recent",
            "rss_feed": "https://arxiv.org/list/cs.DC/rss",
            "api_endpoint": "https://api.arxiv.org/query",
            "category": "preprint_repository",
            "focus": "Distributed computing",
            "relevance": 5,
            "priority": "critical",
        },
        {
            "id": "arxiv_cs_lg",
            "name": "arXiv cs.LG",
            "url": "https://arxiv.org/list/cs.LG/recent",
            "rss_feed": "https://arxiv.org/list/cs.LG/rss",
            "api_endpoint": "https://api.arxiv.org/query",
            "category": "preprint_repository",
            "focus": "Machine learning",
            "relevance": 5,
            "priority": "critical",
        },
    ]
}


class TestHarvestCallDedup:
    """Dedup tests for harvest calls within a single target.

    With the arXiv-CATEGORY routing enhancement, dedup keys are 3-tuples:
    (engine, query, category).  Three arxiv-category registry sources (cs.AR,
    cs.DC, cs.LG) each derive a DISTINCT category, so 3 sources x 2 seed
    queries = 6 distinct (engine, query, category) calls -- NOT collapsed to 2.

    The old generic dedup (2 calls for 3 sources) now only applies when no
    category is derivable (category=None), e.g. generic "arxiv" routed sources
    without an id-based or url-based category mapping.
    """

    def _make_three_arxiv_registry(self) -> dict:
        return {
            "paper-publisher": _THREE_ARXIV_SOURCES_REG,
            "blog-newsfeed": {"sources": []},
            "github-repos": {"repositories": []},
        }

    def test_three_arxiv_sources_two_queries_fires_six_distinct_calls(self):
        """3 arxiv-category sources + 2 seed queries -> 6 distinct per-category calls.

        Each (engine, query, category) triple is unique, so all 6 fire.
        This is the MEANINGFUL behaviour: cs.AR + query-A, cs.DC + query-A,
        cs.LG + query-A, cs.AR + query-B, cs.DC + query-B, cs.LG + query-B.
        """
        registry = self._make_three_arxiv_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["disaggregated LLM inference", "prefill decode GPU scheduling"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_ar", "arxiv_cs_dc", "arxiv_cs_lg"],
        }

        call_log: list[tuple] = []

        class _SpyHarvest:
            def query_papers(self, q, *, engine, limit, category=None):
                call_log.append((engine, q, category))
                return list(CANNED_ARXIV)

            def poll_rss(self, *a, **kw):
                return []

            def query_forum(self, *a, **kw):
                return []

            def poll_github_releases(self, *a, **kw):
                return []

        spy = _SpyHarvest()
        web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=spy
        )

        # 3 categories x 2 queries = 6 distinct calls
        assert len(call_log) == 6, (
            f"Expected 6 distinct per-category query_papers calls "
            f"(3 categories x 2 queries), got {len(call_log)}: {call_log}"
        )
        # All calls are for engine="arxiv"
        assert all(c[0] == "arxiv" for c in call_log)
        # Each (engine, query, category) triple is unique (no duplicates)
        assert len(set(call_log)) == 6, (
            f"Duplicate (engine, query, category) tuples found: {call_log}"
        )
        # All 3 categories appear in the call log
        categories_seen = {c[2] for c in call_log}
        assert categories_seen == {"cs.AR", "cs.DC", "cs.LG"}, (
            f"Expected categories cs.AR, cs.DC, cs.LG; got {categories_seen}"
        )

    def test_identical_engine_query_category_still_dedupes(self):
        """Identical (engine, query, category) triple is deduped to 1 call."""
        # Build a registry with two IDENTICAL arxiv_cs_ar entries (id collision
        # is unusual but tests the dedup guard directly).
        registry = {
            "paper-publisher": {
                "sources": [
                    {
                        "id": "arxiv_cs_ar",
                        "name": "arXiv cs.AR (primary)",
                        "url": "https://arxiv.org/list/cs.AR/recent",
                        "rss_feed": "https://arxiv.org/list/cs.AR/rss",
                        "category": "preprint_repository",
                        "focus": "Hardware architectures",
                        "relevance": 5,
                        "priority": "critical",
                    },
                    {
                        "id": "arxiv_cs_ar_dup",
                        "name": "arXiv cs.AR (duplicate)",
                        "url": "https://arxiv.org/list/cs.AR/recent",
                        "rss_feed": "https://arxiv.org/list/cs.AR/rss",
                        "category": "preprint_repository",
                        "focus": "Hardware architectures",
                        "relevance": 5,
                        "priority": "critical",
                    },
                ]
            },
            "blog-newsfeed": {"sources": []},
            "github-repos": {"repositories": []},
        }
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["kv cache hardware"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_ar", "arxiv_cs_ar_dup"],
        }

        call_log: list[tuple] = []

        class _SpyHarvest:
            def query_papers(self, q, *, engine, limit, category=None):
                call_log.append((engine, q, category))
                return list(CANNED_ARXIV)

            def poll_rss(self, *a, **kw):
                return []

            def query_forum(self, *a, **kw):
                return []

            def poll_github_releases(self, *a, **kw):
                return []

        spy = _SpyHarvest()
        web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5, _harvest_mod=spy
        )

        # arxiv_cs_ar -> category=cs.AR (from _ARXIV_ID_TO_CATEGORY)
        # arxiv_cs_ar_dup -> not in _ARXIV_ID_TO_CATEGORY but rss_feed contains
        #   /list/cs.AR/ -> also resolves to cs.AR -> same triple -> deduped
        assert len(call_log) == 1, (
            f"Expected 1 call (duplicate cs.AR deduped), got {len(call_log)}: {call_log}"
        )
        assert call_log[0] == ("arxiv", "kv cache hardware", "cs.AR")

    def test_trace_has_no_duplicate_engine_query_rows(self):
        """The harvest/target trace has no duplicate (engine, query) rows for distinct categories."""
        from web_decision import DecisionTrace

        registry = self._make_three_arxiv_registry()
        target = {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["disaggregated LLM inference", "prefill decode GPU scheduling"],
            "fillable_by": ["arxiv"],
            "routed_sources": ["arxiv_cs_ar", "arxiv_cs_dc", "arxiv_cs_lg"],
        }

        class _NopHarvest:
            def query_papers(self, q, *, engine, limit, category=None):
                return list(CANNED_ARXIV)

            def poll_rss(self, *a, **kw):
                return []

            def query_forum(self, *a, **kw):
                return []

            def poll_github_releases(self, *a, **kw):
                return []

        trace = DecisionTrace()
        web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT,
            per_source_limit=5, _harvest_mod=_NopHarvest(), trace=trace
        )

        harvest_recs = trace.find("harvest", "target")
        assert len(harvest_recs) == 1
        queries_in_trace = harvest_recs[0]["data"]["queries"]

        # Build (engine, query_display) pairs from the trace and check for duplicates.
        pairs = [(q["engine"], q["query"]) for q in queries_in_trace]
        assert len(pairs) == len(set(pairs)), (
            f"Duplicate (engine, query) rows in trace: {pairs}"
        )
        # 3 categories x 2 queries = 6 trace entries.
        assert len(pairs) == 6, (
            f"Expected 6 trace entries (3 categories x 2 queries), "
            f"got {len(pairs)}: {pairs}"
        )
        # Trace query strings should include [cat:X] annotations
        cat_annotated = [q["query"] for q in queries_in_trace if "[cat:" in q["query"]]
        assert len(cat_annotated) == 6, (
            f"Expected all 6 trace entries to include [cat:...], "
            f"got {len(cat_annotated)}: {queries_in_trace}"
        )

    def test_distinct_feed_urls_are_not_deduped(self):
        """Two different RSS feed URLs for the same lane are two distinct calls."""
        registry = {
            "paper-publisher": {"sources": []},
            "blog-newsfeed": {
                "sources": [
                    {
                        "id": "feed_a",
                        "name": "Feed A",
                        "url": "https://feed-a.example.com/",
                        "rss_feed": "https://feed-a.example.com/rss",
                        "category": "vendor_blogs",
                        "focus": "topic",
                        "relevance": 5,
                        "priority": "critical",
                    },
                    {
                        "id": "feed_b",
                        "name": "Feed B",
                        "url": "https://feed-b.example.com/",
                        "rss_feed": "https://feed-b.example.com/rss",
                        "category": "vendor_blogs",
                        "focus": "topic",
                        "relevance": 5,
                        "priority": "critical",
                    },
                ]
            },
            "github-repos": {"repositories": []},
        }
        target = {
            "target_id": "news",
            "lane": "news",
            "origin_ids": ["news"],
            "queries": [],
            "fillable_by": ["web"],
            "routed_sources": ["feed_a", "feed_b"],
        }

        rss_call_log: list[str] = []

        class _SpyHarvest:
            def poll_rss(self, feed_url, *, source_id, limit):
                rss_call_log.append(feed_url)
                return list(CANNED_RSS)

            def query_papers(self, *a, **kw):
                return []

            def query_forum(self, *a, **kw):
                return []

            def poll_github_releases(self, *a, **kw):
                return []

        web_crawl._harvest_target(
            target, registry, _PURPOSE_TEXT, per_source_limit=5,
            _harvest_mod=_SpyHarvest()
        )

        # Two DIFFERENT feed URLs -> two RSS calls (no dedup across distinct URLs)
        assert len(rss_call_log) == 2, (
            f"Expected 2 distinct poll_rss calls for different feed URLs, "
            f"got {len(rss_call_log)}: {rss_call_log}"
        )
        assert rss_call_log[0] != rss_call_log[1]


# ===========================================================================
# Tests: _content_rationale helper
# ===========================================================================

class TestContentRationale:
    """_content_rationale derives a short rationale from the candidate snippet."""

    def test_plain_snippet_returned_as_rationale(self):
        cand = {
            "snippet": "This paper surveys disaggregated inference approaches.",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
        }
        result = web_crawl._content_rationale(cand)
        assert "disaggregated inference" in result
        assert "<" not in result  # no HTML tags

    def test_html_tags_stripped(self):
        cand = {
            "snippet": "<b>Fast</b> inference <em>scheduling</em> for LLMs.",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
        }
        result = web_crawl._content_rationale(cand)
        assert "<b>" not in result
        assert "<em>" not in result
        assert "Fast" in result
        assert "scheduling" in result

    def test_long_snippet_truncated_at_sentence_boundary(self):
        # 200-char sentence followed by more text
        long_snip = "A" * 150 + ". And then some extra long tail " + "X" * 100
        cand = {"snippet": long_snip, "lane": "gap", "origin_ids": []}
        result = web_crawl._content_rationale(cand)
        assert len(result) <= 225  # at most 220 + a tiny bit

    def test_long_snippet_no_sentence_boundary_gets_ellipsis(self):
        cand = {"snippet": "A" * 300, "lane": "gap", "origin_ids": []}
        result = web_crawl._content_rationale(cand)
        assert result.endswith("...")
        assert len(result) <= 225

    def test_empty_snippet_falls_back_to_lane_origin(self):
        cand = {"snippet": "", "lane": "research", "origin_ids": ["GAP-02", "DIR-0001"]}
        result = web_crawl._content_rationale(cand)
        assert "lane=research" in result
        assert "GAP-02" in result

    def test_missing_snippet_falls_back(self):
        cand = {"lane": "news", "origin_ids": ["news"]}
        result = web_crawl._content_rationale(cand)
        assert "lane=news" in result

    def test_whitespace_only_snippet_falls_back(self):
        cand = {"snippet": "   \n\t  ", "lane": "gap", "origin_ids": ["GAP-01"]}
        result = web_crawl._content_rationale(cand)
        assert "lane=gap" in result

    def test_newlines_collapsed_in_snippet(self):
        cand = {"snippet": "Line one.\nLine two.\nLine three.", "lane": "gap",
                "origin_ids": ["GAP-01"]}
        result = web_crawl._content_rationale(cand)
        assert "\n" not in result


# ===========================================================================
# Tests: interactive nightly report format
# ===========================================================================

class TestInteractiveReportFormat:
    """_write_nightly_report emits the pinned interactive Obsidian checkbox format."""

    def _make_vault(self, tmp_path: Path) -> Path:
        vault = tmp_path / "vault"
        vault.mkdir()
        # Create direction file so _relevance_links can find it
        dir_dir = vault / "objective" / "direction"
        dir_dir.mkdir(parents=True)
        (dir_dir / "DIR-0001-test-direction.md").write_text("", encoding="utf-8")
        return vault

    def test_approve_checkbox_present_with_ident(self, tmp_path):
        from web_harvest import candidate_ident
        vault = self._make_vault(tmp_path)
        c = _make_candidate(
            "Test Paper",
            source_id="arxiv:2401.10001",
            url="https://arxiv.org/abs/2401.10001",
            lane="gap",
            origin_ids=["GAP-01"],
        )
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")

        ident = candidate_ident(c)
        assert f"- [ ] approve" in text
        assert f"`{ident}`" in text
        # approve line must contain the ident in backticks
        approve_line = next(
            (ln for ln in text.splitlines() if "approve" in ln and ident in ln), None
        )
        assert approve_line is not None, (
            f"No approve line with ident {ident!r} found in:\n{text}"
        )
        assert approve_line.startswith("- [ ] approve"), (
            f"approve line does not start with '- [ ] approve': {approve_line!r}"
        )

    def test_reject_checkbox_present_with_same_ident(self, tmp_path):
        from web_harvest import candidate_ident
        vault = self._make_vault(tmp_path)
        c = _make_candidate(
            "Test Paper",
            source_id="arxiv:2401.10001",
            url="https://arxiv.org/abs/2401.10001",
            lane="gap",
            origin_ids=["GAP-01"],
        )
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")

        ident = candidate_ident(c)
        reject_line = next(
            (ln for ln in text.splitlines() if "reject" in ln and ident in ln
             and "reason" not in ln), None
        )
        assert reject_line is not None, (
            f"No reject line with ident {ident!r} found in:\n{text}"
        )
        assert reject_line.startswith("- [ ] reject"), (
            f"reject line does not start with '- [ ] reject': {reject_line!r}"
        )

    def test_approve_and_reject_ident_are_identical(self, tmp_path):
        from web_harvest import candidate_ident
        vault = self._make_vault(tmp_path)
        c = _make_candidate(
            "Ident Match Paper",
            source_id="arxiv:2401.99999",
            url="https://arxiv.org/abs/2401.99999",
            lane="gap",
            origin_ids=["GAP-01"],
        )
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")

        expected_ident = candidate_ident(c)
        lines = text.splitlines()

        approve_idents = [
            ln.split("`")[1]
            for ln in lines
            if ln.startswith("- [ ] approve") and "`" in ln
        ]
        reject_idents = [
            ln.split("`")[1]
            for ln in lines
            if ln.startswith("- [ ] reject") and "`" in ln
        ]

        assert len(approve_idents) == 1
        assert len(reject_idents) == 1
        assert approve_idents[0] == reject_idents[0] == expected_ident, (
            f"approve ident={approve_idents[0]!r}, reject ident={reject_idents[0]!r}, "
            f"expected={expected_ident!r}"
        )

    def test_ident_equals_candidate_ident(self, tmp_path):
        """The ident in the report == candidate_ident(cand) == what enqueue receives."""
        from web_harvest import candidate_ident
        vault = self._make_vault(tmp_path)
        # RSS-style candidate: ident = URL, not source_id
        c = {
            "title": "RSS Post",
            "url": "https://developer.nvidia.com/blog/some-post",
            "source_id": "nvidia_developer_blog",
            "id_type": "url",
            "published": "2026-06-10",
            "snippet": "GPU inference improvements.",
            "engine": "rss",
            "lane": "news",
            "origin_ids": ["news"],
            "score": 0.6,
        }
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")

        expected_ident = candidate_ident(c)
        assert expected_ident == "https://developer.nvidia.com/blog/some-post"
        # The URL ident (not the feed source_id) must appear in the approve line
        assert f"`{expected_ident}`" in text
        assert "nvidia_developer_blog" not in text.split("approve")[1].split("\n")[0]

    def test_reason_indented_sub_bullet_present(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c = _make_candidate("Paper with reason slot", lane="gap", origin_ids=["GAP-01"])
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        # The reason line must be indented (4 spaces) and immediately follow reject line
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("- [ ] reject") and i + 1 < len(lines):
                reason_line = lines[i + 1]
                assert reason_line.startswith("    - reason:"), (
                    f"Expected '    - reason:' after reject line, got {reason_line!r}"
                )
                break
        else:
            pytest.fail("No reject line found in report")

    def test_published_field_present(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c = _make_candidate("Dated Paper", published="2026-05-15", lane="gap",
                            origin_ids=["GAP-01"])
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        assert "**published**" in text
        assert "2026-05-15" in text

    def test_published_field_dash_when_absent(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c = dict(_make_candidate("No-date Paper", lane="gap", origin_ids=["GAP-01"]))
        c["published"] = ""
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        # Should contain em-dash for missing date
        assert "—" in text  # —

    def test_score_field_present(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c = _make_candidate("Scored Paper", score=0.9123, lane="gap",
                            origin_ids=["GAP-01"])
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        assert "**score**" in text
        assert "0.9123" in text

    def test_relevance_field_present(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c = _make_candidate("Relevance Paper", lane="gap", origin_ids=["GAP-01"])
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        assert "**relevance**" in text
        # GAP-01 -> wiki/gaps wikilink
        assert "[[wiki/gaps]]" in text

    def test_rationale_field_present(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c = _make_candidate(
            "Rationale Paper",
            lane="gap",
            origin_ids=["GAP-01"],
        )
        c["snippet"] = "This paper describes disaggregated serving with measurable gains."
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        assert "**rationale**" in text
        assert "disaggregated serving" in text

    def test_url_field_present(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c = _make_candidate(
            "URL Paper",
            url="https://arxiv.org/abs/2401.55555",
            lane="gap",
            origin_ids=["GAP-01"],
        )
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        assert "**url**" in text
        assert "https://arxiv.org/abs/2401.55555" in text

    def test_instruction_line_present(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c = _make_candidate("Instruction Test", lane="gap", origin_ids=["GAP-01"])
        report_path = web_crawl._write_nightly_report(str(vault), "2026-06-22", [c])
        text = report_path.read_text(encoding="utf-8")
        assert "wiki-approve" in text

    def test_decision_trace_section_present(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "## Decision trace" in report_text

    def test_multiple_candidates_numbered(self, tmp_path):
        vault = self._make_vault(tmp_path)
        c1 = _make_candidate("First Paper", source_id="arxiv:2401.10001",
                              url="https://arxiv.org/abs/2401.10001",
                              lane="gap", origin_ids=["GAP-01"])
        c2 = _make_candidate("Second Paper", source_id="arxiv:2401.10002",
                              url="https://arxiv.org/abs/2401.10002",
                              lane="gap", origin_ids=["GAP-01"])
        report_path = web_crawl._write_nightly_report(
            str(vault), "2026-06-22", [c1, c2]
        )
        text = report_path.read_text(encoding="utf-8")
        assert "### 1. First Paper" in text
        assert "### 2. Second Paper" in text


# ===========================================================================
# Tests: enqueue called with published= and rationale= (content rationale)
# ===========================================================================

class TestEnqueuePublishedAndRationale:
    """crawl() passes published= and content rationale to ingest_index.enqueue."""

    def test_enqueue_receives_published(self, tmp_path, monkeypatch):
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        selected = result["selected"]
        if not selected:
            pytest.skip("No candidates selected")

        # Read the ingest index and check published was stored
        index_path = vault / "meta" / "ingest_index.json"
        assert index_path.exists()
        data = json.loads(index_path.read_text())
        waiting = [
            r for r in data.get("sources", {}).values()
            if r.get("status") == "waiting_approval"
        ]
        # Each candidate in CANNED_* has published="2026-06-01"; it must be stored
        for row in waiting:
            assert "published" in row, (
                f"Row {row['id']!r} missing 'published' field: {row}"
            )
            assert row["published"] == "2026-06-01", (
                f"Row {row['id']!r} published={row['published']!r}, expected '2026-06-01'"
            )

    def test_enqueue_rationale_equals_content_rationale(self, tmp_path, monkeypatch):
        """The rationale stored in the ingest index equals _content_rationale(cand)."""
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        selected = result["selected"]
        if not selected:
            pytest.skip("No candidates selected")

        index_path = vault / "meta" / "ingest_index.json"
        data = json.loads(index_path.read_text())

        for c in selected:
            from web_harvest import candidate_ident
            ident = candidate_ident(c)
            # _normalize_enqueue_id maps ident -> sid
            import agents.ingest_index as _rii
            sid, _, _ = _rii._normalize_enqueue_id(ident)
            row = data["sources"].get(sid)
            if row is None:
                continue
            expected_rationale = web_crawl._content_rationale(c)
            assert row.get("rationale") == expected_rationale, (
                f"Row {sid!r} rationale mismatch:\n"
                f"  stored:   {row.get('rationale')!r}\n"
                f"  expected: {expected_rationale!r}"
            )

    def test_enqueue_ident_matches_report_and_candidate_ident(self, tmp_path, monkeypatch):
        """enqueue id == candidate_ident == ident on approve/reject lines in report."""
        vault = _make_tmp_vault(tmp_path)
        _patch_loaders(monkeypatch, tmp_path, vault)
        _patch_harvest(monkeypatch)
        _patch_rank(monkeypatch)

        result = web_crawl.crawl(str(vault), dry_run=False, today="2026-06-22")
        selected = result["selected"]
        if not selected:
            pytest.skip("No candidates selected")

        from web_harvest import candidate_ident
        import agents.ingest_index as _rii

        report_text = Path(result["report_path"]).read_text(encoding="utf-8")
        index_path = vault / "meta" / "ingest_index.json"
        data = json.loads(index_path.read_text())

        for c in selected:
            ident = candidate_ident(c)
            sid, _, _ = _rii._normalize_enqueue_id(ident)

            # ident must appear in the report (approve/reject lines)
            assert f"`{ident}`" in report_text, (
                f"Ident {ident!r} not found in report as backtick-wrapped token"
            )
            # ident must have produced a row in the index
            assert sid in data["sources"], (
                f"Ident {ident!r} (sid={sid!r}) not found in ingest index"
            )
