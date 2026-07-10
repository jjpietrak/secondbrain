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
    DecisionTrace,
    _clean_query_text,
    _derive_gap_queries,
    _parse_frontmatter,
    _parse_seed_queries,
    assign_lanes,
    build_plan,
    merge_targets,
    news_target,
    parse_directions,
    parse_gaps,
    render_trace_markdown,
    route_to_sources,
    select_candidates,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Per-gap file fixtures (new wiki/gap/GAP-NN-<slug>.md schema)
GAP_08_FILE = """\
---
type: gap
id: GAP-08
title: "Optical prior art -- citations [3]-[6] from Photons to Tokens not ingested"
status: open
topics: [T-0006]
concepts: ["[[wiki/concepts/optical-compute]]"]
fillable_by: [arxiv]
priority: medium
shows_up_in: ["[[wiki/sources/photons-to-tokens]]"]
created: 2026-06-22
updated: 2026-06-23
written_by: wiki
---
## Missing
entity/source pages for these four cited papers; they contain device parameters

## Why
The optical roofline range needs these citations.

## Open questions
- Are these all on arXiv?
"""

GAP_04_FILE = """\
---
type: gap
id: GAP-04
title: "LLM serving systems coverage is thin"
status: open
topics: [T-0003]
concepts: ["[[wiki/concepts/llm-serving]]", "[[wiki/concepts/pd-scheduling]]"]
fillable_by: [arxiv, web, github]
priority: high
shows_up_in: ["[[wiki/entities/splitwise]]"]
created: 2026-06-20
updated: 2026-06-23
written_by: wiki
---
## Missing
ingested source pages for seminal P/D systems (DistServe arXiv 2401.09670, Splitwise)

## Why
Vault has almost no content; gaps in P/D scheduling limit the simulator.

## Open questions
- Which papers are most canonical?
"""

GAP_FILLED_FILE = """\
---
type: gap
id: GAP-99
title: "Already resolved gap"
status: filled
topics: [T-0001]
fillable_by: [arxiv]
priority: low
shows_up_in: []
created: 2026-06-01
updated: 2026-06-23
written_by: wiki
---
## Missing
Nothing -- this gap is filled.
"""

# Helper: write per-gap files into a tmp wiki/gap/ directory
def _make_gap_dir(tmp_path, files: dict) -> Path:
    """Create wiki/gap/ with the given filename->content mapping. Returns the dir path."""
    gap_dir = tmp_path / "wiki" / "gap"
    gap_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (gap_dir / name).write_text(content, encoding="utf-8")
    return gap_dir

DIR_WITH_PLAIN_QUERIES = """\
---
type: direction
id: DIR-0004
created: 2026-06-22
updated: 2026-06-22
serves_question: Q-0005
related: ["[[wiki/concepts/optical-compute]]", "[[wiki/concepts/photonic-accelerator]]"]
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
    def test_parses_two_open_gaps(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {
            "GAP-08-optical-prior-art.md": GAP_08_FILE,
            "GAP-04-llm-serving.md": GAP_04_FILE,
        })
        gaps = parse_gaps(str(gap_dir))
        assert len(gaps) == 2

    def test_gap08_id_and_title(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {"GAP-08-optical-prior-art.md": GAP_08_FILE})
        gaps = parse_gaps(str(gap_dir))
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert g08["title"] == "Optical prior art -- citations [3]-[6] from Photons to Tokens not ingested"

    def test_gap08_single_concept(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {"GAP-08-optical-prior-art.md": GAP_08_FILE})
        gaps = parse_gaps(str(gap_dir))
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert g08["concepts"] == ["optical-compute"]

    def test_gap08_fillable_by_bare_tag(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {"GAP-08-optical-prior-art.md": GAP_08_FILE})
        gaps = parse_gaps(str(gap_dir))
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert g08["fillable_by"] == ["arxiv"]

    def test_gap08_priority_from_frontmatter(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {"GAP-08-optical-prior-art.md": GAP_08_FILE})
        gaps = parse_gaps(str(gap_dir))
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert g08["priority"] == "medium"

    def test_gap04_multi_concept(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {"GAP-04-llm-serving.md": GAP_04_FILE})
        gaps = parse_gaps(str(gap_dir))
        g04 = next(g for g in gaps if g["id"] == "GAP-04")
        assert "pd-scheduling" in g04["concepts"]
        assert "llm-serving" in g04["concepts"]

    def test_gap04_multi_tag_fillable_by(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {"GAP-04-llm-serving.md": GAP_04_FILE})
        gaps = parse_gaps(str(gap_dir))
        g04 = next(g for g in gaps if g["id"] == "GAP-04")
        tags = g04["fillable_by"]
        assert "arxiv" in tags
        assert "web" in tags
        assert "github" in tags

    def test_gap04_priority_high(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {"GAP-04-llm-serving.md": GAP_04_FILE})
        gaps = parse_gaps(str(gap_dir))
        g04 = next(g for g in gaps if g["id"] == "GAP-04")
        assert g04["priority"] == "high"

    def test_filled_status_excluded(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {
            "GAP-08-optical-prior-art.md": GAP_08_FILE,
            "GAP-99-resolved.md": GAP_FILLED_FILE,
        })
        gaps = parse_gaps(str(gap_dir))
        ids = [g["id"] for g in gaps]
        assert "GAP-99" not in ids
        assert "GAP-08" in ids

    def test_template_and_index_skipped(self, tmp_path):
        gap_dir = _make_gap_dir(tmp_path, {
            "GAP-08-optical-prior-art.md": GAP_08_FILE,
            "_template.md": "---\ntype: gap\nid: GAP-NNNN\nstatus: open\n---\n",
            "index.md": "# Gap index\n\nOverview.\n",
        })
        gaps = parse_gaps(str(gap_dir))
        # Only GAP-08 should be parsed; template and index skipped
        assert len(gaps) == 1
        assert gaps[0]["id"] == "GAP-08"

    def test_missing_dir_returns_empty(self):
        gaps = parse_gaps("/nonexistent/wiki/gap")
        assert gaps == []

    def test_missing_body_returns_empty_string(self, tmp_path):
        minimal = """\
