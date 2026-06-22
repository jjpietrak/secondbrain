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
                    discovered_by="", objective_ids=None, name=None):
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
