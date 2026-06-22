"""tests/test_web_decision.py -- hermetic, offline, deterministic tests for web_decision.py.

All fixtures are inline or created in tmp_path; no live vault access.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from web_decision import (
    assign_lanes,
    build_plan,
    merge_targets,
    news_target,
    parse_directions,
    parse_gaps,
    route_to_sources,
    select_candidates,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GAPS_FIXTURE_TEXT = """\
---
type: gaps_report
created: 2026-06-01
vault: test-vault
---

## Open-Question Harvest (TOP PRIORITY)

Some questions here.

## Knowledge Gaps

### GAP-08: Optical prior art -- citations [3]-[6] from "Photons to Tokens" not ingested
- shows_up_in: [[wiki/sources/photons-to-tokens]] Open Questions; ...
- missing: entity/source pages for these four cited papers; they contain device parameters
- fillable_by: arxiv (all four are likely arXiv papers based on citation style)
- topic: T-0006
- priority: medium -- may provide the optical roofline range needed to close GAP-01

### GAP-04: LLM serving systems coverage is thin
- shows_up_in: T-0003 Coverage Map; [[wiki/entities/splitwise]] is a stub
- missing: ingested source pages for seminal P/D systems (DistServe arXiv 2401.09670, Splitwise)
- fillable_by: arxiv (DistServe, Splitwise, Mooncake); web (SGLang/TensorRT-LLM docs); github (vllm repo)
- topic: T-0003
- priority: high -- vault has almost no content; gaps in P/D scheduling limit the simulator

## Stale / Unverified

- Some stale notes here.
"""

DIR_WITH_PLAIN_QUERIES = """\
---
type: direction
id: DIR-0004
created: 2026-06-22
updated: 2026-06-22
serves_question: Q-0005
topics: T-0006, T-0007
targets_gap: Four optical-AI prior-art papers cited by Photons-to-Tokens are not yet ingested
priority: medium
status: open
written_by: research
---

## reasoning_pattern
Per-paper analysis pass.

## expected_evidence
A comparative table of device params.

## seed_queries
- LightML Liu et al ISCA 2025 optical accelerator MZI HBM2E LLM throughput
- Demirkiran electrophotonic deep learning accelerator SRAM PCIe
- arxiv: optical LLM accelerator prior art comparison

## solves_when
Q-0005 acceptance criterion met.
"""

DIR_SOLVED = """\
---
type: direction
id: DIR-0099
created: 2026-06-01
serves_question: Q-0099
topics: T-0001
targets_gap: Already resolved gap
priority: low
status: solved
written_by: research
---

## seed_queries
- some query

## expected_evidence
Already there.

## solves_when
Done.
"""

DIR_STANDALONE = """\
---
type: direction
id: DIR-0010
created: 2026-06-22
serves_question: Q-0010
topics: T-0099
targets_gap: Some standalone research direction not matching any gap
priority: high
status: open
written_by: research
---

## seed_queries
- standalone research query one
- web: standalone web query two

## expected_evidence
Some evidence.

