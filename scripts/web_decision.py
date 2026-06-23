#!/usr/bin/env python3
"""web_decision.py -- DECISION engine for Second Brain Phase 3 web agent.

Reads the wiki Gaps + research Directions + source registry + web-config and produces:
  (a) A CRAWL PLAN of ranked targets to harvest.
  (b) A SELECTION function that picks <=5 final candidates by typed budget.

Pure Python, $0, fully offline. Does NOT do network fetching (that is web_harvest.py)
and does NOT rank by embeddings (that is rerank.py).

CLI:
  python scripts/web_decision.py plan [--vault <root>] [--json]
    -> load config + registry from .claude/web/, run build_plan, print the plan.

Exit codes:
  0  -- success
  2  -- usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Decision trace
# ---------------------------------------------------------------------------


class DecisionTrace:
    """Structured, optional decision trace.

    Pass into build_plan/select_candidates/route_to_sources/merge_targets;
    when None, no records are kept (zero overhead).

    Each record is a dict with keys:
      phase (str)  -- coarse stage, e.g. "parse", "merge", "lanes", "route", "select"
      name  (str)  -- sub-step label, e.g. "gaps", "scores", "target", "lane"
      data  (dict) -- step-specific payload (see spec in each function below)
    """

    def __init__(self) -> None:
        self.records: list[dict] = []

    def add(self, phase: str, name: str, **data: Any) -> None:
        self.records.append({"phase": phase, "name": name, "data": data})

    def find(self, phase: str, name: str) -> list[dict]:
        """Return all records matching phase+name (convenience for tests/renderer)."""
        return [r for r in self.records if r["phase"] == phase and r["name"] == name]

    def first(self, phase: str, name: str) -> dict | None:
        """Return the first record matching phase+name, or None."""
        hits = self.find(phase, name)
        return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Import candidate_ident from web_harvest (lazy to stay monkeypatch-safe)
# ---------------------------------------------------------------------------


def _candidate_ident(cand: dict) -> str:
    """Delegate to web_harvest.candidate_ident; imported lazily."""
    try:
        from web_harvest import candidate_ident
        return candidate_ident(cand)
    except ImportError:
        # Fallback (should not happen in production): use source_id
        sid = (cand.get("source_id") or "")
        if sid.startswith(("arxiv:", "doi:")):
            return sid
        return (cand.get("url") or "").rstrip("/") or sid

# ---------------------------------------------------------------------------
# Merge threshold
# ---------------------------------------------------------------------------

# Minimum Jaccard-plus-topic-bonus score required for a (gap, direction) pair
# to be merged. Set empirically against the Inference-Disagg vault:
#   - DIR-0004 / GAP-08 scores ~0.24 (strong lexical overlap on "prior-art /
#     Photons-to-Tokens / cited / papers / ingested" + shared T-0006) -> merges.
#   - DIR-0001 / GAP-02..GAP-08 score ~0.03-0.07 (domain stoplist strips the
#     ubiquitous "optical" token; no lexical anchor remaining) -> do NOT merge.
# 0.18 is the right split point: comfortably below 0.24 and above 0.07.
MERGE_THRESHOLD: float = 0.18

# Domain tokens that are too ubiquitous in this vault to be discriminative.
# Applied on top of the short-token filter (len < 4) and English stopwords.
_DOMAIN_STOPLIST: frozenset[str] = frozenset(
    {
        "optical", "iris", "tetra", "vault", "model", "cache",
        "inference", "disaggregation", "gpu", "llm", "scale",
        "design", "cost", "data",
    }
)

# Minimal English function-word stoplist (tokens that survive len>=4 but are
# not discriminative).
_EN_STOPWORDS: frozenset[str] = frozenset(
    {
        "that", "this", "with", "from", "have", "been", "will",
        "were", "they", "them", "than", "then", "also", "into",
        "only", "some", "such", "each", "both", "very", "more",
        "most", "over", "under", "does", "what", "when", "where",
        "which", "their", "there", "these", "those", "would", "could",
        "should", "about", "after", "before", "between", "across",
        "through", "other", "within", "system", "based", "using",
        "used", "need", "does", "page", "pages", "paper", "papers",
        "source", "sources",
    }
)


def _tokenize(text: str) -> frozenset[str]:
    """Lowercase, split on non-alphanumerics, drop short/stop/domain tokens."""
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return frozenset(
        t for t in tokens
        if len(t) >= 4
        and t not in _EN_STOPWORDS
        and t not in _DOMAIN_STOPLIST
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Parse per-gap files in wiki/gap/
# ---------------------------------------------------------------------------

_PRIORITY_RE = re.compile(r"^(high|medium|low)\b", re.IGNORECASE)
_TOPIC_RE = re.compile(r"T-\d+")
_FILLABLE_TAG_RE = re.compile(
    r"\b(arxiv|web|github|forum|x)\b", re.IGNORECASE
)

# Regex to extract YAML inline-list items, e.g. [T-0006, T-0003] or ["[[...]]", "[[...]]"]
_YAML_LIST_RE = re.compile(r'\[([^\]]*)\]')


def _parse_gap_frontmatter_list(raw: str) -> list[str]:
    """Parse a YAML inline list value like [T-0006] or [arxiv, web] into a list of strings.

    Also handles plain comma-separated values (no brackets).
    """
    raw = raw.strip()
    # Try inline list syntax [a, b, c]
    m = _YAML_LIST_RE.match(raw)
    if m:
        inner = m.group(1)
    else:
        inner = raw
    # Split on commas, strip whitespace and quotes
    items = []
    for item in inner.split(","):
        item = item.strip().strip("\"'")
        if item:
            items.append(item)
    return items


def parse_gaps(
    gap_dir: str,
    *,
    trace: "DecisionTrace | None" = None,
) -> list[dict]:
    """Parse per-gap files from the wiki/gap/ directory.

    Each file matching GAP-*.md (skipping _template.md and index.md) is a gap.
    Only gaps with status: open (or missing status) are included.

    Returns list of dicts with keys:
      id, title, shows_up_in, missing, fillable_by, topics, priority
    """
    d = Path(gap_dir)
    if not d.is_dir():
        return []

    gaps = []

    for path in sorted(d.glob("GAP-*.md")):
        # Skip _template.md and index.md (also GAP-prefixed safety: just check these names)
        if path.name.startswith("_") or path.name == "index.md":
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        fm = _parse_frontmatter(text)

        # Filter: only open (or missing) status
        status = fm.get("status", "open").strip().lower()
        if status and status != "open":
            continue

        gap_id = fm.get("id", "").strip()
        if not gap_id:
            # Derive id from filename stem (e.g. GAP-08-some-slug -> GAP-08)
            stem = path.stem  # e.g. "GAP-08-optical-prior-art"
            m = re.match(r"^(GAP-\d+)", stem, re.IGNORECASE)
            gap_id = m.group(1).upper() if m else stem

        title = fm.get("title", "").strip().strip("\"'")

        # shows_up_in: YAML list of wikilinks -> join as comma-separated string for downstream
        shows_up_in_raw = fm.get("shows_up_in", "")
        shows_up_in_list = _parse_gap_frontmatter_list(shows_up_in_raw)
        shows_up_in = ", ".join(shows_up_in_list)

        # Extract ## Missing body section
        missing = _parse_section(text, "Missing")

        # fillable_by: YAML inline list [arxiv, web] or plain string
        fillable_by_raw = fm.get("fillable_by", "")
        fillable_by_items = _parse_gap_frontmatter_list(fillable_by_raw)
        fillable_by = list(
            dict.fromkeys(  # deduplicate, preserve order
                t.lower() for t in _FILLABLE_TAG_RE.findall(" ".join(fillable_by_items))
            )
        )

        # topics: YAML inline list [T-0006, T-0003] or plain string
        topics_raw = fm.get("topics", "")
        topics = _TOPIC_RE.findall(topics_raw)

        # priority: plain string (high/medium/low)
        priority_raw = fm.get("priority", "medium").strip()
        pm = _PRIORITY_RE.match(priority_raw)
        priority = pm.group(1).lower() if pm else "medium"

        gaps.append(
            {
                "id": gap_id,
                "title": title,
                "shows_up_in": shows_up_in,
                "missing": missing,
                "fillable_by": fillable_by,
                "topics": topics,
                "priority": priority,
            }
        )

    if trace is not None:
        trace.add(
            "parse",
            "gaps",
            gaps=[
                {"id": g["id"], "priority": g["priority"], "topics": g["topics"], "fillable_by": g["fillable_by"]}
                for g in gaps
            ],
            count=len(gaps),
        )

    return gaps


# ---------------------------------------------------------------------------
# Parse DIR-*.md files
# ---------------------------------------------------------------------------

_ENGINE_TAG_RE = re.compile(
    r"^(web|arxiv|github|forum|x):\s*(.+)$", re.IGNORECASE
)


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML-style frontmatter between --- delimiters."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            fm[k.strip().lower()] = v.strip().strip("\"'")
    return fm


def _parse_seed_queries(body: str) -> list[dict]:
    """Extract seed_queries section from a direction body.

    Items can be:
      - plain query text
      - engine-tagged: arxiv: some query
    Returns list of {engine: str|None, query: str}.
    """
    # Find ## seed_queries section
    m = re.search(r"^##\s+seed_queries\s*$", body, re.MULTILINE)
    if not m:
        return []
    section = body[m.end():]
    # Stop at the next ## heading
    stop = re.search(r"^##\s+", section, re.MULTILINE)
    if stop:
        section = section[: stop.start()]

    queries = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        if not item:
            continue
        tag_match = _ENGINE_TAG_RE.match(item)
        if tag_match:
            queries.append(
                {"engine": tag_match.group(1).lower(), "query": tag_match.group(2).strip()}
            )
        else:
            queries.append({"engine": None, "query": item})
    return queries


def _parse_section(body: str, heading: str) -> str:
    """Extract text of a ## heading section."""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$", body, re.MULTILINE)
    if not m:
        return ""
    section = body[m.end():]
    stop = re.search(r"^##\s+", section, re.MULTILINE)
    if stop:
        section = section[: stop.start()]
    return section.strip()