---
type: gap
id: GAP-99
title: "Minimal gap"
status: open
topics: [T-0001]
fillable_by: [arxiv]
priority: low
shows_up_in: []
---
"""
        gap_dir = _make_gap_dir(tmp_path, {"GAP-99-minimal.md": minimal})
        gaps = parse_gaps(str(gap_dir))
        assert len(gaps) == 1
        assert gaps[0]["id"] == "GAP-99"
        assert gaps[0]["priority"] == "low"
        assert gaps[0]["missing"] == ""  # no ## Missing section

    def test_output_dict_shape(self, tmp_path):
        """Output dict shape must be: id, title, shows_up_in, missing, fillable_by, concepts, priority."""
        gap_dir = _make_gap_dir(tmp_path, {"GAP-08-optical-prior-art.md": GAP_08_FILE})
        gaps = parse_gaps(str(gap_dir))
        required_keys = {"id", "title", "shows_up_in", "missing", "fillable_by", "concepts", "priority"}
        assert required_keys.issubset(gaps[0].keys())

    def test_missing_as_body_text(self, tmp_path):
        """The 'missing' field is the ## Missing section body text."""
        gap_dir = _make_gap_dir(tmp_path, {"GAP-08-optical-prior-art.md": GAP_08_FILE})
        gaps = parse_gaps(str(gap_dir))
        g08 = next(g for g in gaps if g["id"] == "GAP-08")
        assert "entity/source pages" in g08["missing"]

    def test_trace_parse_gaps_record(self, tmp_path):
        """parse_gaps with trace emits a parse/gaps record."""
        from web_decision import DecisionTrace
        gap_dir = _make_gap_dir(tmp_path, {"GAP-08-optical-prior-art.md": GAP_08_FILE})
        t = DecisionTrace()
        gaps = parse_gaps(str(gap_dir), trace=t)
        rec = t.first("parse", "gaps")
        assert rec is not None
        assert rec["data"]["count"] == len(gaps)


# Real gap files (produced by the wiki agent) use YAML BLOCK lists, not inline
# lists -- e.g. GAP-11 in the live vault. The old _parse_frontmatter dropped
# these, so topics/fillable_by came back empty and the gap lane routed nothing.
GAP_BLOCK_LIST_FILE = """\
---
type: gap
id: GAP-11
title: Splitwise paper (arXiv 2311.18677) not ingested - KV transfer protocol and
  MoE coverage unknown
status: open
topics:
- T-0001
- T-0002
concepts:
- "[[wiki/concepts/kv-cache]]"
- "[[wiki/concepts/moe]]"
fillable_by:
- arxiv
priority: high
shows_up_in:
- thin [[wiki/entities/splitwise]]
- KV transfer protocol TBD
created: '2026-07-01'
updated: '2026-07-08'
written_by: wiki
---
## Missing
The Splitwise paper's KV transfer protocol and MoE coverage.
"""


class TestParseFrontmatterBlockList:
    """Fix 1: YAML block-list frontmatter must parse into list-shaped values."""

    def test_block_list_topics_parsed(self):
        fm = _parse_frontmatter(GAP_BLOCK_LIST_FILE)
        # Block list is normalised to a bracketed inline-list string
        assert fm["topics"] == "[T-0001, T-0002]"

    def test_block_list_fillable_by_parsed(self):
        fm = _parse_frontmatter(GAP_BLOCK_LIST_FILE)
        assert fm["fillable_by"] == "[arxiv]"

    def test_block_list_scalar_still_parses(self):
        fm = _parse_frontmatter(GAP_BLOCK_LIST_FILE)
        assert fm["status"] == "open"
        assert fm["priority"] == "high"

    def test_inline_list_still_parses(self):
        """The pre-existing inline-list form must keep working."""
        fm = _parse_frontmatter(GAP_08_FILE)
        assert fm["topics"] == "[T-0006]"
        assert fm["fillable_by"] == "[arxiv]"

    def test_empty_key_without_block_list_is_empty(self):
        text = "---\ntype: gap\ntopics:\nstatus: open\n---\n"
        fm = _parse_frontmatter(text)
        assert fm["topics"] == ""
        assert fm["status"] == "open"

    def test_parse_gaps_block_list_non_empty_routing_fields(self, tmp_path):
        """End-to-end: a real block-list gap file yields non-empty concepts + fillable_by."""
        gap_dir = _make_gap_dir(tmp_path, {"GAP-11-splitwise.md": GAP_BLOCK_LIST_FILE})
        gaps = parse_gaps(str(gap_dir))
        g11 = next(g for g in gaps if g["id"] == "GAP-11")
        assert g11["concepts"] == ["kv-cache", "moe"]
        assert g11["fillable_by"] == ["arxiv"]


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

    def test_concepts_parsed_from_frontmatter(self, tmp_path):
        d = tmp_path / "direction"
        d.mkdir()
        (d / "DIR-0004-optical.md").write_text(DIR_WITH_PLAIN_QUERIES, encoding="utf-8")
        dirs = parse_directions(str(d))
        assert "optical-compute" in dirs[0]["concepts"]
        assert "photonic-accelerator" in dirs[0]["concepts"]

    def test_expected_evidence_captured(self, tmp_path):
        d = tmp_path / "direction"
        d.mkdir()
        (d / "DIR-0004-optical.md").write_text(DIR_WITH_PLAIN_QUERIES, encoding="utf-8")
        dirs = parse_directions(str(d))
        assert "comparative table" in dirs[0]["expected_evidence"].lower()

    def test_nonexistent_dir_returns_empty(self):
        dirs = parse_directions("/nonexistent/path/direction")
        assert dirs == []