## solves_when
When done.
"""


REGISTRY_FIXTURE = {
    "paper-publisher": {
        "sources": [
            {
                "id": "arxiv_cs_ar",
                "name": "arXiv cs.AR",
                "url": "https://arxiv.org/list/cs.AR/recent",
                "rss_feed": "https://arxiv.org/list/cs.AR/rss",
                "api_endpoint": "https://api.arxiv.org/query",
                "category": "preprint_repository",
                "focus": "hardware architecture AI accelerators",
                "relevance": 5,
                "priority": "critical",
            },
            {
                "id": "semantic_scholar",
                "name": "Semantic Scholar",
                "url": "https://www.semanticscholar.org/",
                "rss_feed": None,
                "api_endpoint": "https://api.semanticscholar.org/",
                "category": "paper_api",
                "focus": "full-text search papers",
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
                "focus": "distributed inference disaggregation systems",
                "relevance": 5,
                "priority": "critical",
            },
            {
                "id": "arxiv_eess_sp",
                "name": "arXiv eess.SP",
                "url": "https://arxiv.org/list/eess.SP/recent",
                "rss_feed": "https://arxiv.org/list/eess.SP/rss",
                "api_endpoint": "https://api.arxiv.org/query",
                "category": "preprint_repository",
                "focus": "signal processing",
                "relevance": 2,
                "priority": "low",
            },
        ]
    },
    "github-repos": {
        "repositories": [
            {
                "id": "vllm",
                "name": "vLLM",
                "url": "https://github.com/vllm-project/vllm",
                "category": "inference_frameworks",
                "focus": "inference server PagedAttention disaggregation",
                "relevance": 5,
                "priority": "critical",
            },
            {
                "id": "tvm",
                "name": "TVM",
                "url": "https://github.com/apache/tvm",
                "category": "compiler_infrastructure",
                "focus": "compiler heterogeneous accelerators",
                "relevance": 5,
                "priority": "critical",
            },
        ]
    },
    "blog-newsfeed": {
        "sources": [
            {
                "id": "nvidia_developer_blog",
                "name": "NVIDIA Developer Blog",
                "url": "https://developer.nvidia.com/blog/",
                "rss_feed": "https://developer.nvidia.com/blog/feed",
                "category": "vendor_blogs",
                "focus": "CUDA TensorRT GPU architectures inference optimization",
                "relevance": 5,
                "priority": "critical",
            },
            {
                "id": "semianalysis_newsletter",
                "name": "SemiAnalysis Newsletter",
                "url": "https://newsletter.semianalysis.com/",
                "rss_feed": "https://semianalysis.substack.com/feed",
                "category": "hardware_analysis",
                "focus": "AI accelerator landscape custom silicon market analysis",
                "relevance": 5,
                "priority": "critical",
            },
            {
                "id": "inferencex_semianalysis",
                "name": "InferenceX by SemiAnalysis",
                "url": "https://inferencex.semianalysis.com/blog",
                "rss_feed": "https://inferencex.semianalysis.com/blog/feed",
                "category": "benchmarking",
                "focus": "inference benchmarks reproducible results",
                "relevance": 5,
                "priority": "critical",
            },
            {
                "id": "vllm_blog",
                "name": "vLLM Blog",
                "url": "https://blog.vllm.ai/",
                "rss_feed": "https://blog.vllm.ai/feed",
                "category": "inference_frameworks",
                "focus": "vLLM serving optimizations disaggregation",
                "relevance": 5,
                "priority": "critical",
            },
            {
                "id": "the_batch_newsletter",
                "name": "The Batch",
                "url": "https://www.deeplearning.ai/the-batch/",
                "rss_feed": "https://www.deeplearning.ai/feed",
                "category": "specialist_research",
                "focus": "broad AI trends emerging models",
                "relevance": 3,
                "priority": "medium",
            },
            {
                "id": "stanford_ai_lab",
                "name": "Stanford AI Lab Blog",
                "url": "https://ai.stanford.edu/blog/",
                "rss_feed": "https://ai.stanford.edu/feed.xml",
                "category": "research_institutions",
                "focus": "research AI infrastructure kernel optimization",
                "relevance": 4,
                "priority": "high",
            },
        ]
    },
}

CONFIG_FIXTURE = {
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


# ---------------------------------------------------------------------------
# parse_gaps tests
# ---------------------------------------------------------------------------


class TestParseGaps:
    def test_parses_two_gaps(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        assert len(gaps) == 2

    def test_gap08_id_and_title(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert g08["title"] == 'Optical prior art -- citations [3]-[6] from "Photons to Tokens" not ingested'

    def test_gap08_single_topic(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert g08["topics"] == ["T-0006"]

    def test_gap08_fillable_by_bare_tag(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert g08["fillable_by"] == ["arxiv"]

    def test_gap08_priority_from_first_word(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert g08["priority"] == "medium"

    def test_gap04_multi_topic(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        g04 = next(g for g in gaps if g["id"] == "GAP-04")
        assert "T-0003" in g04["topics"]

    def test_gap04_multi_tag_fillable_by(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        g04 = next(g for g in gaps if g["id"] == "GAP-04")
        tags = g04["fillable_by"]
        assert "arxiv" in tags
        assert "web" in tags
        assert "github" in tags

    def test_gap04_priority_high(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        g04 = next(g for g in gaps if g["id"] == "GAP-04")
        assert g04["priority"] == "high"

    def test_no_stale_section_parsed_as_gap(self):
        gaps = parse_gaps(GAPS_FIXTURE_TEXT)
        ids = [g["id"] for g in gaps]
        assert all(gid.startswith("GAP-") for gid in ids)

    def test_missing_section_returns_empty(self):
        gaps = parse_gaps("# Some file\n\nNo knowledge gaps section here.\n")
        assert gaps == []

    def test_missing_field_returns_empty_string(self):
        text = """\
## Knowledge Gaps

### GAP-99: Minimal gap
- shows_up_in: somewhere
- missing: something
- fillable_by: arxiv
- topic: T-0001
- priority: low
"""
        gaps = parse_gaps(text)
        assert len(gaps) == 1
        assert gaps[0]["id"] == "GAP-99"
        assert gaps[0]["priority"] == "low"


# ---------------------------------------------------------------------------
# parse_directions tests
# ---------------------------------------------------------------------------


class TestParseDirections:
    def test_parses_open_dir(self, tmp_path):
        d = tmp_path / "direction"
        d.mkdir()
        (d / "DIR-0004-optical.md").write_text(DIR_WITH_PLAIN_QUERIES, encoding="utf-8")
        dirs = parse_directions(str(d))
        assert len(dirs) == 1
        assert dirs[0]["id"] == "DIR-0004"

    def test_skips_solved_status(self, tmp_path):
        d = tmp_path / "direction"
        d.mkdir()
        (d / "DIR-0099-solved.md").write_text(DIR_SOLVED, encoding="utf-8")
        (d / "DIR-0004-optical.md").write_text(DIR_WITH_PLAIN_QUERIES, encoding="utf-8")
        dirs = parse_directions(str(d))
        assert len(dirs) == 1
        assert dirs[0]["id"] == "DIR-0004"

    def test_skips_template(self, tmp_path):
        d = tmp_path / "direction"
        d.mkdir()
        template_text = """\