def parse_directions(
    direction_dir: str,
    trace: "DecisionTrace | None" = None,
) -> list[dict]:
    """Read all DIR-*.md files (skip _template + status != open).

    Returns list of dicts with keys:
      id, serves_question, topics, targets_gap, priority, status,
      seed_queries, expected_evidence, solves_when
    """
    d = Path(direction_dir)
    if not d.is_dir():
        return []

    directions = []
    skipped: list[dict] = []  # for trace

    for path in sorted(d.glob("DIR-*.md")):
        if path.name.startswith("_"):
            if trace is not None:
                skipped.append({"file": path.name, "reason": "template (name starts with _)"})
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        fm = _parse_frontmatter(text)

        status = fm.get("status", "").strip().lower()
        if status != "open":
            if trace is not None:
                skipped.append({"file": path.name, "reason": f"status={status!r} (not open)"})
            continue

        dir_id = fm.get("id", path.stem).strip()
        serves_q_raw = fm.get("serves_question", "").strip()
        # Could be a single Q-id or comma-separated; store as list
        serves_question = [q.strip() for q in serves_q_raw.split(",") if q.strip()]

        topics_raw = fm.get("topics", "").strip()
        topics = _TOPIC_RE.findall(topics_raw)

        targets_gap = fm.get("targets_gap", "").strip().strip('"')
        priority_raw = fm.get("priority", "medium").strip()
        pm = _PRIORITY_RE.match(priority_raw)
        priority = pm.group(1).lower() if pm else "medium"

        # Strip frontmatter from body
        body_match = re.match(r"^---.*?---\r?\n", text, re.DOTALL)
        body = text[body_match.end():] if body_match else text

        seed_queries = _parse_seed_queries(body)
        expected_evidence = _parse_section(body, "expected_evidence")
        solves_when = _parse_section(body, "solves_when")

        # Extract engine tags from seed_queries
        engine_tags = list(
            dict.fromkeys(
                sq["engine"] for sq in seed_queries if sq["engine"] is not None
            )
        )

        directions.append(
            {
                "id": dir_id,
                "serves_question": serves_question,
                "topics": topics,
                "targets_gap": targets_gap,
                "priority": priority,
                "status": status,
                "seed_queries": seed_queries,
                "expected_evidence": expected_evidence,
                "solves_when": solves_when,
                "_engine_tags": engine_tags,  # internal: for fillable_by union
            }
        )

    if trace is not None:
        trace.add(
            "parse",
            "directions",
            directions=[
                {
                    "id": di["id"],
                    "priority": di["priority"],
                    "topics": di["topics"],
                    "serves_question": di["serves_question"],
                }
                for di in directions
            ],
            skipped=skipped,
            count=len(directions),
        )

    return directions


# ---------------------------------------------------------------------------
# Merge targets
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _max_priority(a: str, b: str) -> str:
    """Return the higher of two priority strings (high > medium > low)."""
    if _PRIORITY_ORDER.get(a, 1) <= _PRIORITY_ORDER.get(b, 1):
        return a
    return b


def _score_gap_direction(gap: dict, direction: dict) -> float:
    """Score a (gap, direction) pair by lexical similarity + shared-topic bonus.

    Algorithm:
    1. Tokenize direction.targets_gap and (gap.title + " " + gap.missing) using
       _tokenize() -- lowercased, split on non-alphanumerics, drop tokens < 4
       chars, English stopwords, and the domain stoplist.
    2. Jaccard = |intersection| / |union| of the two token sets.
    3. Add +0.05 per shared topic id (capped at +0.10).

    Returns a float in [0, 1.10] (practically much lower).
    """
    dir_tokens = _tokenize(direction.get("targets_gap", ""))
    gap_text = gap.get("title", "") + " " + gap.get("missing", "")
    gap_tokens = _tokenize(gap_text)

    jaccard = _jaccard(dir_tokens, gap_tokens)

    # Topic bonus: +0.05 per shared topic id, capped at +0.10
    gap_topics = set(gap.get("topics", []))
    dir_topics = set(direction.get("topics", []))
    shared_topics = len(gap_topics & dir_topics)
    topic_bonus = min(shared_topics * 0.05, 0.10)

    return jaccard + topic_bonus


