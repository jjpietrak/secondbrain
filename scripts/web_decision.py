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
# Parse gaps.md
# ---------------------------------------------------------------------------

_PRIORITY_RE = re.compile(r"^(high|medium|low)\b", re.IGNORECASE)
_TOPIC_RE = re.compile(r"T-\d+")
_FILLABLE_TAG_RE = re.compile(
    r"\b(arxiv|web|github|forum|x)\b", re.IGNORECASE
)


def parse_gaps(gaps_md_text: str) -> list[dict]:
    """Parse the ## Knowledge Gaps section of gaps.md.

    Each gap is a block starting with ### GAP-NN: <title>.
    Returns list of dicts with keys:
      id, title, shows_up_in, missing, fillable_by, topics, priority
    """
    # Find the ## Knowledge Gaps section
    knowledge_gaps_match = re.search(
        r"^##\s+Knowledge Gaps\s*$", gaps_md_text, re.MULTILINE
    )
    if not knowledge_gaps_match:
        return []

    section_text = gaps_md_text[knowledge_gaps_match.end():]

    # Stop at the next ## heading (e.g. ## Stale, ## Self-contained)
    next_section = re.search(r"^##\s+", section_text, re.MULTILINE)
    if next_section:
        section_text = section_text[: next_section.start()]

    gaps = []
    # Split into blocks at ### GAP-NN:
    blocks = re.split(r"(?=^###\s+GAP-)", section_text, flags=re.MULTILINE)

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        header_match = re.match(r"^###\s+(GAP-\d+):\s*(.+)$", block, re.MULTILINE)
        if not header_match:
            continue

        gap_id = header_match.group(1)
        title = header_match.group(2).strip()

        def _field(name: str) -> str:
            m = re.search(rf"^-\s+{name}:\s*(.+?)$", block, re.MULTILINE | re.DOTALL)
            if not m:
                return ""
            # Trim to just this field's line (stop at next "- key:")
            val = m.group(1)
            # Stop at the next bullet field
            stop = re.search(r"\n-\s+\w", val)
            if stop:
                val = val[: stop.start()]
            return val.strip()

        shows_up_in = _field("shows_up_in")
        missing = _field("missing")
        fillable_by_raw = _field("fillable_by")
        topic_raw = _field("topic")
        priority_raw = _field("priority")

        # Parse fillable_by: extract bare engine tags
        fillable_by = list(
            dict.fromkeys(  # deduplicate while preserving order
                t.lower() for t in _FILLABLE_TAG_RE.findall(fillable_by_raw)
            )
        )

        # Parse topics: T-NNNN comma list
        topics = _TOPIC_RE.findall(topic_raw)

        # Priority: first word (high/medium/low)
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


def parse_directions(direction_dir: str) -> list[dict]:
    """Read all DIR-*.md files (skip _template + status != open).

    Returns list of dicts with keys:
      id, serves_question, topics, targets_gap, priority, status,
      seed_queries, expected_evidence, solves_when
    """
    d = Path(direction_dir)
    if not d.is_dir():
        return []

    directions = []
    for path in sorted(d.glob("DIR-*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        fm = _parse_frontmatter(text)

        status = fm.get("status", "").strip().lower()
        if status != "open":
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


def merge_targets(gaps: list[dict], directions: list[dict]) -> list[dict]:
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

    for score, gap, direction in scored_pairs:
        if score < MERGE_THRESHOLD:
            break  # remaining scores are all lower (list is sorted)
        if gap["id"] in consumed_gaps or direction["id"] in consumed_dirs:
            continue
        merges.append((gap, direction))
        consumed_gaps.add(gap["id"])
        consumed_dirs.add(direction["id"])

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


def assign_lanes(merged_targets: list[dict]) -> dict:
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


def route_to_sources(target: dict, registry: dict) -> list[str]:
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
                if s["id"] not in seen:
                    result_ids.append(s["id"])
                    seen.add(s["id"])

        elif tag == "github":
            repos = registry.get("github-repos", {}).get("repositories", [])
            repos_sorted = sorted(repos, key=lambda s: -int(s.get("relevance", 0)))
            for s in repos_sorted:
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
                if s["id"] not in seen:
                    result_ids.append(s["id"])
                    seen.add(s["id"])

        # forum / x: skip (API engines handle these)

    return result_ids[:5]


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


def build_plan(vault_root: str, config: dict, registry: dict) -> dict:
    """Orchestrate parse -> merge -> lanes -> news -> route.

    Returns {"targets": [...ordered: gap, research, news...], "config": config, "notes": [...]}.
    """
    vault_path = Path(vault_root)
    notes = []

    # Parse
    gaps_path = vault_path / "wiki" / "gaps.md"
    if gaps_path.exists():
        gaps = parse_gaps(gaps_path.read_text(encoding="utf-8", errors="ignore"))
        notes.append(f"parsed {len(gaps)} gaps from wiki/gaps.md")
    else:
        gaps = []
        notes.append("wiki/gaps.md not found -- no gaps loaded")

    direction_dir = vault_path / "objective" / "direction"
    directions = parse_directions(str(direction_dir))
    notes.append(f"parsed {len(directions)} open directions from objective/direction/")

    # Merge
    merged = merge_targets(gaps, directions)
    notes.append(f"merged into {len(merged)} targets ({sum(1 for t in merged if t['lane']=='gap')} gap, {sum(1 for t in merged if t['lane']=='research')} research)")

    # Assign lanes
    lanes = assign_lanes(merged)

    # Route to sources
    for target in lanes["gap"] + lanes["research"]:
        target["routed_sources"] = route_to_sources(target, registry)

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
) -> list[dict]:
    """Pick <=5 final candidates by typed budget.

    Steps:
    1. ingest-dedup: drop candidates whose source_id/url/id is ingested/pending/rejected.
    2. Group by lane, sort each lane by score desc.
    3. Fill each lane up to its quota.
    4. Spillover: underfill donates spare slots to the next lane in spillover_order.
    5. Cap total at config.new_sources_total.

    Each returned candidate is tagged with its lane.
    """
    # Build dedup sets
    dedup_source_ids: set[str] = set()
    dedup_urls: set[str] = set()

    if ingest_rows:
        for row in ingest_rows:
            status = row.get("status", "")
            if status in ("ingested", "pending", "rejected"):
                sid = row.get("id") or row.get("source_id") or ""
                url = row.get("url") or ""
                if sid:
                    dedup_source_ids.add(sid)
                if url:
                    dedup_urls.add(url.rstrip("/"))

    def _is_duped(c: dict) -> bool:
        sid = c.get("source_id") or c.get("id") or ""
        url = (c.get("url") or "").rstrip("/")
        return sid in dedup_source_ids or (url and url in dedup_urls)

    filtered = [c for c in candidates if not _is_duped(c)]

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
            added += 1

    # Final cap
    selected = selected[:total_cap]
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

        plan = build_plan(vault_root, config, registry)

        if args.as_json:
            print(json.dumps(plan, indent=2))
        else:
            _print_plan_table(plan)

        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