class TestSeedQueryCleaning:
    """Follow-up B: DIR seed_queries are passed through _clean_query_text."""

    def test_wikilinks_stripped_from_plain_query(self):
        body = (
            "## seed_queries\n"
            "- prefill decode split in [[wiki/concepts/kv-cache|KV cache]] serving\n"
        )
        queries = _parse_seed_queries(body)
        assert len(queries) == 1
        assert queries[0]["engine"] is None
        assert queries[0]["query"] == "prefill decode split in KV cache serving"
        assert "[[" not in queries[0]["query"]

    def test_engine_tag_preserved_and_query_cleaned(self):
        body = (
            "## seed_queries\n"
            "- arxiv: disaggregated serving [[wiki/concepts/prefill|prefill]] latency\n"
        )
        queries = _parse_seed_queries(body)
        assert len(queries) == 1
        assert queries[0]["engine"] == "arxiv"
        assert queries[0]["query"] == "disaggregated serving prefill latency"

    def test_query_that_cleans_to_empty_is_dropped(self):
        # A line that is pure markdown emphasis cleans to "" and must be dropped.
        body = "## seed_queries\n- ***\n- real disaggregation query\n"
        queries = _parse_seed_queries(body)
        assert len(queries) == 1
        assert queries[0]["query"] == "real disaggregation query"


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
        # rejected = sticky, should also be dropped.
        # The ingest row id is the candidate_ident (per-item URL), not source_id.
        pool = [_make_candidate("c3", "gap", 9, source_id="url:https://example.com/c3")]
        # candidate_ident returns the url ("https://example.com/c3"), so the row
        # id must match that per-item ident for the dedup to fire.
        ingest_rows = [{"id": "https://example.com/c3", "status": "rejected", "url": ""}]
        result = select_candidates(pool, CONFIG_FIXTURE, ingest_rows=ingest_rows)
        assert all(c.get("url", "").rstrip("/") != "https://example.com/c3" for c in result)

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

    # ------------------------------------------------------------------
    # New: intra-pool dedup via candidate_ident
    # ------------------------------------------------------------------

    def test_intra_pool_dedup_collapses_same_ident_keeps_higher_score(self):
        """Two candidates with the same candidate_ident -> only the higher-score one survives."""
        # Both point to the same URL (same per-item identity)
        url = "https://arxiv.org/abs/2401.12345"
        c_low = {
            "id": "dup_low",
            "source_id": "arxiv:2401.12345",
            "url": url,
            "title": "Paper (low score copy)",
            "lane": "gap",
            "score": 3.0,
            "id_type": "arxiv",
        }
        c_high = {
            "id": "dup_high",
            "source_id": "arxiv:2401.12345",
            "url": url,
            "title": "Paper (high score copy)",
            "lane": "gap",
            "score": 9.0,
            "id_type": "arxiv",
        }
        config = dict(CONFIG_FIXTURE)
        config["lanes"] = {"gap": {"quota": 3}, "research": {"quota": 0}, "news": {"quota": 0}}
        config["new_sources_total"] = 3
        result = select_candidates([c_low, c_high], config)
        # Must collapse to exactly 1 candidate
        assert len(result) == 1
        assert result[0]["score"] == 9.0

    def test_ingest_dedup_by_candidate_ident_not_source_id(self):
        """A candidate is dropped when its candidate_ident matches an ingest row's id
        (the new per-item key), even if source_id differs from the row id."""
        # RSS candidate: source_id is the feed id, url is per-item
        c = {
            "id": "blog_post",
            "source_id": "nvidia_developer_blog",  # feed-level id
            "url": "https://developer.nvidia.com/blog/some-post",
            "title": "Some post",
            "lane": "news",
            "score": 0.7,
            "id_type": "url",
        }
        # Ingest row keyed by candidate_ident (the per-item URL)
        ingest_rows = [
            {"id": "https://developer.nvidia.com/blog/some-post", "status": "ingested", "url": ""}
        ]
        config = dict(CONFIG_FIXTURE)
        config["lanes"] = {"gap": {"quota": 0}, "research": {"quota": 0}, "news": {"quota": 1}}
        config["new_sources_total"] = 1
        result = select_candidates([c], config, ingest_rows=ingest_rows)
        assert result == [], "Candidate whose per-item URL is ingested must be dropped"

    def test_same_feed_different_url_rss_candidates_both_retained(self):
        """Two RSS posts from the same feed (same source_id) but different URLs must
        BOTH survive intra-pool dedup (they are distinct per-item identities)."""
        base = {
            "id_type": "url",
            "published": "2026-06-01",
            "snippet": "snippet",
            "engine": "rss",
        }
        c1 = dict(base, id="hf_1", source_id="huggingface_blog",
                   url="https://huggingface.co/blog/post-alpha",
                   title="HF Post Alpha", lane="news", score=0.8)
        c2 = dict(base, id="hf_2", source_id="huggingface_blog",
                   url="https://huggingface.co/blog/post-beta",
                   title="HF Post Beta", lane="news", score=0.7)
        config = dict(CONFIG_FIXTURE)
        config["lanes"] = {"gap": {"quota": 0}, "research": {"quota": 0}, "news": {"quota": 2}}
        config["new_sources_total"] = 2
        result = select_candidates([c1, c2], config)
        assert len(result) == 2, (
            "Two posts from the same feed with different URLs must both be retained"
        )


# ---------------------------------------------------------------------------
# build_plan tests
# ---------------------------------------------------------------------------