def merge_targets(
    gaps: list[dict],
    directions: list[dict],
    trace: "DecisionTrace | None" = None,
) -> list[dict]:
    """Merge directions INTO gaps using one-to-one greedy Jaccard assignment.

    Algorithm:
    1. Score every (gap, direction) pair with _score_gap_direction().
    2. Sort all pairs by score descending.
    3. Walk the list; merge a pair only if score >= MERGE_THRESHOLD and NEITHER
       the gap nor the direction has already been consumed. Mark both consumed.
    4. Unconsumed gaps -> standalone gap targets.
    5. Unconsumed directions -> standalone research-lane targets.

    This ensures each direction merges into AT MOST ONE gap and each gap
    absorbs AT MOST ONE direction.
    """
    if not gaps or not directions:
        # Fast path: nothing to score
        merged_targets = []
        for gap in gaps:
            queries = _derive_gap_queries(gap)
            merged_targets.append(
                {
                    "target_id": gap["id"],
                    "lane": "gap",
                    "origin_ids": [gap["id"]],
                    "priority": gap["priority"],
                    "queries": queries,
                    "expected_evidence": gap.get("missing", ""),
                    "fillable_by": list(gap.get("fillable_by", [])),
                    "routed_sources": [],
                    "_gap": gap,
                    "_dir": None,
                }
            )
        for direction in directions:
            queries = [sq["query"] for sq in direction.get("seed_queries", [])]
            merged_targets.append(
                {
                    "target_id": direction["id"],
                    "lane": "research",
                    "origin_ids": [direction["id"]],
                    "priority": direction["priority"],
                    "queries": queries,
                    "expected_evidence": direction.get("expected_evidence", ""),
                    "fillable_by": list(direction.get("_engine_tags", [])),
                    "routed_sources": [],
                    "_gap": None,
                    "_dir": direction,
                }
            )
        if trace is not None:
            trace.add(
                "merge",
                "scores",
                threshold=MERGE_THRESHOLD,
                pairs=[],
                merged=[],
                unmatched_gaps=[g["id"] for g in gaps],
                unmatched_directions=[d["id"] for d in directions],
            )
        return merged_targets

    # Score all pairs
    scored_pairs: list[tuple[float, dict, dict]] = []
    for gap in gaps:
        for direction in directions:
            score = _score_gap_direction(gap, direction)
            scored_pairs.append((score, gap, direction))

    # Sort by score desc; use gap.id + dir.id as tiebreaker for stability
    scored_pairs.sort(key=lambda x: (-x[0], x[1]["id"], x[2]["id"]))

    consumed_gaps: set[str] = set()
    consumed_dirs: set[str] = set()
    merges: list[tuple[dict, dict]] = []

    # We need to do two passes when tracing: first collect decisions, then build targets.
    # The greedy walk is identical; we just note which pairs got which decision.
    _trace_consumed_gaps: set[str] = set()
    _trace_consumed_dirs: set[str] = set()
    _trace_pairs: list[dict] = []

    for score, gap, direction in scored_pairs:
        # Compute component scores for the trace record
        dir_tokens = _tokenize(direction.get("targets_gap", ""))
        gap_text = gap.get("title", "") + " " + gap.get("missing", "")
        gap_tokens = _tokenize(gap_text)
        jaccard = _jaccard(dir_tokens, gap_tokens)
        gap_topics = set(gap.get("topics", []))
        dir_topics = set(direction.get("topics", []))
        shared = len(gap_topics & dir_topics)
        topic_bonus = min(shared * 0.05, 0.10)

        if score < MERGE_THRESHOLD:
            decision = "below-threshold"
        elif gap["id"] in consumed_gaps:
            decision = "gap-consumed"
        elif direction["id"] in consumed_dirs:
            decision = "direction-consumed"
        else:
            decision = "MERGED"
            merges.append((gap, direction))
            consumed_gaps.add(gap["id"])
            consumed_dirs.add(direction["id"])

        if trace is not None:
            _trace_pairs.append({
                "gap": gap["id"],
                "direction": direction["id"],
                "jaccard": round(jaccard, 4),
                "topic_bonus": round(topic_bonus, 4),
                "total": round(score, 4),
                "decision": decision,
            })

    # Build targets from merges
    merged_targets: list[dict] = []

    merged_gap_ids = {gap["id"] for gap, _ in merges}
    merged_dir_ids = {direction["id"] for _, direction in merges}

    # 1. Merged targets
    for gap, direction in merges:
        queries = [sq["query"] for sq in direction.get("seed_queries", [])]
        if not queries:
            queries = _derive_gap_queries(gap)
        fillable_by = list(
            dict.fromkeys(
                gap.get("fillable_by", []) + direction.get("_engine_tags", [])
            )
        )
        merged_targets.append(
            {
                "target_id": f"{gap['id']}+{direction['id']}",
                "lane": "gap",
                "origin_ids": [gap["id"], direction["id"]],
                "priority": _max_priority(gap["priority"], direction["priority"]),
                "queries": queries,
                "expected_evidence": direction.get("expected_evidence", ""),
                "fillable_by": fillable_by,
                "routed_sources": [],
                "_gap": gap,
                "_dir": direction,
            }
        )

    # 2. Standalone gaps (not consumed by a merge)
    for gap in gaps:
        if gap["id"] in merged_gap_ids:
            continue
        queries = _derive_gap_queries(gap)
        merged_targets.append(
            {
                "target_id": gap["id"],
                "lane": "gap",
                "origin_ids": [gap["id"]],
                "priority": gap["priority"],
                "queries": queries,
                "expected_evidence": gap.get("missing", ""),
                "fillable_by": list(gap.get("fillable_by", [])),
                "routed_sources": [],
                "_gap": gap,
                "_dir": None,
            }
        )

    # 3. Standalone directions (not consumed by a merge) -> research lane
    for direction in directions:
        if direction["id"] in merged_dir_ids:
            continue
        queries = [sq["query"] for sq in direction.get("seed_queries", [])]
        merged_targets.append(
            {
                "target_id": direction["id"],
                "lane": "research",
                "origin_ids": [direction["id"]],
                "priority": direction["priority"],
                "queries": queries,
                "expected_evidence": direction.get("expected_evidence", ""),
                "fillable_by": list(direction.get("_engine_tags", [])),
                "routed_sources": [],
                "_gap": None,
                "_dir": direction,
            }
        )

    if trace is not None:
        # Sort trace pairs by total desc for readability
        _trace_pairs.sort(key=lambda p: -p["total"])
        trace.add(
            "merge",
            "scores",
            threshold=MERGE_THRESHOLD,
            pairs=_trace_pairs,
            merged=[
                {
                    "target_id": f"{g['id']}+{d['id']}",
                    "origin_ids": [g["id"], d["id"]],
                    "priority": _max_priority(g["priority"], d["priority"]),
                }
                for g, d in merges
            ],
            unmatched_gaps=[g["id"] for g in gaps if g["id"] not in merged_gap_ids],
            unmatched_directions=[d["id"] for d in directions if d["id"] not in merged_dir_ids],
        )

    return merged_targets