---
type: direction
id: DIR-NNNN
status: open
topics: []
serves_question: Q-NNNN
targets_gap: ""
priority: medium
---
## seed_queries
- some query
"""
        (d / "_template.md").write_text(template_text, encoding="utf-8")
        (d / "DIR-0004-optical.md").write_text(DIR_WITH_PLAIN_QUERIES, encoding="utf-8")
        dirs = parse_directions(str(d))
        # _template.md skipped because name starts with _
        assert all(di["id"] != "DIR-NNNN" for di in dirs)
        assert len(dirs) == 1

    def test_plain_and_engine_tagged_seed_queries(self, tmp_path):
        d = tmp_path / "direction"
        d.mkdir()
        (d / "DIR-0004-optical.md").write_text(DIR_WITH_PLAIN_QUERIES, encoding="utf-8")
        dirs = parse_directions(str(d))
        dir0 = dirs[0]
        queries = dir0["seed_queries"]
        # Three queries: two plain, one engine-tagged
        assert len(queries) == 3
        plain = [q for q in queries if q["engine"] is None]
        tagged = [q for q in queries if q["engine"] == "arxiv"]
        assert len(plain) == 2
        assert len(tagged) == 1
        assert tagged[0]["query"] == "optical LLM accelerator prior art comparison"

    def test_topics_parsed_from_frontmatter(self, tmp_path):
        d = tmp_path / "direction"
        d.mkdir()
        (d / "DIR-0004-optical.md").write_text(DIR_WITH_PLAIN_QUERIES, encoding="utf-8")
        dirs = parse_directions(str(d))
        assert "T-0006" in dirs[0]["topics"]
        assert "T-0007" in dirs[0]["topics"]

    def test_expected_evidence_captured(self, tmp_path):
        d = tmp_path / "direction"
        d.mkdir()
        (d / "DIR-0004-optical.md").write_text(DIR_WITH_PLAIN_QUERIES, encoding="utf-8")
        dirs = parse_directions(str(d))
        assert "comparative table" in dirs[0]["expected_evidence"].lower()

    def test_nonexistent_dir_returns_empty(self):
        dirs = parse_directions("/nonexistent/path/direction")
        assert dirs == []


# ---------------------------------------------------------------------------
# merge_targets tests
# ---------------------------------------------------------------------------


class TestMergeTargets:
    def _make_gap08(self):
        return {
            "id": "GAP-08",
            "title": "Optical prior art not ingested",
            "shows_up_in": "wiki/sources/photons-to-tokens",
            "missing": "entity pages for four cited papers",
            "fillable_by": ["arxiv"],
            "topics": ["T-0006"],
            "priority": "medium",
        }

    def _make_dir0004(self):
        return {
            "id": "DIR-0004",
            "serves_question": ["Q-0005"],
            "topics": ["T-0006", "T-0007"],
            "targets_gap": "Four optical-AI prior-art papers cited by Photons-to-Tokens are not yet ingested",
            "priority": "medium",
            "status": "open",
            "seed_queries": [
                {"engine": None, "query": "LightML Liu et al ISCA 2025"},
                {"engine": None, "query": "Demirkiran electrophotonic"},
                {"engine": "arxiv", "query": "optical LLM prior art"},
            ],
            "expected_evidence": "Comparative table of device params",
            "solves_when": "Q-0005 met",
            "_engine_tags": ["arxiv"],
        }

    def _make_standalone_dir(self):
        return {
            "id": "DIR-0010",
            "serves_question": ["Q-0010"],
            "topics": ["T-0099"],
            "targets_gap": "Some standalone direction not matching any gap",
            "priority": "high",
            "status": "open",
            "seed_queries": [
                {"engine": None, "query": "standalone research query"},
                {"engine": "web", "query": "standalone web query"},
            ],
            "expected_evidence": "Some evidence",
            "solves_when": "Done",
            "_engine_tags": ["web"],
        }

    def test_gap_dir_overlap_shared_topic(self):
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merged = merge_targets([gap], [direction])
        assert len(merged) == 1
        t = merged[0]
        assert "GAP-08" in t["origin_ids"]
        assert "DIR-0004" in t["origin_ids"]

    def test_merged_target_id_format(self):
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merged = merge_targets([gap], [direction])
        assert merged[0]["target_id"] == "GAP-08+DIR-0004"

    def test_merged_priority_is_max(self):
        gap = self._make_gap08()  # medium
        direction = self._make_dir0004()  # medium
        merged = merge_targets([gap], [direction])
        assert merged[0]["priority"] == "medium"

        # Make direction high to test max
        direction2 = dict(direction)
        direction2["priority"] = "high"
        merged2 = merge_targets([gap], [direction2])
        assert merged2[0]["priority"] == "high"

    def test_merged_queries_from_direction(self):
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merged = merge_targets([gap], [direction])
        queries = merged[0]["queries"]
        # Should use direction's seed queries (plain text)
        assert "LightML Liu et al ISCA 2025" in queries
        assert "Demirkiran electrophotonic" in queries

    def test_fillable_by_union(self):
        gap = self._make_gap08()  # fillable_by: [arxiv]
        direction = self._make_dir0004()  # _engine_tags: [arxiv]
        merged = merge_targets([gap], [direction])
        # Union: arxiv appears from both; deduped
        assert "arxiv" in merged[0]["fillable_by"]

    def test_standalone_gap_no_direction(self):
        gap = self._make_gap08()
        merged = merge_targets([gap], [])
        assert len(merged) == 1
        t = merged[0]
        assert t["origin_ids"] == ["GAP-08"]
        assert t["lane"] == "gap"
        # Queries derived from title/missing
        assert len(t["queries"]) >= 1

    def test_standalone_direction_no_gap(self):
        standalone = self._make_standalone_dir()
        merged = merge_targets([], [standalone])
        assert len(merged) == 1
        t = merged[0]
        assert t["origin_ids"] == ["DIR-0010"]
        assert t["lane"] == "research"

    def test_unmatched_direction_goes_research_lane(self):
        gap = self._make_gap08()
        standalone = self._make_standalone_dir()
        merged = merge_targets([gap], [standalone])
        assert len(merged) == 2
        research_targets = [t for t in merged if t["lane"] == "research"]
        assert len(research_targets) == 1
        assert research_targets[0]["origin_ids"] == ["DIR-0010"]

    def test_gap_with_no_direction_has_gap_lane(self):
        gap = self._make_gap08()
        merged = merge_targets([gap], [])
        assert merged[0]["lane"] == "gap"

    # -- Regression tests: over-merge bug (one-to-one constraint) --

    def test_broad_topic_dir_does_not_merge_into_unrelated_gap(self):
        """DIR with broad topics (T-0004, T-0006) but dead-zone targets_gap must
        NOT merge into a gap about entities (T-0007 only, no lexical overlap)."""
        broad_dir = {
            "id": "DIR-BROAD",
            "serves_question": ["Q-X"],
            "topics": ["T-0004", "T-0006"],
            "targets_gap": "No quantitative phase diagram or B_ScaleOut target derived from Baidu Eq.7; optical fabric hypothesis qualitative only",
            "priority": "high",
            "status": "open",
            "seed_queries": [{"engine": None, "query": "baidu afd dead zone phase boundary"}],
            "expected_evidence": "Phase diagram",
            "solves_when": "Done",
            "_engine_tags": [],
        }
        gap_entities = {
            "id": "GAP-06",
            "title": "Specialised accelerator entities missing",
            "shows_up_in": "T-0007 Coverage Map",
            "missing": "entity pages for Cerebras, Tenstorrent, d-Matrix, AMD MI300X; Pareto frontier",
            "fillable_by": ["web", "arxiv"],
            "topics": ["T-0007"],
            "priority": "medium",
        }
        gap_kv = {
            "id": "GAP-05",
            "title": "Optical KV cache layout completely undefined",
            "shows_up_in": "disaggregation-thesis Open Questions",
            "missing": "memory layout spec for KV cache produced by an optical accelerator",
            "fillable_by": ["arxiv"],
            "topics": ["T-0002", "T-0006"],
            "priority": "high",
        }
        merged = merge_targets([gap_entities, gap_kv], [broad_dir])
        # broad_dir should not merge into GAP-06 (no lexical overlap, only broad topics)
        gap06_targets = [t for t in merged if "GAP-06" in t.get("origin_ids", [])]
        assert len(gap06_targets) == 1
        assert "DIR-BROAD" not in gap06_targets[0]["origin_ids"], (
            "broad-topic DIR must not merge into an entity gap with no lexical match"
        )

    def test_each_direction_consumed_at_most_once(self):
        """No direction id should appear in more than one merged target's origin_ids."""
        gap_a = {
            "id": "GAP-A",
            "title": "Prior art papers not ingested",
            "missing": "entity pages for four cited papers",
            "fillable_by": ["arxiv"],
            "topics": ["T-0006"],
            "priority": "medium",
        }
        gap_b = {
            "id": "GAP-B",
            "title": "Prior art papers missing comparative table",
            "missing": "cited papers and entity pages for optical prior art comparison",
            "fillable_by": ["arxiv"],
            "topics": ["T-0006"],
            "priority": "high",
        }
        dir_strong = {
            "id": "DIR-ONCE",
            "topics": ["T-0006", "T-0007"],
            "targets_gap": "Four optical-AI prior-art papers cited by Photons-to-Tokens not ingested",
            "priority": "medium",
            "seed_queries": [{"engine": None, "query": "optical prior art ingested cited papers"}],
            "expected_evidence": "Evidence",
            "solves_when": "Done",
            "_engine_tags": ["arxiv"],
        }
        merged = merge_targets([gap_a, gap_b], [dir_strong])
        # Collect all origin_ids across all targets and check DIR-ONCE appears at most once
        all_origin_ids = [oid for t in merged for oid in t.get("origin_ids", [])]
        dir_count = all_origin_ids.count("DIR-ONCE")
        assert dir_count <= 1, (
            f"DIR-ONCE appeared in {dir_count} targets (one-to-one constraint violated)"
        )

    def test_strong_overlap_pair_merges(self):
        """A DIR/GAP pair with strong lexical overlap (DIR-0004/GAP-08 pattern) DOES merge."""
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merged = merge_targets([gap], [direction])
        merged_ids = [t["target_id"] for t in merged]
        assert "GAP-08+DIR-0004" in merged_ids, (
            "Strong-overlap pair (DIR-0004/GAP-08) must produce a merged target"
        )

    def test_only_one_merged_per_multi_gap_scenario(self):
        """When one direction could theoretically overlap multiple gaps via topic alone,
        the greedy one-to-one algorithm assigns it to the best-scoring gap only."""
        # Three gaps all share T-0006 with the direction, but only GAP-08 has lexical match
        gap_a = self._make_gap08()  # strong lexical match + T-0006
        gap_b = {
            "id": "GAP-01",
            "title": "Iris Tetra optical roofline no published number",
            "missing": "compute bandwidth ratio FLOPs per byte determines attention design",
            "fillable_by": ["arxiv"],
            "topics": ["T-0006"],
            "priority": "high",
        }
        gap_c = {
            "id": "GAP-07",
            "title": "OptiSim open source status and fork plan",
            "missing": "GitHub URL or confirmation availability licensing",
            "fillable_by": ["web"],
            "topics": ["T-0006"],
            "priority": "medium",
        }
        direction = self._make_dir0004()
        merged = merge_targets([gap_a, gap_b, gap_c], [direction])
        # Count how many targets include DIR-0004 in their origin_ids
        dir_appearances = sum(1 for t in merged if "DIR-0004" in t.get("origin_ids", []))
        assert dir_appearances == 1, (
            f"DIR-0004 appeared in {dir_appearances} targets; one-to-one violated"
        )
        # The merge should be with GAP-08 (highest lexical score), not GAP-01 or GAP-07
        merged_target = next(t for t in merged if "DIR-0004" in t.get("origin_ids", []))
        assert "GAP-08" in merged_target["origin_ids"], (
            "Greedy should merge DIR-0004 with GAP-08 (best lexical match), not another gap"
        )