class TestBuildPlan:
    def _make_vault(self, tmp_path, gap_files=None, dirs=None):
        """Create a minimal vault directory with wiki/gap/ files and direction files."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "objective" / "direction").mkdir(parents=True)
        (vault / "meta").mkdir()

        if gap_files is not None:
            gap_dir = vault / "wiki" / "gap"
            gap_dir.mkdir(parents=True)
            for name, text in gap_files.items():
                (gap_dir / name).write_text(text, encoding="utf-8")
        else:
            # Always create wiki/ even without gaps
            (vault / "wiki").mkdir(exist_ok=True)

        for name, text in (dirs or {}).items():
            (vault / "objective" / "direction" / name).write_text(text, encoding="utf-8")

        return vault

    def test_returns_dict_with_targets_config_notes(self, tmp_path):
        vault = self._make_vault(tmp_path, gap_files={
            "GAP-08-optical.md": GAP_08_FILE,
            "GAP-04-llm-serving.md": GAP_04_FILE,
        })
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        assert "targets" in plan
        assert "config" in plan
        assert "notes" in plan

    def test_gap_targets_before_research_before_news(self, tmp_path):
        vault = self._make_vault(
            tmp_path,
            gap_files={
                "GAP-08-optical.md": GAP_08_FILE,
                "GAP-04-llm-serving.md": GAP_04_FILE,
            },
            dirs={"DIR-0010-standalone.md": DIR_STANDALONE},
        )
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        targets = plan["targets"]

        lanes_order = [t["lane"] for t in targets]
        gap_indices = [i for i, lane in enumerate(lanes_order) if lane == "gap"]
        research_indices = [i for i, lane in enumerate(lanes_order) if lane == "research"]
        news_indices = [i for i, lane in enumerate(lanes_order) if lane == "news"]

        if gap_indices and research_indices:
            assert max(gap_indices) < min(research_indices)
        if research_indices and news_indices:
            assert max(research_indices) < min(news_indices)

    def test_news_target_always_present(self, tmp_path):
        vault = self._make_vault(tmp_path, gap_files={"GAP-08-optical.md": GAP_08_FILE})
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        news_targets = [t for t in plan["targets"] if t["lane"] == "news"]
        assert len(news_targets) == 1

    def test_news_target_has_routed_sources(self, tmp_path):
        vault = self._make_vault(tmp_path, gap_files={"GAP-08-optical.md": GAP_08_FILE})
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        news = next(t for t in plan["targets"] if t["lane"] == "news")
        assert len(news["routed_sources"]) > 0

    def test_no_internal_keys_in_output(self, tmp_path):
        vault = self._make_vault(tmp_path, gap_files={"GAP-08-optical.md": GAP_08_FILE})
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
        vault = self._make_vault(tmp_path, gap_files={
            "GAP-08-optical.md": GAP_08_FILE,
            "GAP-04-llm-serving.md": GAP_04_FILE,
        })
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)

        required_keys = {
            "target_id", "lane", "origin_ids", "priority",
            "queries", "expected_evidence", "fillable_by", "routed_sources",
        }
        for t in plan["targets"]:
            assert required_keys.issubset(t.keys()), f"Missing keys in target: {t}"

    def test_gap_targets_have_routed_sources(self, tmp_path):
        vault = self._make_vault(tmp_path, gap_files={
            "GAP-08-optical.md": GAP_08_FILE,
            "GAP-04-llm-serving.md": GAP_04_FILE,
        })
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        gap_targets = [t for t in plan["targets"] if t["lane"] == "gap"]
        with_sources = [t for t in gap_targets if t["routed_sources"]]
        assert len(with_sources) >= 1

    def test_merged_gap_dir_present_in_output(self, tmp_path):
        # DIR-0004 shares topic T-0006 with GAP-08
        vault = self._make_vault(
            tmp_path,
            gap_files={"GAP-08-optical.md": GAP_08_FILE},
            dirs={"DIR-0004-optical.md": DIR_WITH_PLAIN_QUERIES},
        )
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        gap_targets = [t for t in plan["targets"] if t["lane"] == "gap"]
        merged = [t for t in gap_targets if "DIR-0004" in t.get("target_id", "")]
        assert len(merged) == 1

    def test_notes_report_gap_dir(self, tmp_path):
        vault = self._make_vault(tmp_path, gap_files={"GAP-08-optical.md": GAP_08_FILE})
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        notes_text = " ".join(plan["notes"])
        assert "wiki/gap/" in notes_text

    def test_missing_gap_dir_produces_note(self, tmp_path):
        vault = self._make_vault(tmp_path)  # no wiki/gap/ dir
        plan = build_plan(str(vault), CONFIG_FIXTURE, REGISTRY_FIXTURE)
        notes_text = " ".join(plan["notes"])
        assert "wiki/gap/ not found" in notes_text


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

    def test_category_weights_rank_semianalysis_above_nvidia(self):
        """Phase 1: category_weights demote NVIDIA vendor_blogs below SemiAnalysis feeds."""
        cw = {
            "vendor_blogs": 0.5,
            "hardware_analysis": 1.2,
            "benchmarking": 1.15,
            "specialist_research": 1.2,
            "_default": 1.0,
        }
        routed = news_target(REGISTRY_FIXTURE, category_weights=cw)["routed_sources"]
        assert "semianalysis_newsletter" in routed and "inferencex_semianalysis" in routed
        # Both SemiAnalysis feeds must out-sort the NVIDIA vendor_blogs feed.
        assert routed.index("semianalysis_newsletter") < routed.index("nvidia_developer_blog")
        assert routed.index("inferencex_semianalysis") < routed.index("nvidia_developer_blog")

    def test_no_weights_preserves_relevance_order(self):
        """Back-compat: category_weights=None keeps plain relevance ordering."""
        routed_default = news_target(REGISTRY_FIXTURE)["routed_sources"]
        routed_none = news_target(REGISTRY_FIXTURE, category_weights=None)["routed_sources"]
        assert routed_default == routed_none


# ---------------------------------------------------------------------------
# Deterministic gap query derivation (Phase 1)
# ---------------------------------------------------------------------------


class TestDeriveGapQueries:
    def test_clean_strips_wikilinks_and_citations(self):
        raw = "Missing [[wiki/concepts/kv-cache|KV cache]] evidence from [3]-[6] and **bold**"
        cleaned = _clean_query_text(raw)
        assert "[[" not in cleaned and "]]" not in cleaned
        assert "[3]" not in cleaned and "[6]" not in cleaned
        assert "**" not in cleaned
        assert "KV cache" in cleaned  # alias survives

    def test_markdown_link_reduced_to_text(self):
        assert _clean_query_text("see [Splitwise](https://arxiv.org/abs/2311.18677)") \
            == "see Splitwise"

    def test_gap_queries_are_clean_phrases(self):
        gap = {
            "title": "Optical interconnect prior art",
            "missing": "No entity pages for [[wiki/sources/photons-to-tokens]] "
                       "or the cited papers [3]-[6]; also needs benchmarks.",
        }
        qs = _derive_gap_queries(gap)
        assert qs[0] == "Optical interconnect prior art"
        # First sentence only, cleaned of wikilink + citation noise.
        assert "[[" not in qs[1] and "[3]" not in qs[1]
        assert "photons-to-tokens" in qs[1]
        assert ";" not in qs[1]  # split on sentence boundary

    def test_empty_gap_yields_no_queries(self):
        assert _derive_gap_queries({"title": "", "missing": ""}) == []


# ---------------------------------------------------------------------------
# DecisionTrace tests
# ---------------------------------------------------------------------------


class TestDecisionTrace:
    """Verify that DecisionTrace records are emitted correctly, that the renderer
    produces expected sections, and that zero-overhead (trace=None) works."""

    # ------------------------------------------------------------------ helpers
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

    def _make_weak_dir(self):
        """A direction with topic-only overlap (no lexical match) -> below-threshold."""
        return {
            "id": "DIR-0010",
            "serves_question": ["Q-0010"],
            "topics": ["T-0099"],  # no shared topic with GAP-08
            "targets_gap": "Some standalone research direction not matching any gap at all",
            "priority": "high",
            "status": "open",
            "seed_queries": [
                {"engine": None, "query": "standalone research query"},
            ],
            "expected_evidence": "Some evidence",
            "solves_when": "Done",
            "_engine_tags": [],
        }

    # ------------------------------------------------------------------ merge_targets trace
    def test_merge_trace_emits_scores_record(self):
        t = DecisionTrace()
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merge_targets([gap], [direction], trace=t)
        rec = t.first("merge", "scores")
        assert rec is not None, "merge/scores record must be emitted"

    def test_merge_trace_strong_pair_decision_is_merged(self):
        t = DecisionTrace()
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merge_targets([gap], [direction], trace=t)
        rec = t.first("merge", "scores")
        pairs = rec["data"]["pairs"]
        # Find the GAP-08 / DIR-0004 pair
        strong = next(
            (p for p in pairs if p["gap"] == "GAP-08" and p["direction"] == "DIR-0004"),
            None,
        )
        assert strong is not None, "GAP-08/DIR-0004 pair must appear in trace"
        assert strong["decision"] == "MERGED", f"expected MERGED, got {strong['decision']}"
        from web_decision import MERGE_THRESHOLD
        assert strong["total"] >= MERGE_THRESHOLD, (
            f"total={strong['total']} should be >= MERGE_THRESHOLD={MERGE_THRESHOLD}"
        )

    def test_merge_trace_weak_pair_decision_is_below_threshold(self):
        t = DecisionTrace()
        gap = self._make_gap08()
        weak_dir = self._make_weak_dir()
        merge_targets([gap], [weak_dir], trace=t)
        rec = t.first("merge", "scores")
        pairs = rec["data"]["pairs"]
        weak = next(
            (p for p in pairs if p["direction"] == "DIR-0010"),
            None,
        )
        assert weak is not None, "DIR-0010 pair must appear in trace"
        assert weak["decision"] == "below-threshold", (
            f"expected below-threshold, got {weak['decision']}"
        )

    def test_merge_trace_two_dirs_records_consumed_decision(self):
        """When two directions both score above threshold vs the same gap, the
        second (lower-scoring) one should be direction-consumed or gap-consumed."""
        t = DecisionTrace()
        gap = self._make_gap08()
        dir1 = self._make_dir0004()
        # A second direction that ALSO has strong lexical overlap + shared topic
        dir2 = {
            "id": "DIR-0009",
            "serves_question": ["Q-0009"],
            "topics": ["T-0006"],
            "targets_gap": "Optical prior-art papers cited by Photons-to-Tokens not yet ingested in entity pages",
            "priority": "medium",
            "status": "open",
            "seed_queries": [{"engine": None, "query": "optical prior art ingested entity"}],
            "expected_evidence": "Evidence",
            "solves_when": "Done",
            "_engine_tags": ["arxiv"],
        }
        merge_targets([gap], [dir1, dir2], trace=t)
        rec = t.first("merge", "scores")
        pairs = rec["data"]["pairs"]
        decisions = {p["direction"]: p["decision"] for p in pairs}
        # One of the two should be MERGED; the other should be consumed
        consumed_decisions = {"gap-consumed", "direction-consumed", "below-threshold"}
        assert decisions.get("DIR-0004") == "MERGED" or decisions.get("DIR-0009") == "MERGED", (
            "At least one dir should be MERGED"
        )
        # The other must NOT also be MERGED
        if decisions.get("DIR-0004") == "MERGED":
            assert decisions.get("DIR-0009") in consumed_decisions, (
                f"DIR-0009 should be consumed, got {decisions.get('DIR-0009')}"
            )
        else:
            assert decisions.get("DIR-0004") in consumed_decisions, (
                f"DIR-0004 should be consumed, got {decisions.get('DIR-0004')}"
            )

    def test_merge_trace_threshold_and_merged_fields(self):
        t = DecisionTrace()
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merge_targets([gap], [direction], trace=t)
        rec = t.first("merge", "scores")
        from web_decision import MERGE_THRESHOLD
        assert rec["data"]["threshold"] == MERGE_THRESHOLD
        merged_list = rec["data"]["merged"]
        assert len(merged_list) == 1
        assert merged_list[0]["target_id"] == "GAP-08+DIR-0004"
        assert rec["data"]["unmatched_gaps"] == []
        assert rec["data"]["unmatched_directions"] == []

    def test_merge_trace_no_trace_when_none(self):
        """Calling merge_targets without trace leaves no side effects."""
        gap = self._make_gap08()
        direction = self._make_dir0004()
        # Should not raise; trace=None is the default
        result = merge_targets([gap], [direction])
        assert len(result) == 1  # existing behavior unchanged

    # ------------------------------------------------------------------ route_to_sources trace
    def test_route_trace_emits_target_record(self):
        t = DecisionTrace()
        target = {
            "target_id": "GAP-08",
            "lane": "gap",
            "origin_ids": ["GAP-08"],
            "priority": "medium",
            "queries": [],
            "expected_evidence": "",
            "fillable_by": ["arxiv"],
            "routed_sources": [],
            "_gap": {
                "id": "GAP-08",
                "title": "Optical prior art not ingested",
                "topics": ["T-0006"],
                "fillable_by": ["arxiv"],
            },
            "_dir": None,
        }
        route_to_sources(target, REGISTRY_FIXTURE, trace=t)
        recs = t.find("route", "target")
        assert len(recs) == 1
        rec = recs[0]
        assert rec["data"]["target_id"] == "GAP-08"
        assert rec["data"]["lane"] == "gap"

    def test_route_trace_considered_has_chosen_flags(self):
        t = DecisionTrace()
        target = {
            "target_id": "GAP-08",
            "lane": "gap",
            "origin_ids": ["GAP-08"],
            "priority": "medium",
            "queries": [],
            "expected_evidence": "",
            "fillable_by": ["arxiv"],
            "routed_sources": [],
            "_gap": {
                "id": "GAP-08",
                "title": "Optical prior art not ingested",
                "topics": ["T-0006"],
                "fillable_by": ["arxiv"],
            },
            "_dir": None,
        }
        route_to_sources(target, REGISTRY_FIXTURE, trace=t)
        rec = t.find("route", "target")[0]
        considered = rec["data"]["considered"]
        assert len(considered) > 0
        chosen_sources = [s for s in considered if s["chosen"]]
        unchosen_sources = [s for s in considered if not s["chosen"]]
        # arxiv has 4 sources (3 high-relevance + 1 low); cap is 5 so all might be chosen
        # but we need at least some chosen
        assert len(chosen_sources) > 0
        # Check that all fields are present
        for s in considered:
            assert "source_id" in s
            assert "category" in s
            assert "relevance" in s
            assert "focus_score" in s
            assert "chosen" in s
            assert "reason" in s

    def test_route_trace_none_has_no_effect(self):
        """route_to_sources without trace should behave identically to before."""
        target = {
            "target_id": "GAP-08",
            "lane": "gap",
            "origin_ids": ["GAP-08"],
            "priority": "medium",
            "queries": [],
            "expected_evidence": "",
            "fillable_by": ["arxiv"],
            "routed_sources": [],
            "_gap": {
                "id": "GAP-08",
                "title": "Optical prior art not ingested",
                "topics": ["T-0006"],
                "fillable_by": ["arxiv"],
            },
            "_dir": None,
        }
        result_no_trace = route_to_sources(target, REGISTRY_FIXTURE)
        result_with_none = route_to_sources(target, REGISTRY_FIXTURE, trace=None)
        assert result_no_trace == result_with_none

    # ------------------------------------------------------------------ select_candidates trace
    def test_select_trace_emits_per_lane_records(self):
        t = DecisionTrace()
        pool = (
            [_make_candidate(f"g{i}", "gap", 10 - i) for i in range(5)]
            + [_make_candidate(f"r{i}", "research", 10 - i) for i in range(3)]
            + [_make_candidate(f"n{i}", "news", 10 - i) for i in range(2)]
        )
        select_candidates(pool, CONFIG_FIXTURE, trace=t)
        lane_recs = t.find("select", "lane")
        lane_names = [r["data"]["lane"] for r in lane_recs]
        # All three spillover_order lanes should have a record
        assert "gap" in lane_names
        assert "research" in lane_names
        assert "news" in lane_names

    def test_select_trace_over_quota_reason(self):
        t = DecisionTrace()
        # 5 gap candidates but quota=3 -> 2 should be over-quota
        pool = [_make_candidate(f"g{i}", "gap", 10 - i) for i in range(5)]
        select_candidates(pool, CONFIG_FIXTURE, trace=t)
        gap_rec = next(r for r in t.find("select", "lane") if r["data"]["lane"] == "gap")
        rejected = gap_rec["data"]["rejected"]
        assert len(rejected) >= 1
        for rej in rejected:
            assert rej["reason"] == "over-quota", f"Expected over-quota, got {rej['reason']}"

    def test_select_trace_ingest_dedup_reason(self):
        """A candidate matching an ingest row should be annotated with ingest-dedup:<status>."""
        t = DecisionTrace()
        # g1 has source_id starting with "arxiv:" so _candidate_ident returns "arxiv:1234.5678"
        # (the fallback in _candidate_ident checks for arxiv:/doi: prefix on source_id).
        pool = [
            _make_candidate("g0", "gap", 9.0),
            _make_candidate("g1", "gap", 8.0, source_id="arxiv:1234.5678"),
        ]
        # Match the ident that _candidate_ident actually returns: "arxiv:1234.5678"
        ingest_rows = [{"id": "arxiv:1234.5678", "status": "ingested", "url": ""}]
        select_candidates(pool, CONFIG_FIXTURE, ingest_rows=ingest_rows, trace=t)
        dedup_rec = t.first("select", "dedup")
        assert dedup_rec is not None
        ingest_dropped = dedup_rec["data"]["ingest_dropped"]
        assert len(ingest_dropped) >= 1
        dropped_idents = [d["ident"] for d in ingest_dropped]
        assert "arxiv:1234.5678" in dropped_idents
        # Check status recorded
        dropped_item = next(d for d in ingest_dropped if d["ident"] == "arxiv:1234.5678")
        assert dropped_item["status"] == "ingested"

    def test_select_trace_ingest_dedup_reason_in_rejected(self):
        """After select_candidates, the lane record's rejected list includes ingest-dedup entries."""
        t = DecisionTrace()
        pool = [
            _make_candidate("g0", "gap", 9.0),
            _make_candidate("g1", "gap", 8.0),
        ]
        ingest_rows = [{"id": "https://example.com/g1", "status": "rejected", "url": ""}]
        select_candidates(pool, CONFIG_FIXTURE, ingest_rows=ingest_rows, trace=t)
        gap_rec = next(r for r in t.find("select", "lane") if r["data"]["lane"] == "gap")
        rejected = gap_rec["data"]["rejected"]
        ingest_reasons = [r for r in rejected if r["reason"].startswith("ingest-dedup:")]
        assert len(ingest_reasons) >= 1
        assert ingest_reasons[0]["reason"] == "ingest-dedup:rejected"

    def test_select_trace_final_record(self):
        t = DecisionTrace()
        pool = [_make_candidate(f"g{i}", "gap", 10.0 - i) for i in range(3)]
        select_candidates(pool, CONFIG_FIXTURE, trace=t)
        final_rec = t.first("select", "final")
        assert final_rec is not None
        selected = final_rec["data"]["selected"]
        assert len(selected) >= 1
        for item in selected:
            assert "ident" in item
            assert "lane" in item
            assert "score" in item

    def test_select_trace_none_unchanged_behavior(self):
        """select_candidates with no trace returns the same result as with trace=None."""
        pool = [_make_candidate(f"g{i}", "gap", 10.0 - i) for i in range(3)]
        result_default = select_candidates(pool, CONFIG_FIXTURE)
        result_none = select_candidates(pool, CONFIG_FIXTURE, trace=None)
        assert [c["id"] for c in result_default] == [c["id"] for c in result_none]

    # ------------------------------------------------------------------ render_trace_markdown
    def test_render_trace_contains_merge_table_header(self):
        t = DecisionTrace()
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merge_targets([gap], [direction], trace=t)
        md = render_trace_markdown(t)
        assert "## Merge scoring" in md
        assert "| gap | direction | jaccard | concept_bonus | total | decision |" in md

    def test_render_trace_contains_selection_section(self):
        t = DecisionTrace()
        pool = (
            [_make_candidate(f"g{i}", "gap", 10 - i) for i in range(4)]
            + [_make_candidate(f"r{i}", "research", 10 - i) for i in range(2)]
            + [_make_candidate(f"n{i}", "news", 10 - i) for i in range(2)]
        )
        select_candidates(pool, CONFIG_FIXTURE, trace=t)
        md = render_trace_markdown(t)
        assert "## Selection" in md
        assert "### Lane: gap" in md

    def test_render_trace_merged_target_appears_in_output(self):
        t = DecisionTrace()
        gap = self._make_gap08()
        direction = self._make_dir0004()
        merge_targets([gap], [direction], trace=t)
        md = render_trace_markdown(t)
        # The merged target id should appear somewhere
        assert "GAP-08+DIR-0004" in md

    def test_render_trace_idents_appear_in_selection(self):
        t = DecisionTrace()
        pool = [_make_candidate("g0", "gap", 9.0)]
        select_candidates(pool, CONFIG_FIXTURE, trace=t)
        md = render_trace_markdown(t)
        # The ident for g0 should be its URL (id_type=url in _make_candidate)
        assert "https://example.com/g0" in md

    def test_render_trace_returns_string(self):
        t = DecisionTrace()
        md = render_trace_markdown(t)
        assert isinstance(md, str)
        # Should always have the header
        assert "# Web Decision Trace" in md

    def test_render_trace_routing_section(self):
        """When route_to_sources is called with a trace, the Routing section appears."""
        t = DecisionTrace()
        target = {
            "target_id": "GAP-08",
            "lane": "gap",
            "origin_ids": ["GAP-08"],
            "priority": "medium",
            "queries": [],
            "expected_evidence": "",
            "fillable_by": ["arxiv"],
            "routed_sources": [],
            "_gap": {
                "id": "GAP-08",
                "title": "Optical prior art",
                "topics": ["T-0006"],
                "fillable_by": ["arxiv"],
            },
            "_dir": None,
        }
        route_to_sources(target, REGISTRY_FIXTURE, trace=t)
        md = render_trace_markdown(t)
        assert "## Routing" in md
        assert "GAP-08" in md

    # ------------------------------------------------------------------ zero-overhead
    def test_zero_overhead_merge_targets(self):
        """merge_targets(trace=None) is default and must not raise or change results."""
        gap = self._make_gap08()
        direction = self._make_dir0004()
        result = merge_targets([gap], [direction])
        assert result[0]["target_id"] == "GAP-08+DIR-0004"

    def test_zero_overhead_select_candidates(self):
        """select_candidates(trace=None) behaves identically to calling without trace."""
        pool = [_make_candidate(f"g{i}", "gap", 10.0 - i) for i in range(3)]
        r1 = select_candidates(pool, CONFIG_FIXTURE)
        r2 = select_candidates(pool, CONFIG_FIXTURE, trace=None)
        assert len(r1) == len(r2)
        for a, b in zip(r1, r2):
            assert a["id"] == b["id"]

    def test_zero_overhead_route_to_sources(self):
        """route_to_sources(trace=None) returns same list as without trace."""
        target = {
            "target_id": "T",
            "lane": "gap",
            "origin_ids": ["T"],
            "priority": "medium",
            "queries": [],
            "expected_evidence": "",
            "fillable_by": ["arxiv"],
            "routed_sources": [],
            "_gap": {"id": "T", "title": "test", "topics": [], "fillable_by": ["arxiv"]},
            "_dir": None,
        }
        r1 = route_to_sources(target, REGISTRY_FIXTURE)
        r2 = route_to_sources(target, REGISTRY_FIXTURE, trace=None)
        assert r1 == r2