def _derive_gap_queries(gap: dict) -> list[str]:
    """Derive search queries from a gap's missing + title fields."""
    queries = []
    title = gap.get("title", "").strip()
    missing = gap.get("missing", "").strip()

    if title:
        queries.append(title)
    if missing:
        # Take the first sentence of missing as a query
        first_sentence = re.split(r"[;.]", missing)[0].strip()
        if first_sentence and first_sentence != title:
            queries.append(first_sentence)

    return queries


# ---------------------------------------------------------------------------
# Assign lanes
# ---------------------------------------------------------------------------


def assign_lanes(
    merged_targets: list[dict],
    trace: "DecisionTrace | None" = None,
) -> dict:
    """Assign and sort merged targets into gap and research lanes.

    - Any target whose origin includes a GAP -> gap lane
      (sort by priority high>med>low, then prefer arxiv-fillable targets
       i.e. those whose fillable_by contains 'arxiv')
    - A standalone direction -> research lane (sort by priority)

    Returns {"gap": [...], "research": [...]}
    """

    def _gap_sort_key(t: dict) -> tuple:
        prio = _PRIORITY_ORDER.get(t["priority"], 1)
        # Prefer arxiv-fillable (lower secondary sort value = comes first)
        has_arxiv = 0 if "arxiv" in t.get("fillable_by", []) else 1
        return (prio, has_arxiv)

    def _research_sort_key(t: dict) -> tuple:
        return (_PRIORITY_ORDER.get(t["priority"], 1),)

    gap_lane = sorted(
        [t for t in merged_targets if t["lane"] == "gap"],
        key=_gap_sort_key,
    )
    research_lane = sorted(
        [t for t in merged_targets if t["lane"] == "research"],
        key=_research_sort_key,
    )

    if trace is not None:
        trace.add(
            "lanes",
            "assign",
            lanes={
                "gap": [t["target_id"] for t in gap_lane],
                "research": [t["target_id"] for t in research_lane],
                "news": ["news"],
            },
        )

    return {"gap": gap_lane, "research": research_lane}


# ---------------------------------------------------------------------------
# News target
# ---------------------------------------------------------------------------

_NEWS_CATEGORIES = {
    "vendor_blogs",
    "benchmarking",
    "hardware_analysis",
    "specialist_research",
}


def news_target(registry: dict, purpose_text: str = "") -> dict:
    """Build a single lane=news target from the highest-relevance blog/news feeds."""
    # Collect all sources from blog-newsfeed registry
    sources = registry.get("blog-newsfeed", {}).get("sources", [])

    # Filter to news-relevant categories, sorted by relevance desc
    news_sources = [
        s for s in sources
        if s.get("category", "") in _NEWS_CATEGORIES
    ]
    news_sources.sort(key=lambda s: -int(s.get("relevance", 0)))

    # Top 5 by relevance
    top_sources = news_sources[:5]
    routed_ids = [s["id"] for s in top_sources]

    return {
        "target_id": "news",
        "lane": "news",
        "origin_ids": ["news"],
        "priority": "medium",
        "queries": [],
        "expected_evidence": "recent vendor/analyst announcement relevant to the vault PURPOSE",
        "fillable_by": ["web"],
        "routed_sources": routed_ids,
    }


# ---------------------------------------------------------------------------
# Route to sources
# ---------------------------------------------------------------------------

_ARXIV_CATEGORIES = {"preprint_repository", "paper_api", "research_aggregator"}


def route_to_sources(
    target: dict,
    registry: dict,
    trace: "DecisionTrace | None" = None,
) -> list[str]:
    """Map fillable_by tags to registry source ids.

    Mapping:
      arxiv -> paper-publisher sources (preprint_repository / paper_api / research_aggregator)
      github -> github-repos entries
      web -> blog-newsfeed sources (rank by relevance + focus-keyword overlap)
      forum/x -> [] (handled by API engines, not registry)

    Returns registry ids best-first, capped at 5.
    """
    fillable_by = target.get("fillable_by", [])
    result_ids: list[str] = []
    seen: set[str] = set()

    # For trace: collect all candidates considered with scoring details
    _trace_considered: list[dict] = []

    for tag in fillable_by:
        tag = tag.lower()

        if tag == "arxiv":
            paper_sources = registry.get("paper-publisher", {}).get("sources", [])
            arxiv_sources = [
                s for s in paper_sources
                if s.get("category", "") in _ARXIV_CATEGORIES
            ]
            arxiv_sources.sort(key=lambda s: -int(s.get("relevance", 0)))
            for s in arxiv_sources:
                chosen = s["id"] not in seen and len(result_ids) < 5
                reason = "chosen" if chosen else ("already-seen" if s["id"] in seen else "cap-5")
                if trace is not None:
                    _trace_considered.append({
                        "source_id": s["id"],
                        "category": s.get("category", ""),
                        "relevance": int(s.get("relevance", 0)),
                        "focus_score": 0.0,
                        "chosen": chosen,
                        "reason": reason,
                    })
                if s["id"] not in seen:
                    result_ids.append(s["id"])
                    seen.add(s["id"])

        elif tag == "github":
            repos = registry.get("github-repos", {}).get("repositories", [])
            repos_sorted = sorted(repos, key=lambda s: -int(s.get("relevance", 0)))
            for s in repos_sorted:
                chosen = s["id"] not in seen and len(result_ids) < 5
                reason = "chosen" if chosen else ("already-seen" if s["id"] in seen else "cap-5")
                if trace is not None:
                    _trace_considered.append({
                        "source_id": s["id"],
                        "category": s.get("category", ""),
                        "relevance": int(s.get("relevance", 0)),
                        "focus_score": 0.0,
                        "chosen": chosen,
                        "reason": reason,
                    })
                if s["id"] not in seen:
                    result_ids.append(s["id"])
                    seen.add(s["id"])

        elif tag == "web":
            # Score: relevance + keyword overlap with target topics/title
            blog_sources = registry.get("blog-newsfeed", {}).get("sources", [])
            target_keywords = _target_keywords(target)

            def _web_score(s: dict) -> float:
                rel = float(s.get("relevance", 0))
                focus = (s.get("focus", "") + " " + s.get("name", "")).lower()
                overlap = sum(1 for kw in target_keywords if kw.lower() in focus)
                return rel + overlap * 0.5

            blog_sorted = sorted(blog_sources, key=lambda s: -_web_score(s))
            for s in blog_sorted:
                fs = _web_score(s)
                chosen = s["id"] not in seen and len(result_ids) < 5
                reason = "chosen" if chosen else ("already-seen" if s["id"] in seen else "cap-5")
                if trace is not None:
                    _trace_considered.append({
                        "source_id": s["id"],
                        "category": s.get("category", ""),
                        "relevance": int(s.get("relevance", 0)),
                        "focus_score": round(fs, 4),
                        "chosen": chosen,
                        "reason": reason,
                    })
                if s["id"] not in seen:
                    result_ids.append(s["id"])
                    seen.add(s["id"])

        # forum / x: skip (API engines handle these)

    routed = result_ids[:5]

    if trace is not None:
        trace.add(
            "route",
            "target",
            target_id=target.get("target_id", ""),
            lane=target.get("lane", ""),
            fillable_by=list(fillable_by),
            considered=_trace_considered,
            routed=routed,
        )

    return routed