# ---------------------------------------------------------------------------
# assign_lanes tests
# ---------------------------------------------------------------------------


class TestAssignLanes:
    def _make_target(self, tid, lane, priority, fillable_by=None):
        return {
            "target_id": tid,
            "lane": lane,
            "origin_ids": [tid],
            "priority": priority,
            "queries": [],
            "expected_evidence": "",
            "fillable_by": fillable_by or [],
            "routed_sources": [],
            "_gap": None,
            "_dir": None,
        }

    def test_gap_targets_in_gap_lane(self):
        t1 = self._make_target("GAP-01", "gap", "high", ["arxiv"])
        t2 = self._make_target("GAP-02", "gap", "medium", [])
        lanes = assign_lanes([t1, t2])
        assert len(lanes["gap"]) == 2
        assert len(lanes["research"]) == 0

    def test_gap_lane_sorted_priority(self):
        t_med = self._make_target("GAP-08", "gap", "medium", ["arxiv"])
        t_high = self._make_target("GAP-02", "gap", "high", ["arxiv"])
        t_low = self._make_target("GAP-03", "gap", "low", [])
        lanes = assign_lanes([t_med, t_high, t_low])
        gap_ids = [t["target_id"] for t in lanes["gap"]]
        assert gap_ids[0] == "GAP-02"  # high first
        assert gap_ids[-1] == "GAP-03"  # low last

    def test_arxiv_fillable_prefers_first_in_same_priority(self):
        # Two medium-priority targets: one arxiv-fillable, one not
        t_noarxiv = self._make_target("GAP-A", "gap", "medium", [])
        t_arxiv = self._make_target("GAP-B", "gap", "medium", ["arxiv"])
        lanes = assign_lanes([t_noarxiv, t_arxiv])
        gap_ids = [t["target_id"] for t in lanes["gap"]]
        # arxiv-fillable should come first among same priority
        assert gap_ids[0] == "GAP-B"

    def test_research_lane_sorted_priority(self):
        t_high = self._make_target("DIR-0010", "research", "high")
        t_low = self._make_target("DIR-0011", "research", "low")
        lanes = assign_lanes([t_high, t_low])
        assert lanes["research"][0]["target_id"] == "DIR-0010"
        assert lanes["research"][1]["target_id"] == "DIR-0011"

    def test_standalone_direction_in_research_lane(self):
        t = self._make_target("DIR-0010", "research", "high")
        lanes = assign_lanes([t])
        assert len(lanes["research"]) == 1
        assert len(lanes["gap"]) == 0