# ===========================================================================
# Tests: render_trace_markdown covers harvest/target and rank/scores records
# ===========================================================================

class TestRenderHarvestAndRank:
    """render_trace_markdown renders harvest/target and rank/scores records."""

    def _make_trace_with_harvest_and_rank(self) -> "DecisionTrace":
        """Build a DecisionTrace populated with harvest/target and rank/scores records."""
        t = DecisionTrace()

        # Two harvest/target records with per-engine query detail
        t.add(
            "harvest",
            "target",
            target_id="GAP-01+DIR-0001",
            lane="gap",
            queries=[
                {"engine": "arxiv", "query": "disaggregated LLM inference", "n_returned": 4},
                {"engine": "hackernews", "query": "disaggregated serving", "n_returned": 2},
            ],
            n_candidates=6,
            seen_dropped=1,
        )
        t.add(
            "harvest",
            "target",
            target_id="news",
            lane="news",
            queries=[
                {"engine": "rss", "query": "https://developer.nvidia.com/blog/feed", "n_returned": 3},
                {"engine": "rss", "query": "https://semianalysis.substack.com/feed", "n_returned": 0,
                 "error": "HTTP 503"},
            ],
            n_candidates=3,
            seen_dropped=0,
        )

        # rank/scores record
        t.add(
            "rank",
            "scores",
            path="fallback",
            candidates=[
                {"ident": "arxiv:2401.10001", "score": 0.85},
                {"ident": "arxiv:2401.10002", "score": 0.72},
                {"ident": "https://news.ycombinator.com/item?id=12345", "score": 0.55},
            ],
            n=3,
        )

        return t

    def test_harvest_section_header_present(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        assert "## Harvest" in md

    def test_harvest_query_table_engine_column(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        assert "arxiv" in md
        assert "hackernews" in md
        assert "rss" in md

    def test_harvest_query_table_n_returned(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        # n_returned values from the fixture
        assert "| 4 |" in md
        assert "| 2 |" in md
        assert "| 3 |" in md

    def test_harvest_error_appears_in_table(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        assert "HTTP 503" in md

    def test_harvest_per_target_summary_present(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        assert "Per-target summary" in md
        assert "GAP-01+DIR-0001" in md
        assert "news" in md

    def test_harvest_seen_dropped_in_summary(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        # seen_dropped=1 for GAP-01+DIR-0001, seen_dropped=0 for news
        assert "| 1 |" in md

    def test_rank_section_header_present(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        assert "## Rank" in md

    def test_rank_path_label_in_header(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        assert "path=fallback" in md

    def test_rank_candidate_table_has_ident_and_score(self):
        t = self._make_trace_with_harvest_and_rank()
        md = render_trace_markdown(t)
        assert "arxiv:2401.10001" in md
        assert "0.8500" in md
        assert "arxiv:2401.10002" in md

    def test_rank_section_absent_when_no_record(self):
        t = DecisionTrace()
        # Only add a harvest record, no rank record
        t.add(
            "harvest", "target",
            target_id="T1", lane="gap",
            queries=[{"engine": "arxiv", "query": "test", "n_returned": 1}],
            n_candidates=1,
            seen_dropped=0,
        )
        md = render_trace_markdown(t)
        assert "## Rank" not in md

    def test_harvest_section_absent_when_no_record(self):
        t = DecisionTrace()
        # Only add a rank record, no harvest record
        t.add(
            "rank", "scores",
            path="fallback",
            candidates=[{"ident": "arxiv:1234", "score": 0.5}],
            n=1,
        )
        md = render_trace_markdown(t)
        assert "## Harvest" not in md

    def test_render_harvest_rank_section_ordering(self):
        """Harvest and Rank sections appear before Selection in the rendered output."""
        t = self._make_trace_with_harvest_and_rank()
        # Also add a select/final record to verify ordering
        t.add("select", "final", selected=[{"ident": "arxiv:2401.10001", "lane": "gap", "score": 0.85}], total_cap=5)
        md = render_trace_markdown(t)
        harvest_pos = md.find("## Harvest")
        rank_pos = md.find("## Rank")
        selection_pos = md.find("## Selection")
        # Harvest before Rank, Rank before Selection
        assert harvest_pos < rank_pos
        assert rank_pos < selection_pos


# ---------------------------------------------------------------------------
# Phase-4A: learned kwarg on route_to_sources + build_plan
# ---------------------------------------------------------------------------


def _make_learned_routing(source_id: str, rep: float) -> dict:
    """Minimal learned dict seeded with one source reputation for routing tests."""
    return {
        "updated": "2026-06-24",
        "sources": {
            source_id: {"accept": 5, "reject": 0, "rep": rep},
        },
        "engines": {},
        "reject_patterns": {"keywords": []},
        "calibration": {"accepted_score_mean": 0.7, "rejected_score_mean": 0.3, "n": 5},
        "processed_ids": [],
    }


class TestRouteToSourcesLearned:
    """route_to_sources uses learned rep to reorder within each fillable_by class."""

    def _reg_with_two_arxiv(self) -> dict:
        """Registry with two arxiv sources: A (relevance=3) and B (relevance=5)."""
        return {
            "paper-publisher": {
                "sources": [
                    {
                        "id": "arxiv_low_rel",
                        "name": "arXiv low",
                        "category": "preprint_repository",
                        "relevance": 3,
                        "rss_feed": "",
                        "url": "",
                    },
                    {
                        "id": "arxiv_high_rel",
                        "name": "arXiv high",
                        "category": "preprint_repository",
                        "relevance": 5,
                        "rss_feed": "",
                        "url": "",
                    },
                ]
            },
            "blog-newsfeed": {"sources": []},
            "github-repos": {"repositories": []},
        }

    def _target_arxiv(self) -> dict:
        return {
            "target_id": "GAP-01",
            "lane": "gap",
            "origin_ids": ["GAP-01"],
            "queries": ["test query"],
            "fillable_by": ["arxiv"],
            "routed_sources": [],
            "_gap": None,
            "_dir": None,
        }

    def test_learned_none_uses_default_ordering(self):
        """learned=None leaves ordering by relevance unchanged."""
        reg = self._reg_with_two_arxiv()
        target = self._target_arxiv()
        sources = route_to_sources(target, reg, learned=None)
        # High relevance (5) should come first
        assert sources[0] == "arxiv_high_rel"
        assert sources[1] == "arxiv_low_rel"

    def test_high_rep_source_promoted_above_higher_relevance(self):
        """A source with strong rep+enough to beat the relevance gap is promoted first."""
        reg = self._reg_with_two_arxiv()
        target = self._target_arxiv()
        # arxiv_low_rel has relevance=3; we give it rep=+2.5, so total = 5.5 > 5.0
        learned = _make_learned_routing("arxiv_low_rel", rep=2.5)
        sources = route_to_sources(target, reg, learned=learned)
        # low_rel + big rep should now sort ahead of high_rel
        assert sources[0] == "arxiv_low_rel", (
            f"Expected arxiv_low_rel first (rep boost), got {sources}"
        )

    def test_neutral_rep_does_not_change_order(self):
        """A source with rep=0.0 does not change the existing order."""
        reg = self._reg_with_two_arxiv()
        target = self._target_arxiv()
        learned = _make_learned_routing("arxiv_low_rel", rep=0.0)
        sources_no_learned = route_to_sources(target, reg, learned=None)
        sources_neutral = route_to_sources(target, reg, learned=learned)
        assert sources_no_learned == sources_neutral

    def test_web_tag_rep_boosts_blog_source(self):
        """rep_for boosts a blog-newsfeed source in the web tag path."""
        reg = {
            "paper-publisher": {"sources": []},
            "blog-newsfeed": {
                "sources": [
                    {"id": "blog_low",  "name": "low",  "relevance": 2, "category": "vendor_blogs", "focus": "test"},
                    {"id": "blog_high", "name": "high", "relevance": 4, "category": "vendor_blogs", "focus": "test"},
                ]
            },
            "github-repos": {"repositories": []},
        }
        target = {
            "target_id": "GAP-01", "lane": "gap", "origin_ids": ["GAP-01"],
            "queries": ["test"], "fillable_by": ["web"], "routed_sources": [],
            "_gap": None, "_dir": None,
        }
        # blog_low has relevance=2; give it rep=+3.0 -> total 5.0 >= blog_high (4.0)
        learned = _make_learned_routing("blog_low", rep=3.0)
        sources = route_to_sources(target, reg, learned=learned)
        assert sources[0] == "blog_low", f"Expected blog_low first, got {sources}"

    def test_build_plan_threads_learned_into_routing(self, tmp_path):
        """build_plan(..., learned=...) threads learned through to route_to_sources."""
        vault = tmp_path / "vault"
        gap_dir = vault / "wiki" / "gap"
        gap_dir.mkdir(parents=True)
        (gap_dir / "GAP-04-llm.md").write_text(GAP_04_FILE, encoding="utf-8")
        (vault / "objective" / "direction").mkdir(parents=True)
        (vault / "meta").mkdir(parents=True)

        # Give arxiv_cs_dc a strong rep so it sorts ahead of everything else
        reg = {
            "paper-publisher": {
                "sources": [
                    {
                        "id": "arxiv_cs_dc",
                        "name": "arXiv cs.DC",
                        "category": "preprint_repository",
                        "relevance": 3,
                        "rss_feed": "",
                        "url": "",
                    },
                    {
                        "id": "arxiv_cs_ar",
                        "name": "arXiv cs.AR",
                        "category": "preprint_repository",
                        "relevance": 5,
                        "rss_feed": "",
                        "url": "",
                    },
                ]
            },
            "blog-newsfeed": {"sources": []},
            "github-repos": {"repositories": []},
        }
        config = dict(CONFIG_FIXTURE)
        learned = _make_learned_routing("arxiv_cs_dc", rep=3.0)  # rep=3 boosts cs.DC above cs.AR (rel=5)

        plan = build_plan(str(vault), config, reg, learned=learned)
        targets = plan["targets"]
        # At least one target should have arxiv_cs_dc first in routed_sources
        gap_targets = [t for t in targets if t["lane"] == "gap"]
        # With rep=3 on arxiv_cs_dc (total 3+3=6) vs arxiv_cs_ar (5+0=5), cs.DC should sort first
        for t in gap_targets:
            if t.get("routed_sources"):
                assert t["routed_sources"][0] == "arxiv_cs_dc", (
                    f"Expected arxiv_cs_dc first in {t['routed_sources']}"
                )