def _target_keywords(target: dict) -> list[str]:
    """Extract keywords from target topics, title, and queries."""
    keywords = []
    # Topic IDs -> topic tokens
    for topic in target.get("_gap", {}).get("topics", []) if target.get("_gap") else []:
        keywords.append(topic)
    # Title words
    title = ""
    if target.get("_gap"):
        title = target["_gap"].get("title", "")
    elif target.get("_dir"):
        title = target["_dir"].get("targets_gap", "")
    keywords.extend(w for w in title.lower().split() if len(w) > 3)
    # First query words
    for q in target.get("queries", [])[:2]:
        keywords.extend(w for w in q.lower().split() if len(w) > 3)
    return keywords[:20]


# ---------------------------------------------------------------------------
# Load registry
# ---------------------------------------------------------------------------


def load_registry(web_config_dir: Path) -> dict:
    """Load all three registry files from .claude/web/sources/."""
    sources_dir = web_config_dir / "sources"
    registry = {}

    for name, key in [
        ("paper-publisher.json", "paper-publisher"),
        ("blog-newsfeed.json", "blog-newsfeed"),
        ("github-repos.json", "github-repos"),
    ]:
        p = sources_dir / name
        if p.exists():
            try:
                registry[key] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                registry[key] = {}

    return registry


# ---------------------------------------------------------------------------
# Build plan
# ---------------------------------------------------------------------------


def build_plan(
    vault_root: str,
    config: dict,
    registry: dict,
    trace: "DecisionTrace | None" = None,
) -> dict:
    """Orchestrate parse -> merge -> lanes -> news -> route.

    Returns {"targets": [...ordered: gap, research, news...], "config": config, "notes": [...]}.
    """
    vault_path = Path(vault_root)
    notes = []

    # Parse
    gap_dir = vault_path / "wiki" / "gap"
    if gap_dir.is_dir():
        gaps = parse_gaps(str(gap_dir), trace=trace)
        notes.append(f"parsed {len(gaps)} gaps from wiki/gap/")
    else:
        gaps = []
        notes.append("wiki/gap/ not found -- no gaps loaded")

    direction_dir = vault_path / "objective" / "direction"
    directions = parse_directions(str(direction_dir), trace=trace)
    notes.append(f"parsed {len(directions)} open directions from objective/direction/")

    # Merge
    merged = merge_targets(gaps, directions, trace=trace)
    notes.append(f"merged into {len(merged)} targets ({sum(1 for t in merged if t['lane']=='gap')} gap, {sum(1 for t in merged if t['lane']=='research')} research)")

    # Assign lanes
    lanes = assign_lanes(merged, trace=trace)

    # Route to sources
    for target in lanes["gap"] + lanes["research"]:
        target["routed_sources"] = route_to_sources(target, registry, trace=trace)

    # News target
    purpose_text = ""
    try:
        purpose_path = vault_path / "vault.yaml"
        if purpose_path.exists():
            import yaml
            vault_yaml = yaml.safe_load(purpose_path.read_text())
            purpose_text = (vault_yaml or {}).get("purpose", "")
    except Exception:
        pass
    news = news_target(registry, purpose_text)

    # Build ordered target list: gap, research, news
    all_targets = []
    for t in lanes["gap"] + lanes["research"] + [news]:
        # Clean internal-only keys before returning
        clean = {k: v for k, v in t.items() if not k.startswith("_")}
        all_targets.append(clean)

    return {"targets": all_targets, "config": config, "notes": notes}


# ---------------------------------------------------------------------------
# Select candidates
# ---------------------------------------------------------------------------