# ---------------------------------------------------------------------------
# route_to_sources tests
# ---------------------------------------------------------------------------


class TestRouteToSources:
    def _make_target(self, fillable_by, topics=None, title=""):
        return {
            "target_id": "GAP-TEST",
            "lane": "gap",
            "origin_ids": ["GAP-TEST"],
            "priority": "medium",
            "queries": [],
            "expected_evidence": "",
            "fillable_by": fillable_by,
            "routed_sources": [],
            "_gap": {
                "id": "GAP-TEST",
                "title": title,
                "topics": topics or [],
                "fillable_by": fillable_by,
            },
            "_dir": None,
        }

    def test_arxiv_routes_to_paper_sources(self):
        target = self._make_target(["arxiv"])
        sources = route_to_sources(target, REGISTRY_FIXTURE)
        # Should include paper-publisher sources
        assert any(s in sources for s in ["arxiv_cs_ar", "semantic_scholar", "arxiv_cs_dc"])

    def test_arxiv_results_include_high_relevance_first(self):
        target = self._make_target(["arxiv"])
        sources = route_to_sources(target, REGISTRY_FIXTURE)
        # arxiv_eess_sp (relevance=2) should not come before higher-relevance sources
        if "arxiv_eess_sp" in sources:
            eess_idx = sources.index("arxiv_eess_sp")
            for high_s in ["arxiv_cs_ar", "semantic_scholar", "arxiv_cs_dc"]:
                if high_s in sources:
                    assert sources.index(high_s) < eess_idx

    def test_github_routes_to_repo_ids(self):
        target = self._make_target(["github"])
        sources = route_to_sources(target, REGISTRY_FIXTURE)
        assert any(s in sources for s in ["vllm", "tvm"])

    def test_web_routes_to_blog_ids(self):
        target = self._make_target(["web"])
        sources = route_to_sources(target, REGISTRY_FIXTURE)
        assert any(s in sources for s in [
            "nvidia_developer_blog", "semianalysis_newsletter",
            "inferencex_semianalysis", "vllm_blog", "the_batch_newsletter",
        ])

    def test_forum_returns_no_registry_ids(self):
        # forum/x handled by API engines, not registry
        target = self._make_target(["forum"])
        sources = route_to_sources(target, REGISTRY_FIXTURE)
        assert sources == []

    def test_x_returns_no_registry_ids(self):
        target = self._make_target(["x"])
        sources = route_to_sources(target, REGISTRY_FIXTURE)
        assert sources == []

    def test_results_capped_at_5(self):
        # arxiv + github + web would produce many; cap at 5
        target = self._make_target(["arxiv", "github", "web"])
        sources = route_to_sources(target, REGISTRY_FIXTURE)
        assert len(sources) <= 5

    def test_no_duplicates_in_results(self):
        target = self._make_target(["arxiv", "web"])
        sources = route_to_sources(target, REGISTRY_FIXTURE)
        assert len(sources) == len(set(sources))

    def test_empty_registry_returns_empty(self):
        target = self._make_target(["arxiv"])
        sources = route_to_sources(target, {})
        assert sources == []