def select_candidates(
    candidates: list[dict],
    config: dict,
    ingest_rows: list[dict] | None = None,
    trace: "DecisionTrace | None" = None,
) -> list[dict]:
    """Pick <=5 final candidates by typed budget.

    Steps:
    1. intra-pool dedup: collapse candidates with the same candidate_ident (keep
       the one with the higher score) BEFORE quota filling, so the same paper
       found via two queries / two feeds doesn't take two slots.
    2. ingest-dedup: drop candidates whose candidate_ident matches an ingest row
       id (web-enqueued rows are keyed by candidate_ident) OR whose url matches
       a row url, for rows with status in {ingested, pending, rejected}.
    3. Group by lane, sort each lane by score desc.
    4. Fill each lane up to its quota.
    5. Spillover: underfill donates spare slots to the next lane in spillover_order.
    6. Cap total at config.new_sources_total.

    Each returned candidate is tagged with its lane.
    """
    # --- Step 1: intra-pool dedup by candidate_ident (keep highest score) ---
    seen_idents: dict[str, dict] = {}  # ident -> best candidate so far
    _intra_pool_dropped: list[dict] = []  # for trace

    for c in candidates:
        ident = _candidate_ident(c)
        if not ident:
            # No stable ident: cannot dedup reliably; keep as-is under a unique key
            seen_idents[id(c)] = c  # type: ignore[assignment]
            continue
        existing = seen_idents.get(ident)
        if existing is None or float(c.get("score", 0.0)) > float(existing.get("score", 0.0)):
            if existing is not None and trace is not None:
                _intra_pool_dropped.append({
                    "ident": ident,
                    "dropped_id": existing.get("id", ident),
                    "kept_id": c.get("id", ident),
                })
            seen_idents[ident] = c
        else:
            if trace is not None:
                _intra_pool_dropped.append({
                    "ident": ident,
                    "dropped_id": c.get("id", ident),
                    "kept_id": existing.get("id", ident),  # type: ignore[union-attr]
                })
    deduped_pool = list(seen_idents.values())

    # --- Step 2: ingest-dedup ---
    dedup_idents: set[str] = set()
    dedup_urls: set[str] = set()
    # Map ident/url -> (row_id, status) for trace
    _ingest_dedup_map: dict[str, tuple[str, str]] = {}

    if ingest_rows:
        for row in ingest_rows:
            status = row.get("status", "")
            if status in ("ingested", "pending", "rejected"):
                # Rows enqueued by the new pipeline carry candidate_ident as their id;
                # rows from older pipelines may carry source_id -- match both.
                sid = row.get("id") or row.get("source_id") or ""
                url = row.get("url") or ""
                if sid:
                    dedup_idents.add(sid)
                    _ingest_dedup_map[sid] = (sid, status)
                if url:
                    clean_url = url.rstrip("/")
                    dedup_urls.add(clean_url)
                    _ingest_dedup_map[clean_url] = (sid, status)

    def _is_duped(c: dict) -> bool:
        ident = _candidate_ident(c)
        url = (c.get("url") or "").rstrip("/")
        return ident in dedup_idents or (url and url in dedup_urls)

    def _dedup_status(c: dict) -> str:
        """Return the ingest status that caused this candidate to be deduped."""
        ident = _candidate_ident(c)
        if ident in _ingest_dedup_map:
            return _ingest_dedup_map[ident][1]
        url = (c.get("url") or "").rstrip("/")
        if url and url in _ingest_dedup_map:
            return _ingest_dedup_map[url][1]
        return "unknown"

    _ingest_dropped: list[dict] = []  # for trace

    filtered = []
    for c in deduped_pool:
        if _is_duped(c):
            if trace is not None:
                ident = _candidate_ident(c)
                status = _dedup_status(c)
                row_id = _ingest_dedup_map.get(ident, _ingest_dedup_map.get(
                    (c.get("url") or "").rstrip("/"), ("", status)
                ))[0]
                _ingest_dropped.append({
                    "ident": ident,
                    "matched_row_id": row_id,
                    "status": status,
                })
        else:
            filtered.append(c)

    # Group by lane, sort by score desc
    lanes_map: dict[str, list[dict]] = {}
    for c in filtered:
        lane = c.get("lane", "gap")
        lanes_map.setdefault(lane, []).append(c)

    for lane in lanes_map:
        lanes_map[lane].sort(key=lambda c: -float(c.get("score", 0.0)))

    # Fill quotas
    lanes_config = config.get("lanes", {})
    spillover_order = config.get("spillover_order", ["gap", "research", "news"])
    total_cap = config.get("new_sources_total", 5)

    # Determine per-lane quota
    quotas = {lane: lanes_config.get(lane, {}).get("quota", 0) for lane in spillover_order}

    selected: list[dict] = []
    lane_leftovers: dict[str, list[dict]] = {}

    # First pass: fill each lane up to its quota
    for lane in spillover_order:
        quota = quotas.get(lane, 0)
        pool = lanes_map.get(lane, [])
        chosen = pool[:quota]
        leftover = pool[quota:]
        selected.extend(chosen)
        lane_leftovers[lane] = leftover

        if trace is not None:
            considered = [{"ident": _candidate_ident(c), "score": float(c.get("score", 0.0))} for c in pool]
            selected_idents = [_candidate_ident(c) for c in chosen]
            rejected = []
            for c in leftover:
                ident = _candidate_ident(c)
                rejected.append({
                    "ident": ident,
                    "score": float(c.get("score", 0.0)),
                    "reason": "over-quota",
                })
            if not pool:
                # Record an empty lane entry
                trace.add(
                    "select",
                    "lane",
                    lane=lane,
                    quota=quota,
                    considered=[],
                    selected=[],
                    rejected=[{"ident": "n/a", "score": 0.0, "reason": "lane-empty"}],
                )
            else:
                trace.add(
                    "select",
                    "lane",
                    lane=lane,
                    quota=quota,
                    considered=considered,
                    selected=selected_idents,
                    rejected=rejected,
                )

    # Second pass: spillover
    # For each lane that underfilled, its spare slots go to the NEXT lane in spillover_order
    # that still has ranked candidates.
    spare_slots: dict[str, int] = {}
    for lane in spillover_order:
        used = sum(1 for c in selected if c.get("lane") == lane)
        quota = quotas.get(lane, 0)
        spare = quota - used
        if spare > 0:
            spare_slots[lane] = spare

    _spillover_moves: list[dict] = []  # for trace

    if spare_slots:
        # Collect all leftovers in spillover order
        spill_pool: list[dict] = []
        for lane in spillover_order:
            spill_pool.extend(lane_leftovers.get(lane, []))

        # Give spare slots from underfilled lanes to spill_pool candidates
        total_spare = sum(spare_slots.values())
        added = 0
        for c in spill_pool:
            if added >= total_spare:
                break
            if len(selected) >= total_cap:
                break
            selected.append(c)
            if trace is not None:
                # Determine which lane donated the spare slot
                from_lane = next(
                    (l for l in spillover_order if spare_slots.get(l, 0) > 0),
                    "unknown",
                )
                _spillover_moves.append({
                    "from_lane": from_lane,
                    "to_lane": c.get("lane", "unknown"),
                    "ident": _candidate_ident(c),
                    "slots": spare_slots.get(from_lane, 0),
                })
            added += 1

    # Final cap
    selected = selected[:total_cap]

    if trace is not None:
        # Emit dedup summary
        trace.add(
            "select",
            "dedup",
            intra_pool_dropped=_intra_pool_dropped,
            ingest_dropped=_ingest_dropped,
        )
        # Emit ingest-dedup rejected reasons into appropriate lane records
        # (annotate: for each ingest-dropped candidate, find its lane record and append)
        for dropped in _ingest_dropped:
            ident = dropped["ident"]
            status = dropped["status"]
            # Find which lane this candidate was in (search filtered-out from deduped_pool)
            for c in deduped_pool:
                if _candidate_ident(c) == ident:
                    lane = c.get("lane", "gap")
                    # Find the lane trace record for this lane
                    for rec in trace.records:
                        if rec["phase"] == "select" and rec["name"] == "lane" and rec["data"].get("lane") == lane:
                            rec["data"].setdefault("rejected", []).append({
                                "ident": ident,
                                "score": float(c.get("score", 0.0)),
                                "reason": f"ingest-dedup:{status}",
                            })
                    break

        # Emit spillover summary
        trace.add(
            "select",
            "spillover",
            moves=_spillover_moves,
        )
        # Emit final selection summary
        trace.add(
            "select",
            "final",
            selected=[
                {
                    "ident": _candidate_ident(c),
                    "lane": c.get("lane", ""),
                    "score": float(c.get("score", 0.0)),
                }
                for c in selected
            ],
            total_cap=total_cap,
        )

    return selected


# ---------------------------------------------------------------------------
# Ingest index integration
# ---------------------------------------------------------------------------


def _load_ingest_rows(vault_root: str) -> list[dict]:
    """Load ingest index rows using agents.ingest_index._load.

    Returns the sources dict values as a list, or [] on failure.
    """
    try:
        # Try the module-level _load function
        import sys as _sys
        from pathlib import Path as _Path
        code_root = _Path(__file__).resolve().parent.parent
        if str(code_root) not in _sys.path:
            _sys.path.insert(0, str(code_root))
        from agents.ingest_index import _load  # type: ignore
        vault_name = _Path(vault_root).name
        data = _load(vault_name)
        return list(data.get("sources", {}).values())
    except Exception:
        # Fallback: read meta/ingest_index.json directly
        p = Path(vault_root) / "meta" / "ingest_index.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return list(data.get("sources", {}).values())
            except (OSError, json.JSONDecodeError):
                pass
    return []


# ---------------------------------------------------------------------------
# Trace renderer
# ---------------------------------------------------------------------------


def render_trace_markdown(trace: DecisionTrace) -> str:
    """Render a DecisionTrace to a human-readable markdown report.

    Sections (each skipped if its record is absent):
      # Web Decision Trace  (summary header)
      ## Inputs             (parsed gaps + directions)
      ## Merge scoring      (per-pair table sorted by total desc)
      ## Lanes              (lane -> target_ids assignment)
      ## Routing            (per-target source selection table)
      ## Selection          (per-lane quota table, dedup, spillover, final)
    """
    lines: list[str] = []

    def _h(level: int, text: str) -> None:
        lines.append(f"{'#' * level} {text}")
        lines.append("")

    def _p(text: str) -> None:
        lines.append(text)
        lines.append("")

    # ------------------------------------------------------------------ header
    gaps_rec = trace.first("parse", "gaps")
    dirs_rec = trace.first("parse", "directions")
    merge_rec = trace.first("merge", "scores")

    n_gaps = gaps_rec["data"]["count"] if gaps_rec else 0
    n_dirs = dirs_rec["data"]["count"] if dirs_rec else 0
    n_targets = len(merge_rec["data"]["merged"]) + len(merge_rec["data"]["unmatched_gaps"]) + len(merge_rec["data"]["unmatched_directions"]) if merge_rec else 0

    _h(1, "Web Decision Trace")
    _p(f"Summary: {n_gaps} gap(s), {n_dirs} direction(s), {n_targets} target(s) produced.")

    # ------------------------------------------------------------------ inputs
    if gaps_rec or dirs_rec:
        _h(2, "Inputs")
        if gaps_rec:
            lines.append("### Gaps")
            lines.append("")
            for g in gaps_rec["data"]["gaps"]:
                lines.append(f"- **{g['id']}** priority={g['priority']} topics={','.join(g['topics'])} fillable_by={','.join(g['fillable_by'])}")
            lines.append("")
        if dirs_rec:
            lines.append("### Directions")
            lines.append("")
            for d in dirs_rec["data"]["directions"]:
                lines.append(f"- **{d['id']}** priority={d['priority']} topics={','.join(d['topics'])} serves={','.join(d['serves_question'])}")
            if dirs_rec["data"]["skipped"]:
                lines.append("")
                lines.append("Skipped (template or non-open):")
                for s in dirs_rec["data"]["skipped"]:
                    lines.append(f"- {s['file']}: {s['reason']}")
            lines.append("")

    # ------------------------------------------------------------------ merge
    if merge_rec:
        threshold = merge_rec["data"]["threshold"]
        _h(2, f"Merge scoring (MERGE_THRESHOLD={threshold})")
        pairs = merge_rec["data"]["pairs"]
        if pairs:
            lines.append("| gap | direction | jaccard | topic_bonus | total | decision |")
            lines.append("|-----|-----------|---------|-------------|-------|----------|")
            for p in pairs:
                lines.append(
                    f"| {p['gap']} | {p['direction']} | {p['jaccard']:.4f} | {p['topic_bonus']:.4f} | {p['total']:.4f} | {p['decision']} |"
                )
            lines.append("")
        else:
            _p("(no pairs evaluated -- one side was empty)")

        merged_list = merge_rec["data"]["merged"]
        if merged_list:
            lines.append("**Merged targets:** " + ", ".join(m["target_id"] for m in merged_list))
            lines.append("")
        unmatched_gaps = merge_rec["data"]["unmatched_gaps"]
        if unmatched_gaps:
            lines.append("**Unmatched gaps (standalone gap lane):** " + ", ".join(unmatched_gaps))
            lines.append("")
        unmatched_dirs = merge_rec["data"]["unmatched_directions"]
        if unmatched_dirs:
            lines.append("**Unmatched directions (-> research lane):** " + ", ".join(unmatched_dirs))
            lines.append("")

    # ------------------------------------------------------------------ lanes
    lanes_rec = trace.first("lanes", "assign")
    if lanes_rec:
        _h(2, "Lanes")
        for lane_name, tids in lanes_rec["data"]["lanes"].items():
            if tids:
                lines.append(f"- **{lane_name}:** " + ", ".join(tids))
            else:
                lines.append(f"- **{lane_name}:** (empty)")
        lines.append("")

    # ------------------------------------------------------------------ routing
    route_recs = trace.find("route", "target")
    if route_recs:
        _h(2, "Routing")
        for rec in route_recs:
            d = rec["data"]
            tid = d["target_id"]
            lane = d["lane"]
            lines.append(f"### {tid} (lane={lane})")
            lines.append("")
            lines.append(f"fillable_by: {', '.join(d['fillable_by'])}")
            lines.append("")
            considered = d.get("considered", [])
            if considered:
                lines.append("| source_id | category | relevance | focus_score | chosen | reason |")
                lines.append("|-----------|----------|-----------|-------------|--------|--------|")
                for s in considered:
                    chosen_str = "yes" if s["chosen"] else "no"
                    lines.append(
                        f"| {s['source_id']} | {s['category']} | {s['relevance']} | {s['focus_score']:.4f} | {chosen_str} | {s['reason']} |"
                    )
                lines.append("")
            routed = d.get("routed", [])
            lines.append(f"**Routed to:** {', '.join(routed) if routed else '(none)'}")
            lines.append("")

    # ------------------------------------------------------------------ harvest
    harvest_recs = trace.find("harvest", "target")
    if harvest_recs:
        _h(2, "Harvest")
        lines.append("| target_id | lane | engine | query | n_returned | error |")
        lines.append("|-----------|------|--------|-------|------------|-------|")
        for rec in harvest_recs:
            d = rec["data"]
            tid = d.get("target_id", "")
            lane = d.get("lane", "")
            queries = d.get("queries", [])
            if queries:
                for q_entry in queries:
                    engine = q_entry.get("engine", "")
                    query_text = (q_entry.get("query") or "")[:60]
                    n = q_entry.get("n_returned", 0)
                    err = q_entry.get("error", "")
                    lines.append(
                        f"| {tid} | {lane} | {engine} | {query_text} | {n} | {err} |"
                    )
            else:
                lines.append(f"| {tid} | {lane} | - | - | 0 | - |")
        lines.append("")
        # Summary per target
        lines.append("**Per-target summary:**")
        lines.append("")
        lines.append("| target_id | n_candidates | seen_dropped |")
        lines.append("|-----------|--------------|--------------|")
        for rec in harvest_recs:
            d = rec["data"]
            lines.append(
                f"| {d.get('target_id', '')} "
                f"| {d.get('n_candidates', 0)} "
                f"| {d.get('seen_dropped', 0)} |"
            )
        lines.append("")

    # ------------------------------------------------------------------ rank
    rank_rec = trace.first("rank", "scores")
    if rank_rec:
        d = rank_rec["data"]
        path = d.get("path", "fallback")
        n = d.get("n", 0)
        _h(2, f"Rank (path={path}, n={n})")
        candidates = d.get("candidates", [])
        if candidates:
            lines.append("| rank | ident | score |")
            lines.append("|------|-------|-------|")
            for i, item in enumerate(candidates, 1):
                lines.append(
                    f"| {i} | {item.get('ident', '')} | {item.get('score', 0.0):.4f} |"
                )
            lines.append("")
        else:
            _p("(no ranked candidates)")

    # ------------------------------------------------------------------ selection
    lane_recs = trace.find("select", "lane")
    dedup_rec = trace.first("select", "dedup")
    spill_rec = trace.first("select", "spillover")
    final_rec = trace.first("select", "final")

    if lane_recs or dedup_rec or final_rec:
        total_cap = final_rec["data"]["total_cap"] if final_rec else "?"
        # Build quotas summary for header
        quota_parts = []
        for rec in lane_recs:
            quota_parts.append(f"{rec['data']['lane']}={rec['data']['quota']}")
        quota_str = ", ".join(quota_parts) if quota_parts else "n/a"
        _h(2, f"Selection (budget total={total_cap}, quotas: {quota_str})")

        for rec in lane_recs:
            d = rec["data"]
            lane_name = d["lane"]
            quota = d["quota"]
            considered = d.get("considered", [])
            selected_idents = set(d.get("selected", []))
            rejected = d.get("rejected", [])

            lines.append(f"### Lane: {lane_name} (quota={quota})")
            lines.append("")
            if not considered and not rejected:
                lines.append("(lane empty)")
                lines.append("")
            else:
                lines.append("| rank | ident | score | decision |")
                lines.append("|------|-------|-------|----------|")
                rank = 1
                for c in considered:
                    ident = c["ident"]
                    score = c["score"]
                    decision = "SELECTED" if ident in selected_idents else "over-quota"
                    lines.append(f"| {rank} | {ident} | {score:.4f} | {decision} |")
                    rank += 1
                # Append ingest-dedup rejects that weren't in `considered` (they were filtered earlier)
                shown_idents = {c["ident"] for c in considered}
                for rej in rejected:
                    if rej["ident"] not in shown_idents and rej["reason"] != "over-quota":
                        lines.append(f"| - | {rej['ident']} | {rej['score']:.4f} | {rej['reason']} |")
                lines.append("")

        if dedup_rec:
            lines.append("### Dedup")
            lines.append("")
            intra = dedup_rec["data"].get("intra_pool_dropped", [])
            ingest = dedup_rec["data"].get("ingest_dropped", [])
            if intra:
                lines.append("**Intra-pool duplicates collapsed:**")
                for item in intra:
                    lines.append(f"- ident `{item['ident']}`: dropped `{item['dropped_id']}`, kept `{item['kept_id']}`")
                lines.append("")
            else:
                lines.append("Intra-pool: no duplicates.")
                lines.append("")
            if ingest:
                lines.append("**Ingest-index matches dropped:**")
                for item in ingest:
                    lines.append(f"- `{item['ident']}` matched row `{item['matched_row_id']}` (status={item['status']})")
                lines.append("")
            else:
                lines.append("Ingest-dedup: no matches.")
                lines.append("")

        if spill_rec:
            moves = spill_rec["data"].get("moves", [])
            if moves:
                lines.append("### Spillover")
                lines.append("")
                for m in moves:
                    lines.append(f"- `{m['ident']}` moved from spare {m['from_lane']} slot -> {m['to_lane']} lane")
                lines.append("")

        if final_rec:
            lines.append("### Final selection")
            lines.append("")
            final_selected = final_rec["data"]["selected"]
            if final_selected:
                lines.append("| ident | lane | score |")
                lines.append("|-------|------|-------|")
                for item in final_selected:
                    lines.append(f"| {item['ident']} | {item['lane']} | {item['score']:.4f} |")
                lines.append("")
            else:
                lines.append("(nothing selected)")
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Vault root resolution
# ---------------------------------------------------------------------------