# ---------------------------------------------------------------------------
# select_candidates tests
# ---------------------------------------------------------------------------


def _make_candidate(cid, lane, score, source_id=None, url=None):
    return {
        "id": cid,
        "source_id": source_id or cid,
        "url": url or f"https://example.com/{cid}",
        "title": f"Title for {cid}",
        "lane": lane,
        "score": float(score),
        "id_type": "url",
    }


class TestSelectCandidates:
    def test_quota_fill_exact_mix(self):
        # gap:3, research:1, news:1 => total 5
        pool = (
            [_make_candidate(f"g{i}", "gap", 10 - i) for i in range(5)]
            + [_make_candidate(f"r{i}", "research", 10 - i) for i in range(3)]
            + [_make_candidate(f"n{i}", "news", 10 - i) for i in range(2)]
        )
        result = select_candidates(pool, CONFIG_FIXTURE)
        assert len(result) == 5
        lane_counts = {}
        for c in result:
            lane_counts[c["lane"]] = lane_counts.get(c["lane"], 0) + 1
        assert lane_counts.get("gap", 0) == 3
        assert lane_counts.get("research", 0) == 1
        assert lane_counts.get("news", 0) == 1

    def test_spillover_gap_underfills(self):
        # gap has only 2 candidates (quota=3) => spare slot goes to research
        pool = (
            [_make_candidate(f"g{i}", "gap", 10 - i) for i in range(2)]  # only 2 gap
            + [_make_candidate(f"r{i}", "research", 10 - i) for i in range(4)]
            + [_make_candidate(f"n{i}", "news", 10 - i) for i in range(2)]
        )
        result = select_candidates(pool, CONFIG_FIXTURE)
        assert len(result) <= 5
        # At least 2 gap results
        gap_results = [c for c in result if c["lane"] == "gap"]
        assert len(gap_results) == 2
        # Total should not exceed cap
        assert len(result) <= CONFIG_FIXTURE["new_sources_total"]

    def test_spillover_total_still_capped(self):
        # All lanes have many candidates
        pool = (
            [_make_candidate(f"g{i}", "gap", 10 - i) for i in range(10)]
            + [_make_candidate(f"r{i}", "research", 10 - i) for i in range(10)]
            + [_make_candidate(f"n{i}", "news", 10 - i) for i in range(10)]
        )
        result = select_candidates(pool, CONFIG_FIXTURE)
        assert len(result) <= 5

    def test_ingest_dedup_ingested_dropped(self):
        pool = [_make_candidate("c1", "gap", 9, source_id="arxiv:1234.5678")]
        ingest_rows = [{"id": "arxiv:1234.5678", "status": "ingested", "url": ""}]
        result = select_candidates(pool, CONFIG_FIXTURE, ingest_rows=ingest_rows)
        assert all(c["source_id"] != "arxiv:1234.5678" for c in result)

    def test_ingest_dedup_pending_dropped(self):
        pool = [_make_candidate("c2", "gap", 9, source_id="arxiv:9999.9999")]
        ingest_rows = [{"id": "arxiv:9999.9999", "status": "pending", "url": ""}]
        result = select_candidates(pool, CONFIG_FIXTURE, ingest_rows=ingest_rows)
        assert all(c["source_id"] != "arxiv:9999.9999" for c in result)

    def test_ingest_dedup_rejected_sticky(self):
        # rejected = sticky, should also be dropped
        pool = [_make_candidate("c3", "gap", 9, source_id="url:https://example.com/c3")]
        ingest_rows = [{"id": "url:https://example.com/c3", "status": "rejected", "url": ""}]
        result = select_candidates(pool, CONFIG_FIXTURE, ingest_rows=ingest_rows)
        assert all(c["source_id"] != "url:https://example.com/c3" for c in result)

    def test_ingest_dedup_by_url(self):
        pool = [_make_candidate("c4", "gap", 9, url="https://example.com/paper")]
        ingest_rows = [{"id": "some_other_id", "status": "ingested", "url": "https://example.com/paper"}]
        result = select_candidates(pool, CONFIG_FIXTURE, ingest_rows=ingest_rows)
        assert all(c.get("url", "").rstrip("/") != "https://example.com/paper" for c in result)

    def test_waiting_approval_not_deduped(self):
        # waiting_approval should NOT drop the candidate (not in dedup statuses)
        pool = [_make_candidate("c5", "gap", 9, source_id="arxiv:1111.2222")]
        ingest_rows = [{"id": "arxiv:1111.2222", "status": "waiting_approval", "url": ""}]
        result = select_candidates(pool, CONFIG_FIXTURE, ingest_rows=ingest_rows)
        # Should still include this candidate since waiting_approval is not a dedup status
        # (only ingested/pending/rejected trigger dedup)
        assert any(c["source_id"] == "arxiv:1111.2222" for c in result)

    def test_cap_never_exceeds_new_sources_total(self):
        pool = [_make_candidate(f"x{i}", "gap", float(i)) for i in range(20)]
        result = select_candidates(pool, CONFIG_FIXTURE)
        assert len(result) <= CONFIG_FIXTURE["new_sources_total"]

    def test_sorted_by_score_desc_within_lane(self):
        pool = [
            _make_candidate("g_low", "gap", 1.0),
            _make_candidate("g_high", "gap", 9.0),
            _make_candidate("g_mid", "gap", 5.0),
        ]
        config = dict(CONFIG_FIXTURE)
        config["lanes"] = {"gap": {"quota": 3}, "research": {"quota": 0}, "news": {"quota": 0}}
        config["new_sources_total"] = 3
        result = select_candidates(pool, config)
        ids = [c["id"] for c in result]
        assert ids[0] == "g_high"
        assert ids[-1] == "g_low"

    def test_no_ingest_rows_no_dedup(self):
        pool = [_make_candidate("c1", "gap", 9)]
        result = select_candidates(pool, CONFIG_FIXTURE, ingest_rows=None)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# build_plan tests