def _resolve_vault_root(vault_arg: str | None) -> str:
    """Resolve vault root.

    Precedence:
      1. --vault CLI argument (used as a path directly if it exists as a dir,
         else passed to vault_config.vault_path as a vault name)
      2. $VAULT_ROOT / $VAULT_PATH env vars
      3. agents.vault_config.vault_path()
    """
    import os
    if vault_arg:
        p = Path(vault_arg)
        if p.is_dir():
            return str(p)
        # treat as vault name
        try:
            from agents import vault_config as vc
            return str(vc.vault_path(vault_arg))
        except Exception:
            return vault_arg

    env = os.environ.get("VAULT_ROOT") or os.environ.get("VAULT_PATH")
    if env:
        return env

    try:
        from agents import vault_config as vc
        return str(vc.vault_path())
    except Exception:
        pass

    raise SystemExit("Cannot resolve vault root -- pass --vault <path>")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _web_config_dir() -> Path:
    """Find .claude/web/ relative to the code repo root."""
    here = Path(__file__).resolve().parent
    # scripts/ -> repo root -> .claude/web/
    candidate = here.parent / ".claude" / "web"
    if candidate.is_dir():
        return candidate
    raise SystemExit(f"Cannot find .claude/web/ (tried {candidate})")


def _print_plan_table(plan: dict) -> None:
    """Print a human-readable table of the plan."""
    targets = plan.get("targets", [])
    notes = plan.get("notes", [])

    print(f"Crawl plan: {len(targets)} targets")
    print()

    for i, t in enumerate(targets, 1):
        lane = t.get("lane", "?")
        tid = t.get("target_id", "?")
        prio = t.get("priority", "?")
        origin = ", ".join(t.get("origin_ids", []))
        queries = t.get("queries", [])
        sources = t.get("routed_sources", [])

        print(f"  [{i}] {tid}  lane={lane}  priority={prio}")
        print(f"       origins: {origin}")
        if queries:
            print(f"       queries[0]: {queries[0][:80]}")
        if sources:
            print(f"       sources: {', '.join(sources[:3])}")
        print()

    if notes:
        print("Notes:")
        for n in notes:
            print(f"  - {n}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Second Brain web DECISION engine -- build a crawl plan."
    )
    sub = parser.add_subparsers(dest="cmd")

    plan_p = sub.add_parser("plan", help="Build and print the crawl plan")
    plan_p.add_argument("--vault", default=None, help="Vault root path or name")
    plan_p.add_argument(
        "--json", action="store_true", dest="as_json", help="Output as JSON"
    )
    plan_p.add_argument(
        "--explain",
        action="store_true",
        default=False,
        help=(
            "Build the plan WITH a DecisionTrace and print the markdown report. "
            "When --json is also set, the trace goes to STDERR; otherwise it goes to STDOUT."
        ),
    )
    plan_p.add_argument(
        "--trace-out",
        default=None,
        metavar="PATH",
        help="Write render_trace_markdown() output to PATH (creates parent dirs).",
    )

    args = parser.parse_args()

    if args.cmd == "plan":
        vault_root = _resolve_vault_root(args.vault)
        web_dir = _web_config_dir()

        config_path = web_dir / "web-config.json"
        if not config_path.exists():
            print(f"ERROR: {config_path} not found", file=sys.stderr)
            return 2
        config = json.loads(config_path.read_text())

        registry = load_registry(web_dir)

        # Build trace only if requested (zero overhead when not requested)
        want_trace = args.explain or bool(args.trace_out)
        trace: DecisionTrace | None = DecisionTrace() if want_trace else None

        plan = build_plan(vault_root, config, registry, trace=trace)

        if args.as_json:
            print(json.dumps(plan, indent=2))
        else:
            _print_plan_table(plan)

        if trace is not None:
            md = render_trace_markdown(trace)
            if args.explain:
                if args.as_json:
                    # --json uses stdout; send trace to stderr
                    print(md, file=sys.stderr)
                else:
                    print(md)
            if args.trace_out:
                out_path = Path(args.trace_out)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(md, encoding="utf-8")
                print(f"[trace] written to {out_path}", file=sys.stderr)

        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