# ---------------------------------------------------------------------------


class TestBuildPlan:
    def _make_vault(self, tmp_path, gaps_text=None, dirs=None):
        """Create a minimal vault directory with gaps.md and direction files."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "wiki").mkdir()
        (vault / "objective" / "direction").mkdir(parents=True)
        (vault / "meta").mkdir()

        if gaps_text is not None:
            (vault / "wiki" / "gaps.md").write_text(gaps_text, encoding="utf-8")

        for name, text in (dirs or {}).items():
            (vault / "objective" / "direction" / name).write_text(text, encoding="utf-8")

        return vault

    def test_returns_dict_with_targets_config_notes(self, tmp_path):
        vault = self._make_vault(tmp_path, gaps_text=GAPS_FIXTURE_TEXT)
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        assert "targets" in plan
        assert "config" in plan
        assert "notes" in plan

    def test_gap_targets_before_research_before_news(self, tmp_path):
        vault = self._make_vault(
            tmp_path,
            gaps_text=GAPS_FIXTURE_TEXT,
            dirs={"DIR-0010-standalone.md": DIR_STANDALONE},
        )
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        targets = plan["targets"]

        lanes_order = [t["lane"] for t in targets]
        # Find where gap, research, news sections start
        # Verify gap comes before research comes before news
        gap_indices = [i for i, lane in enumerate(lanes_order) if lane == "gap"]
        research_indices = [i for i, lane in enumerate(lanes_order) if lane == "research"]
        news_indices = [i for i, lane in enumerate(lanes_order) if lane == "news"]

        if gap_indices and research_indices:
            assert max(gap_indices) < min(research_indices)
        if research_indices and news_indices:
            assert max(research_indices) < min(news_indices)

    def test_news_target_always_present(self, tmp_path):
        vault = self._make_vault(tmp_path, gaps_text=GAPS_FIXTURE_TEXT)
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        news_targets = [t for t in plan["targets"] if t["lane"] == "news"]
        assert len(news_targets) == 1

    def test_news_target_has_routed_sources(self, tmp_path):
        vault = self._make_vault(tmp_path, gaps_text=GAPS_FIXTURE_TEXT)
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        news = next(t for t in plan["targets"] if t["lane"] == "news")
        assert len(news["routed_sources"]) > 0

    def test_no_internal_keys_in_output(self, tmp_path):
        vault = self._make_vault(tmp_path, gaps_text=GAPS_FIXTURE_TEXT)
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        for target in plan["targets"]:
            assert "_gap" not in target
            assert "_dir" not in target
            assert "_engine_tags" not in target

    def test_empty_vault_still_returns_plan(self, tmp_path):
        vault = self._make_vault(tmp_path)  # no gaps, no directions
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        assert "targets" in plan
        # Should have at least a news target
        news_targets = [t for t in plan["targets"] if t["lane"] == "news"]
        assert len(news_targets) == 1

    def test_target_shape_matches_spec(self, tmp_path):
        vault = self._make_vault(tmp_path, gaps_text=GAPS_FIXTURE_TEXT)
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)

        required_keys = {
            "target_id", "lane", "origin_ids", "priority",
            "queries", "expected_evidence", "fillable_by", "routed_sources",
        }
        for t in plan["targets"]:
            assert required_keys.issubset(t.keys()), f"Missing keys in target: {t}"

    def test_gap_targets_have_routed_sources(self, tmp_path):
        vault = self._make_vault(tmp_path, gaps_text=GAPS_FIXTURE_TEXT)
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        gap_targets = [t for t in plan["targets"] if t["lane"] == "gap"]
        # At least some gap targets should have routed sources (arxiv-fillable gaps)
        with_sources = [t for t in gap_targets if t["routed_sources"]]
        assert len(with_sources) >= 1

    def test_merged_gap_dir_present_in_output(self, tmp_path):
        # DIR-0004 shares topic T-0006 with GAP-08 in GAPS_FIXTURE_TEXT
        vault = self._make_vault(
            tmp_path,
            gaps_text=GAPS_FIXTURE_TEXT,
            dirs={"DIR-0004-optical.md": DIR_WITH_PLAIN_QUERIES},
        )
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        gap_targets = [t for t in plan["targets"] if t["lane"] == "gap"]
        merged = [t for t in gap_targets if "DIR-0004" in t.get("target_id", "")]
        # GAP-08 and DIR-0004 share T-0006 -> should be merged
        assert len(merged) == 1

    def test_notes_report_gap_and_direction_counts(self, tmp_path):
        vault = self._make_vault(tmp_path, gaps_text=GAPS_FIXTURE_TEXT)
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        notes_text = " ".join(plan["notes"])
        assert "gaps" in notes_text.lower() or "gap" in notes_text.lower()


# ---------------------------------------------------------------------------
# news_target tests
# ---------------------------------------------------------------------------


class TestNewsTarget:
    def test_returns_news_lane(self):
        t = news_target(REGISTRY_FIXTURE)
        assert t["lane"] == "news"
        assert t["target_id"] == "news"

    def test_has_web_fillable_by(self):
        t = news_target(REGISTRY_FIXTURE)
        assert "web" in t["fillable_by"]

    def test_routed_sources_from_news_categories(self):
        t = news_target(REGISTRY_FIXTURE)
        # Should route to vendor_blogs/benchmarking/hardware_analysis/specialist_research
        assert len(t["routed_sources"]) > 0
        # Should include high-relevance sources
        assert any(s in t["routed_sources"] for s in [
            "nvidia_developer_blog", "semianalysis_newsletter", "inferencex_semianalysis"
        ])

    def test_queries_empty(self):
        t = news_target(REGISTRY_FIXTURE)
        assert t["queries"] == []

    def test_expected_evidence_set(self):
        t = news_target(REGISTRY_FIXTURE)
        assert t["expected_evidence"] != ""
        assert "vendor" in t["expected_evidence"].lower() or "announcement" in t["expected_evidence"].lower()
