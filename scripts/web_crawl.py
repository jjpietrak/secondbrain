#!/usr/bin/env python3
"""web_crawl.py -- End-to-end Phase-3A/3B crawl orchestrator for Second Brain v0.2.

Chains web_decision -> web_harvest -> web_rank -> ingest_index to produce a nightly
candidate digest and approval queue entries.

Pipeline (crawl() function):
  1. Load config + registry (via web_decision loaders).  Load PURPOSE text from
     objective/purpose/PURPOSE.md (or vault.yaml fallback).
  2. build_plan() -> crawl targets.
  3. For each target: harvest from all routed FREE sources (rss / papers / github /
     forum).  Network failures degrade gracefully -- skip that source, continue.
  4. Pool + dedup_seen (seen cache NOT written in dry_run).
  5. score_candidates() per lane group against PURPOSE + target evidence.
  6. select_candidates() -- <=5 with lane quotas, spillover, ingest-dedup.
  7. dry_run: return plan/selected summary without writing anything.
     live run: enqueue each selected candidate; write meta/nightly_report/<today>.md.

Routing map (target -> harvest engine):
  - registry entry has rss_feed        -> poll_rss(rss_feed, source_id=<id>)
  - registry paper source by category:
      arxiv_*  / preprint_repository   -> query_papers(q, engine="arxiv")
      semantic_scholar / paper_api     -> query_papers(q, engine="semantic_scholar")
      openalex                         -> query_papers(q, engine="openalex")
      papers_with_code/aggregator      -> poll_rss(rss_feed) if has feed, else skip
  - github-repos entry                 -> poll_github_releases(url)
  - fillable_by includes "forum"       -> query_forum(q, engine="hackernews")
  - research-lane target with EMPTY routed_sources
                                       -> query_papers(q, engine="arxiv") per seed query
    (Note: WebSearch augmentation for research lanes is an agent-level step.)

Nightly report interactive format (one block per selected candidate):

  ### N. Title
  - [ ] approve * `ident`
  - [ ] reject * `ident`
      - reason:
  - **retrieved via**: ENGINE * **source**: SOURCE_ID * **published**: DATE * **score**: 0.0000 * **lane**: LANE
  - **relevance refs**: [[wikilink]], ...
  - **url**: https://...

  **Summary** -- <stripped snippet capped at ~200 words>

  **Relevance** -- <why selected: lane, origin_ids, expected_evidence, score, engine>

  The approve/reject lines are real Obsidian checkboxes. `ident` is the same value
  passed to ingest_index.enqueue (candidate_ident(cand)). The rationale is derived
  deterministically from the candidate snippet (no LLM call) via _content_rationale().
  scripts/report_approve.py (built separately) parses this format to action approvals.

CLI:
  python scripts/web_crawl.py --vault <root> [--dry-run] [--allow-remote-ollama]
                               [--json] [--limit N] [--explain] [--trace-out PATH]
                               [--perplexity] [--no-reformulate]

  --explain         print the full decision trace to STDERR after the run.
  --trace-out       write the rendered trace markdown to PATH (export on request).
  --perplexity      enable Tier-2 Perplexity harvest (GATED: requires
                    paid_scrape.enabled=true in web-config.json AND PERPLEXITY_API_KEY).
                    Refuses with exit 2 if either guard is missing.
  --no-reformulate  force-skip query reformulation (overrides config query.reformulate).
                    Use for testing / back-compat / debugging the seed-query path.

Exit codes:
  0  -- success
  2  -- usage error / Perplexity guard refused
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path shim: ensure scripts/ is importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Lazy imports (monkeypatch-safe: import at call time inside functions)
# ---------------------------------------------------------------------------


def _import_web_decision():
    import web_decision as _wd
    return _wd


def _import_decision_trace():
    """Import DecisionTrace and render_trace_markdown (lazy, monkeypatch-safe)."""
    from web_decision import DecisionTrace as _DT, render_trace_markdown as _RTM
    return _DT, _RTM


def _import_web_harvest():
    import web_harvest as _wh
    return _wh


def _import_web_rank():
    import web_rank as _wr
    return _wr


def _import_ingest_index():
    from agents import ingest_index as _ii
    return _ii


def _import_agent_learn():
    """Lazy import of agent_learn (monkeypatch-safe)."""
    import agent_learn as _al
    return _al


def _import_web_query():
    """Lazy import of web_query (monkeypatch-safe)."""
    import web_query as _wq
    return _wq


# ---------------------------------------------------------------------------
# Config / registry / PURPOSE loaders (reuse web_decision's loaders)
# ---------------------------------------------------------------------------


def _web_config_dir() -> Path:
    """Find .claude/web/ relative to the code repo root."""
    candidate = _REPO_ROOT / ".claude" / "web"
    if candidate.is_dir():
        return candidate
    raise SystemExit(f"Cannot find .claude/web/ (tried {candidate})")


def _load_config() -> dict:
    """Load web-config.json from .claude/web/."""
    web_dir = _web_config_dir()
    config_path = web_dir / "web-config.json"
    if not config_path.exists():
        raise SystemExit(f"web-config.json not found at {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def _load_registry() -> dict:
    """Load all source registry files from .claude/web/sources/."""
    wd = _import_web_decision()
    return wd.load_registry(_web_config_dir())


def _load_purpose(vault_root: str) -> str:
    """Load PURPOSE text; never crashes. Returns '' on any failure.

    Resolution order:
      1. objective/purpose/PURPOSE.md  (primary -- written by research agent)
      2. vault.yaml purpose field      (fallback)
      3. ''                            (safe default)
    """
    vp = Path(vault_root)

    # 1. objective/purpose/PURPOSE.md
    purpose_md = vp / "objective" / "purpose" / "PURPOSE.md"
    if purpose_md.exists():
        try:
            text = purpose_md.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                return text
        except OSError:
            pass

    # 2. vault.yaml -> purpose field
    vault_yaml = vp / "vault.yaml"
    if vault_yaml.exists():
        try:
            import yaml  # type: ignore[import]
            data = yaml.safe_load(vault_yaml.read_text(encoding="utf-8")) or {}
            purp = (data.get("purpose") or "").strip()
            if purp:
                return purp
        except Exception:
            pass

    return ""


def _load_ingest_rows(vault_root: str) -> list[dict]:
    """Load ingest index rows. Returns [] on any failure."""
    try:
        ii = _import_ingest_index()
        vault_name = Path(vault_root).name
        data = ii._load(vault_name)
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
# Routing: resolve registry entries for a target
# ---------------------------------------------------------------------------

# Registry source ids that map to paper API engines
_PAPER_API_ENGINE_MAP: dict[str, str] = {
    "semantic_scholar": "semantic_scholar",
    "openalex": "openalex",
}

# Category -> paper engine mapping
_CATEGORY_TO_ENGINE: dict[str, str] = {
    "preprint_repository": "arxiv",
    "paper_api": "semantic_scholar",   # default; overridden by id below
    "research_aggregator": "",         # use rss_feed if available; else skip
}


def _registry_entry_for_id(source_id: str, registry: dict) -> dict | None:
    """Look up a registry entry by id across all sections."""
    for section_key, section in registry.items():
        list_key = "repositories" if section_key == "github-repos" else "sources"
        for entry in section.get(list_key, []):
            if entry.get("id") == source_id:
                return entry
    return None


def _is_github_entry(source_id: str, registry: dict) -> tuple[bool, str]:
    """Return (True, repo_url) if source_id is in github-repos, else (False, '')."""
    repos = registry.get("github-repos", {}).get("repositories", [])
    for r in repos:
        if r.get("id") == source_id:
            return True, r.get("url", "")
    return False, ""


def _paper_engine_for_entry(entry: dict) -> str:
    """Determine which paper engine to use for a registry paper-publisher entry."""
    entry_id = entry.get("id", "")
    # Explicit id-based overrides
    if entry_id in _PAPER_API_ENGINE_MAP:
        return _PAPER_API_ENGINE_MAP[entry_id]
    # arxiv_* prefix -> arxiv engine
    if entry_id.startswith("arxiv_"):
        return "arxiv"
    # papers_with_code is an aggregator; prefer rss if available
    if entry_id == "papers_with_code":
        return ""  # handled by RSS path below
    # Category fallback
    cat = entry.get("category", "")
    if cat == "preprint_repository":
        return "arxiv"
    if cat == "paper_api":
        return _PAPER_API_ENGINE_MAP.get(entry_id, "semantic_scholar")
    return ""


# arXiv subject-category derivation map: registry entry id -> arXiv cat string
_ARXIV_ID_TO_CATEGORY: dict[str, str] = {
    "arxiv_cs_ar": "cs.AR",
    "arxiv_cs_dc": "cs.DC",
    "arxiv_cs_lg": "cs.LG",
    "arxiv_eess_sp": "eess.SP",
}

import re as _re


def _arxiv_category(entry: dict) -> str | None:
    """Derive the arXiv subject category from a registry paper-publisher entry.

    Derivation order:
    1. Direct lookup in _ARXIV_ID_TO_CATEGORY by ``entry["id"]``.
    2. Parse the ``rss_feed`` or ``url`` field for a path segment like
       ``/list/cs.AR/rss`` or ``/list/eess.SP/recent``.
    3. Return None when no category is derivable (generic arxiv call, deduped
       as before).

    Examples::
        {"id": "arxiv_cs_ar", ...}          -> "cs.AR"
        {"rss_feed": ".../list/cs.DC/rss"}  -> "cs.DC"
        {"id": "openalex", ...}             -> None
    """
    entry_id = entry.get("id", "")
    if entry_id in _ARXIV_ID_TO_CATEGORY:
        return _ARXIV_ID_TO_CATEGORY[entry_id]

    # Try to parse from rss_feed or url
    for field in ("rss_feed", "url"):
        value = entry.get(field) or ""
        m = _re.search(r"/list/([A-Za-z]+\.[A-Z]+)/", value)
        if m:
            return m.group(1)

    return None


# ---------------------------------------------------------------------------
# Harvest dispatcher for a single target
# ---------------------------------------------------------------------------


def _harvest_target(
    target: dict,
    registry: dict,
    purpose: str,
    *,
    per_source_limit: int = 8,
    _harvest_mod=None,  # injected for testing
    trace=None,         # DecisionTrace | None
) -> list[dict]:
    """Harvest candidates for a single plan target.

    Routing logic:
    - Each routed_source id is looked up in the registry.
    - github-repos entry  -> poll_github_releases
    - paper-publisher entry with rss_feed and category=research_aggregator -> poll_rss
    - paper-publisher entry (arxiv/semantic_scholar/openalex) -> query_papers per query
    - blog-newsfeed entry with rss_feed -> poll_rss
    - fillable_by includes "forum" -> query_forum(hackernews) per query (once)
    - research lane with empty routed_sources -> query_papers(arxiv) per seed query

    All network failures are caught and logged; the harvest continues with other sources.
    If trace is provided, records one harvest/target entry with per-engine call details.
    """
    wh = _harvest_mod or _import_web_harvest()

    lane = target.get("lane", "gap")
    queries = target.get("queries", [])
    queries_by_engine = target.get("queries_by_engine", {})
    routed = target.get("routed_sources", [])
    fillable = target.get("fillable_by", [])
    origin_ids = target.get("origin_ids", [])
    target_id = target.get("target_id", "")

    # Helper: return the per-engine query list when queries_by_engine is available
    # and non-empty for the given engine key; else fall back to the flat queries list.
    def _engine_queries(engine_key: str) -> list[str]:
        if queries_by_engine:
            eng_qs = queries_by_engine.get(engine_key, [])
            if eng_qs:
                return eng_qs
        return queries

    candidates: list[dict] = []
    seen_engines: set[str] = set()  # avoid duplicate forum calls per target

    # Dedup harvest calls within this target.
    # Key = (engine, query_or_url, category) where:
    #   - engine: "arxiv" | "rss" | "github" | "hackernews" | ...
    #   - query_or_url: the query string (for query_papers/query_forum) or the
    #     feed/repo URL (for poll_rss / poll_github_releases)
    #   - category: arXiv subject category string (e.g. "cs.AR") or "" for all
    #     other engines and generic arxiv calls without a category.
    #
    # Using a 3-tuple means the same query against DIFFERENT arXiv categories
    # (cs.AR vs cs.DC) runs as two distinct, meaningful API calls, while the
    # same (engine, query, category) triple still deduplicates within the target.
    _executed_calls: set[tuple[str, str, str]] = set()

    # For trace: track each engine call
    _trace_queries: list[dict] = []

    # -- primary query for query-based engines --
    def _q(idx: int = 0) -> str:
        if queries and idx < len(queries):
            return queries[idx]
        return target_id

    # -- tag candidate with lane + origin_ids + expected_evidence --
    expected_evidence = target.get("expected_evidence", "")

    def _tag(c: dict) -> dict:
        c["lane"] = lane
        c["origin_ids"] = list(origin_ids)
        c["expected_evidence"] = expected_evidence
        return c

    # -- safe call wrapper with trace recording and per-target dedup --
    def _call(fn, engine_label: str, query_str: str, *args,
              _arxiv_cat: str | None = None, **kwargs) -> list[dict]:
        # 3-tuple dedup key: (engine, query_or_url, category).
        # category="" for all non-arxiv engines and generic (no-category) arxiv calls.
        call_key = (engine_label, query_str, _arxiv_cat or "")
        if call_key in _executed_calls:
            # Duplicate (engine, key, category) within this target -- skip silently.
            return []
        _executed_calls.add(call_key)
        # Build the trace display string: append "[cat:X]" when a category is used
        # so --explain shows which category each arXiv call targeted.
        trace_query_str = (
            f"{query_str} [cat:{_arxiv_cat}]" if _arxiv_cat else query_str
        )
        try:
            results = fn(*args, **kwargs) or []
            if trace is not None:
                _trace_queries.append({
                    "engine": engine_label,
                    "query": trace_query_str,
                    "n_returned": len(results),
                })
            return results
        except Exception as exc:
            fn_name = getattr(fn, "__name__", repr(fn))
            print(f"[web_crawl] harvest error ({fn_name}): {exc}", file=sys.stderr)
            if trace is not None:
                _trace_queries.append({
                    "engine": engine_label,
                    "query": trace_query_str,
                    "n_returned": 0,
                    "error": str(exc),
                })
            return []

    # --- routed sources ---
    for src_id in routed:
        is_gh, gh_url = _is_github_entry(src_id, registry)
        if is_gh:
            # GitHub releases
            results = _call(
                wh.poll_github_releases, "github", gh_url,
                gh_url, limit=per_source_limit
            )
            candidates.extend(_tag(c) for c in results)
            continue

        entry = _registry_entry_for_id(src_id, registry)
        if entry is None:
            continue

        rss_feed = entry.get("rss_feed") or ""
        cat = entry.get("category", "")

        # blog-newsfeed or conference entry with rss_feed -> RSS
        section_key = None
        for sk, sec in registry.items():
            list_key = "repositories" if sk == "github-repos" else "sources"
            if any(e.get("id") == src_id for e in sec.get(list_key, [])):
                section_key = sk
                break

        if section_key == "blog-newsfeed" or cat in ("research_aggregator", "conference"):
            if rss_feed:
                results = _call(
                    wh.poll_rss, "rss", rss_feed,
                    rss_feed, source_id=src_id, limit=per_source_limit
                )
                candidates.extend(_tag(c) for c in results)
            continue

        # paper-publisher
        if section_key == "paper-publisher":
            paper_engine = _paper_engine_for_entry(entry)
            if paper_engine:
                # Derive the arXiv subject category (None for non-arXiv engines).
                arxiv_cat = _arxiv_category(entry) if paper_engine == "arxiv" else None
                # Use engine-specific queries when available (from reformulation),
                # falling back to the flat queries list (original behaviour).
                engine_key = paper_engine  # "arxiv" | "semantic_scholar" | "openalex"
                paper_qs = _engine_queries(engine_key) or [_q()]
                for q in paper_qs:
                    kw: dict = {"engine": paper_engine, "limit": per_source_limit}
                    if arxiv_cat is not None:
                        kw["category"] = arxiv_cat
                    results = _call(
                        wh.query_papers, paper_engine, q,
                        q, _arxiv_cat=arxiv_cat, **kw
                    )
                    candidates.extend(_tag(c) for c in results)
            elif rss_feed:
                # research_aggregator without a direct paper engine -> use RSS
                results = _call(
                    wh.poll_rss, "rss", rss_feed,
                    rss_feed, source_id=src_id, limit=per_source_limit
                )
                candidates.extend(_tag(c) for c in results)
            continue

    # --- forum fillable_by ---
    if "forum" in fillable and "forum" not in seen_engines:
        seen_engines.add("forum")
        # Use forum-specific queries when available; else fall back to flat queries
        forum_qs = _engine_queries("forum")
        forum_qs = forum_qs[:2] if forum_qs else [_q()]
        for q in forum_qs:
            results = _call(
                wh.query_forum, "hackernews", q,
                q, engine="hackernews", limit=per_source_limit
            )
            candidates.extend(_tag(c) for c in results)

    # --- research lane fallback: empty routed_sources -> arxiv ---
    if lane == "research" and not routed:
        # Use arxiv-specific queries when available; else fall back to flat queries
        research_qs = _engine_queries("arxiv") or [_q()]
        for q in research_qs:
            results = _call(
                wh.query_papers, "arxiv", q,
                q, engine="arxiv", limit=per_source_limit
            )
            candidates.extend(_tag(c) for c in results)
        # Note: WebSearch augmentation for research lanes is an agent-level step,
        # not done here.

    # Record the harvest/target trace entry (AFTER collecting all results, BEFORE
    # the dedup_seen pass that happens in the caller).
    if trace is not None:
        trace.add(
            "harvest",
            "target",
            target_id=target_id,
            lane=lane,
            queries=_trace_queries,
            n_candidates=len(candidates),
            seen_dropped=0,  # filled in by the caller after dedup_seen
        )

    return candidates


# ---------------------------------------------------------------------------
# Seen-cache path
# ---------------------------------------------------------------------------

def _seen_path() -> str:
    """Default seen-cache path: .claude/web/cache/seen.json inside code repo."""
    return str(_REPO_ROOT / ".claude" / "web" / "cache" / "seen.json")


# ---------------------------------------------------------------------------
# Content rationale helper (deterministic, $0 -- no LLM call)
# ---------------------------------------------------------------------------

def _content_rationale(cand: dict) -> str:
    """Derive a 1-2 sentence content rationale from the candidate snippet.

    Steps:
    1. Strip HTML tags from snippet.
    2. Collapse whitespace.
    3. Take the first ~220 chars, trying to break at a sentence boundary (. ! ?).
    4. If snippet is missing/empty, fall back to a lane+origin summary string.

    Returns a plain ASCII-safe string (no markup).
    """
    raw = (cand.get("snippet") or "").strip()
    if raw:
        # Strip HTML tags
        clean = _re.sub(r"<[^>]+>", "", raw)
        # Collapse whitespace (newlines, tabs, multiple spaces)
        clean = _re.sub(r"\s+", " ", clean).strip()
        if clean:
            # Truncate to ~220 chars at sentence boundary if possible
            if len(clean) > 220:
                # Find last sentence-ending punctuation within 220 chars
                m = _re.search(r"[.!?](?=\s|$)", clean[:220])
                if m:
                    clean = clean[: m.end()].strip()
                else:
                    clean = clean[:220].rstrip() + "..."
            return clean

    # Fallback: lane + origin summary
    lane = cand.get("lane") or ""
    origin_ids = cand.get("origin_ids") or []
    origin_str = ", ".join(origin_ids) if origin_ids else "n/a"
    return f"lane={lane}; serves {origin_str}"


# ---------------------------------------------------------------------------
# Content summary helper (deterministic, $0 -- no LLM call)
# ---------------------------------------------------------------------------

def _content_summary(cand: dict) -> str:
    """Return a Summary paragraph describing the source's CONTENT.

    Steps:
    1. Strip HTML tags from snippet.
    2. Collapse whitespace.
    3. Cap at ~200 WORDS on a word boundary.
    4. If snippet is empty or very short (<=5 words), return an honest note.

    Returns a plain ASCII-safe string (no markup).
    """
    raw = (cand.get("snippet") or "").strip()
    if raw:
        # Strip HTML tags
        clean = _re.sub(r"<[^>]+>", "", raw)
        # Collapse whitespace (newlines, tabs, multiple spaces)
        clean = _re.sub(r"\s+", " ", clean).strip()
        if clean:
            words = clean.split()
            if len(words) >= 5:
                if len(words) > 200:
                    clean = " ".join(words[:200])
                    # Try to end at a sentence boundary within those 200 words
                    m = _re.search(r"[.!?](?=\s|$)", clean)
                    if m:
                        clean = clean[: m.end()].strip()
                    else:
                        clean = clean.rstrip(",;:") + "..."
                return clean

    return (
        "No abstract/snippet retrieved -- fetch the URL to summarize."
    )


# ---------------------------------------------------------------------------
# Relevance paragraph helper (deterministic, $0 -- no LLM call)
# ---------------------------------------------------------------------------

def _relevance_paragraph(cand: dict) -> str:
    """Return a Relevance paragraph explaining WHY the web agent selected this candidate.

    Composed from: lane, origin_ids, expected_evidence, score, engine, source_id.
    One readable paragraph, ASCII only (no Unicode dashes or curly quotes).

    Example shape:
      "Selected for the gap lane to serve GAP-01. The web agent sought:
       Papers describing P/D disaggregation with latency measurements.
       Ranked by semantic relevance to the vault PURPOSE (score 0.850);
       retrieved via arxiv from arxiv_cs_dc."
    """
    lane = (cand.get("lane") or "").strip()
    origin_ids = cand.get("origin_ids") or []
    expected_evidence = (cand.get("expected_evidence") or "").strip()
    score = float(cand.get("score", 0.0))
    engine = (cand.get("engine") or "").strip()
    source_id = (cand.get("source_id") or "").strip()

    origin_str = ", ".join(origin_ids) if origin_ids else "n/a"
    evidence_str = expected_evidence if expected_evidence else "evidence to close this gap"
    # Strip trailing period from evidence_str to avoid double period
    evidence_str = evidence_str.rstrip(".")

    parts: list[str] = [
        f"Selected for the {lane} lane to serve {origin_str}.",
        f"The web agent sought: {evidence_str}.",
        f"Ranked by semantic relevance to the vault PURPOSE (score {score:.3f});",
        f"retrieved via {engine} from {source_id}.",
    ]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Relevance links helper (inline copy of ingest_index._relevance_links logic,
# so web_crawl has no circular import risk; kept in sync with the real impl)
# ---------------------------------------------------------------------------

_RL_GAP_RE = _re.compile(r"^GAP-\d+$", _re.IGNORECASE)
_RL_OBJ_RE = _re.compile(
    r"^(DIR|Q|T|D|QP)-(\d{4,})$", _re.IGNORECASE
)
_RL_OBJ_SUBDIR = {
    "DIR": "direction",
    "Q": "question",
    "T": "topic",
    "D": "decision",
    "QP": "question",
}


def _relevance_links(origin_ids: list, vault_root: str) -> str:
    """Render origin_ids as Obsidian wikilinks for the nightly report.

    Mirrors ingest_index._relevance_links logic; never raises; returns "" when empty.
    """
    if not origin_ids:
        return ""
    parts: list[str] = []
    obj_root = Path(vault_root) / "objective"
    gap_dir = Path(vault_root) / "wiki" / "gap"
    for oid in origin_ids:
        oid = oid.strip()
        if not oid:
            continue
        if _RL_GAP_RE.match(oid):
            oid_upper = oid.upper()
            try:
                matches = list(gap_dir.glob(f"{oid_upper}-*.md"))
            except OSError:
                matches = []
            if matches:
                stem = matches[0].stem
                parts.append(f"[[wiki/gap/{stem}]]")
            else:
                parts.append(f"[[wiki/gap/index]] ({oid_upper})")
            continue
        m = _RL_OBJ_RE.match(oid)
        if m:
            prefix = m.group(1).upper()
            subdir = _RL_OBJ_SUBDIR.get(prefix)
            if subdir:
                subdir_path = obj_root / subdir
                try:
                    matches = list(subdir_path.glob(f"{oid}-*.md"))
                    if matches:
                        stem = matches[0].stem
                        parts.append(f"[[objective/{subdir}/{stem}]]")
                        continue
                except OSError:
                    pass
            parts.append(f"[[{oid}]]")
            continue
        parts.append(f"[[{oid}]]")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Nightly report writer
# ---------------------------------------------------------------------------

_REPORT_FRONTMATTER = """\
---
type: nightly_report
date: {today}
written_by: web
phase: 3a
---

# Nightly Crawl Report -- {today}

"""

_REPORT_INSTRUCTION = (
    "Tick **approve** to ingest, or **reject** (optional reason). "
    "Then run `/wiki-approve`, or it is read on the next nightly run. "
    "Both blank = decide later."
)

_REPORT_FOOTER = (
    "\n---\n\n"
    "User: approve 0-5 via `python -m agents.ingest_index approve <id>` / reject with "
    "`reject <id> --reason ...`\n"
)

_CANDIDATE_BLOCK = """\
### {n}. {title}
- [ ] approve \xb7 `{ident}`
- [ ] reject \xb7 `{ident}`
    - reason:
- **retrieved via**: {engine} \xb7 **source**: {source_id} \xb7 **published**: {published} \xb7 **score**: {score:.4f} \xb7 **lane**: {lane}
- **relevance refs**: {relevance_links}
- **url**: {url}

**Summary** — {content_summary}

**Relevance** — {relevance_paragraph}
"""


def _write_nightly_report(
    vault_root: str,
    today: str,
    selected: list[dict],
    trace=None,  # DecisionTrace | None
    briefing: str = "",
) -> Path:
    """Write meta/nightly_report/<today>.md with one interactive block per candidate.

    Format (pinned -- scripts/report_approve.py parses the checkbox lines):
      ### N. Title
      - [ ] approve * `ident`
      - [ ] reject * `ident`
          - reason:
      - **retrieved via**: ENGINE * **source**: SOURCE_ID * **published**: DATE
        * **score**: 0.0000 * **lane**: LANE
      - **relevance refs**: [[wikilink]], ...
      - **url**: https://...

      **Summary** -- <content summary paragraph>

      **Relevance** -- <relevance paragraph>

    When ``briefing`` is non-empty, a ``## Learning briefing`` section is inserted
    at the TOP of the body (right after the intro instruction line, BEFORE the first
    ``### N.`` candidate block).  report_approve.py already stops at ``## Decision
    trace`` and keys on the candidate checkbox lines, so a top briefing section does
    NOT affect parsing.

    The approve/reject/reason lines are UNCHANGED from the previous format so
    report_approve.py continues to parse them correctly.
    Frontmatter and '## Decision trace' section are preserved unchanged.
    If trace is provided, a '## Decision trace' section is appended.
    """
    from web_harvest import candidate_ident as _cand_ident

    report_dir = Path(vault_root) / "meta" / "nightly_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{today}.md"

    # Build candidate blocks
    if selected:
        blocks: list[str] = [_REPORT_INSTRUCTION + "\n\n"]
        # Embed the learning briefing BEFORE the first candidate block
        if briefing:
            blocks.append(briefing + "\n\n")
        for n, c in enumerate(selected, start=1):
            ident = _cand_ident(c)
            origin_ids = c.get("origin_ids") or []
            published = (c.get("published") or "").strip() or "—"
            score = float(c.get("score", 0.0))
            lane = c.get("lane", "")
            engine = (c.get("engine") or "").strip()
            source_id = (c.get("source_id") or "").strip()
            rel_links = _relevance_links(origin_ids, vault_root)
            content_summary = _content_summary(c)
            relevance_para = _relevance_paragraph(c)
            url = c.get("url", "")
            title = c.get("title", "(no title)")
            blocks.append(
                _CANDIDATE_BLOCK.format(
                    n=n,
                    title=title,
                    ident=ident,
                    engine=engine,
                    source_id=source_id,
                    published=published,
                    score=score,
                    lane=lane,
                    relevance_links=rel_links,
                    content_summary=content_summary,
                    relevance_paragraph=relevance_para,
                    url=url,
                )
            )
        body = "\n".join(blocks)
    else:
        body = "_No candidates selected._"

    content = _REPORT_FRONTMATTER.format(today=today) + body + _REPORT_FOOTER

    # Append the decision trace section if a trace was provided
    if trace is not None:
        try:
            _, _render = _import_decision_trace()
            trace_md = _render(trace)
            content = content + "\n## Decision trace\n\n" + trace_md + "\n"
        except Exception as exc:
            print(f"[web_crawl] could not render trace: {exc}", file=sys.stderr)

    report_path.write_text(content, encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# Main crawl() function
# ---------------------------------------------------------------------------


def crawl(
    vault_root: str,
    *,
    dry_run: bool = False,
    allow_remote_ollama: bool = False,
    today: str | None = None,
    per_source_limit: int = 8,
    trace=None,  # DecisionTrace | None -- if None, one is created internally
    use_perplexity: bool = False,
    use_reformulate: bool | None = None,
) -> dict:
    """End-to-end Phase-3A/3B crawl pipeline.

    Parameters
    ----------
    vault_root:
        Absolute path to the vault root directory.
    dry_run:
        When True, return the plan and ranked candidates without writing to the
        ingest index or nightly report.
    allow_remote_ollama:
        Pass-through to score_candidates -- allows a remote OLLAMA_URL.
    today:
        ISO-8601 date string (YYYY-MM-DD) for recency scoring and report filename.
        Defaults to today's date.
    per_source_limit:
        Maximum candidates to fetch per source / per query call.
    trace:
        Optional DecisionTrace to record into. When None (the default), a fresh
        DecisionTrace is created internally so the nightly report always contains
        the full decision audit. Pass an existing trace to compose with a caller's
        trace context.
    use_perplexity:
        When True, attempt Tier-2 Perplexity harvest after the free Tier-0 harvest.
        GATED: requires BOTH paid_scrape.enabled=true in web-config.json AND
        PERPLEXITY_API_KEY set in the environment. If either guard is missing the
        function prints a clear message to stderr and raises SystemExit(2) -- no
        silent free-only fallback, no silent spend.
        When False (default), behaves exactly as Phase-3A (free Tier-0 only).
    use_reformulate:
        Override the config's query.reformulate flag.  None (default) means read from
        config.  True forces reformulation on; False forces it off (--no-reformulate).

    Returns
    -------
    dry_run=True:
        {"plan": <plan dict>, "selected": <list>, "would_write": "<date>.md",
         "trace": <DecisionTrace>}
    dry_run=False:
        {"selected": <list>, "report_path": <str>, "enqueued": [<id>, ...],
         "trace": <DecisionTrace>}
    """
    today_s = today or date.today().isoformat()

    # Always ensure a trace exists (zero-overhead when not rendered)
    _DT, _render_trace = _import_decision_trace()
    if trace is None:
        trace = _DT()

    # 1. Load config, registry, PURPOSE, ingest rows
    config = _load_config()
    registry = _load_registry()
    purpose_text = _load_purpose(vault_root)
    ingest_rows = _load_ingest_rows(vault_root)

    # 1b. Run agent_learn at crawl start (reads PREVIOUS run outcomes, updates learned.json)
    #     Degrades gracefully: on any error or if learn.enabled is false, set learned={}.
    learned: dict = {}
    briefing: str = ""
    if config.get("learn", {}).get("enabled"):
        try:
            al = _import_agent_learn()
            _apply = not dry_run
            learn_result = al.learn(vault_root, "web", apply=_apply, today=today_s)
            learned = learn_result.get("learned", {})
            briefing = learn_result.get("briefing", "")
        except Exception as exc:
            print(f"[web_crawl] agent_learn failed (degrading gracefully): {exc}", file=sys.stderr)
            learned = {}
            briefing = ""

    # 2. Build plan (threads trace into parse/merge/lanes/route steps)
    wd = _import_web_decision()
    plan = wd.build_plan(vault_root, config, registry, trace=trace, learned=learned)
    targets = plan.get("targets", [])

    # 2b. Query reformulation (Phase 4A step-2, Design B)
    #     Runs AFTER build_plan (which supplies seed queries) and AFTER agent_learn
    #     (which supplies learned reject keywords).  Gated by:
    #       - config.query.reformulate  (true/false, default false)
    #       - use_reformulate param     (None = follow config, True/False = override)
    #     --no-reformulate / use_reformulate=False always skips regardless of config.
    #     Runs in both real and dry_run (read-only, cheap; preview value in dry-run).
    _do_reformulate: bool
    if use_reformulate is False:
        _do_reformulate = False
    elif use_reformulate is True:
        _do_reformulate = True
    else:
        _do_reformulate = bool(config.get("query", {}).get("reformulate", False))

    if _do_reformulate and targets:
        try:
            wq = _import_web_query()
            _use_llm = bool(config.get("query", {}).get("use_llm", True))
            _max_per_engine = int(config.get("query", {}).get("max_queries_per_engine", 3))
            reformulated = wq.reformulate(
                targets,
                purpose=purpose_text,
                vault_root=vault_root,
                learned=learned or None,
                use_llm=_use_llm,
                agent="web",
                max_per_engine=_max_per_engine,
            )
            # Record per-target reformulation in the trace
            for orig_t, ref_t in zip(targets, reformulated):
                ref_rec = ref_t.get("reformulation", {})
                trace.add(
                    "reformulate",
                    "target",
                    target_id=ref_t.get("target_id", ""),
                    method=ref_rec.get("method", "fallback"),
                    old_queries=ref_rec.get("old_queries", []),
                    new_by_engine=ref_t.get("queries_by_engine", {}),
                    rationale=ref_rec.get("rationale", ""),
                )
            targets = reformulated
        except Exception as _ref_exc:
            print(
                f"[web_crawl] query reformulation failed (degrading gracefully): {_ref_exc}",
                file=sys.stderr,
            )
            # targets unchanged -- keep original seed queries

    # 3. Harvest: one call per target; failures degrade gracefully
    #    Track per-target candidate ids so we can compute seen_dropped after dedup.
    wh = _import_web_harvest()
    all_candidates: list[dict] = []
    # Map target_id -> index of the harvest/target trace record
    # (so we can back-fill seen_dropped after dedup_seen).
    _target_cand_counts: list[tuple[str, int, int]] = []  # (target_id, start_idx, end_idx)

    for target in targets:
        start_idx = len(all_candidates)
        cands = _harvest_target(
            target,
            registry,
            purpose_text,
            per_source_limit=per_source_limit,
            trace=trace,
        )
        all_candidates.extend(cands)
        end_idx = len(all_candidates)
        _target_cand_counts.append((target.get("target_id", ""), start_idx, end_idx))

    # 3b. Tier-2 Perplexity harvest (GATED, opt-in via use_perplexity=True)
    #
    # Guard: BOTH paid_scrape.enabled must be True in web-config.json AND
    # PERPLEXITY_API_KEY must be present in the environment. Either guard missing
    # -> refuse with exit 2 (no silent fallback, no silent spend).
    if use_perplexity:
        paid_cfg = config.get("paid_scrape", {})
        enabled = paid_cfg.get("enabled", False)
        api_key = os.environ.get("PERPLEXITY_API_KEY", "")

        if not enabled:
            print(
                "[web_crawl] Perplexity requested but paid_scrape.enabled is false "
                "in web-config -- refusing (flip paid_scrape.enabled to true to proceed)",
                file=sys.stderr,
            )
            raise SystemExit(2)

        if not api_key:
            print(
                "[web_crawl] Perplexity requested but no PERPLEXITY_API_KEY in environment "
                "-- refusing (set PERPLEXITY_API_KEY to proceed)",
                file=sys.stderr,
            )
            raise SystemExit(2)

        # Allocation: select the top max_calls_per_crawl targets by priority order:
        # gap lane (high > med > low), then research, then news.
        # TODO: smarter Perplexity target scheduling (TBD) -- e.g. prioritise targets
        #       whose gap has been open longest, or whose last Perplexity call was oldest.
        max_calls = int(paid_cfg.get("max_calls_per_crawl", 2))
        _LANE_ORDER = {"gap": 0, "research": 1, "news": 2}
        _PRIORITY_ORDER = {"high": 0, "med": 1, "medium": 1, "low": 2}

        def _target_sort_key(t: dict) -> tuple:
            lane_rank = _LANE_ORDER.get(t.get("lane", "news"), 3)
            pri_rank = _PRIORITY_ORDER.get((t.get("priority") or "low").lower(), 2)
            return (lane_rank, pri_rank)

        sorted_targets = sorted(targets, key=_target_sort_key)
        perplexity_targets = sorted_targets[:max_calls]

        # Import cost_tracker lazily (monkeypatch-safe)
        try:
            from agents import cost_tracker as _ct
        except ImportError:
            _ct = None  # type: ignore[assignment]

        for ptarget in perplexity_targets:
            pqueries = ptarget.get("queries", [])
            primary_query = pqueries[0] if pqueries else ptarget.get("target_id", "")
            if not primary_query:
                continue

            try:
                pcands_raw = wh.query_perplexity(primary_query, limit=per_source_limit)
            except Exception as exc:
                print(
                    f"[web_crawl] Perplexity harvest error for {ptarget.get('target_id')!r}: "
                    f"{exc}",
                    file=sys.stderr,
                )
                pcands_raw = []

            # Tag candidates with lane, origin_ids, expected_evidence (same as Tier-0)
            plane = ptarget.get("lane", "gap")
            porigin_ids = ptarget.get("origin_ids", [])
            pexpected_evidence = ptarget.get("expected_evidence", "")
            pcands: list[dict] = []
            for c in pcands_raw:
                c = dict(c)
                c["lane"] = plane
                c["origin_ids"] = list(porigin_ids)
                c["expected_evidence"] = pexpected_evidence
                pcands.append(c)

            all_candidates.extend(pcands)

            # Cost logging: one ledger row per Perplexity call.
            # Perplexity Sonar does not return token counts in the citation response;
            # record with best-effort zeros so spend is tracked (model=sonar).
            if _ct is not None:
                try:
                    _ct.record(
                        action="web-scrape-perplexity",
                        role="harvest",
                        provider="perplexity",
                        model="sonar",
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=0.0,  # best-effort: actual cost unknown without token counts
                        source="pay-as-you-go",
                        # estimated_cost_usd omitted -> estimate_cost("sonar", 0, 0) = 0
                    )
                except Exception as exc:
                    print(
                        f"[web_crawl] cost_tracker record failed: {exc}",
                        file=sys.stderr,
                    )

            # Trace: record the Perplexity call in the harvest section so --explain shows it
            if trace is not None:
                trace.add(
                    "harvest",
                    "target",
                    target_id=ptarget.get("target_id", ""),
                    lane=plane,
                    queries=[
                        {
                            "engine": "perplexity",
                            "query": primary_query,
                            "n_returned": len(pcands_raw),
                        }
                    ],
                    n_candidates=len(pcands),
                    seen_dropped=0,
                )

    # 4. Dedup seen. In-run dedup (collapsing duplicates harvested within THIS run)
    #    always applies; cross-run PERSISTENCE is narrowed to actually-staged items
    #    (see step 6b) so un-staged candidates can resurface on later runs.
    seen_p = _seen_path()
    new_ids_set: set[str] = set()
    new_candidates, updated_seen = wh.dedup_seen(all_candidates, seen_path=seen_p)
    # Compute the set of stable ids that survived dedup
    from web_harvest import _stable_id as _wh_stable_id, candidate_ident as _candidate_ident
    new_ids_set = {_wh_stable_id(c) for c in new_candidates}
    # Reconstruct the pre-run cross-run seen set (updated_seen minus this run's
    # newly-harvested ids) so we persist old seen + newly-staged only.
    prior_seen_keys = set(updated_seen.keys()) - new_ids_set

    # Back-fill seen_dropped into each harvest/target trace record
    harvest_recs = trace.find("harvest", "target")
    for (target_id, start_idx, end_idx), hrec in zip(_target_cand_counts, harvest_recs):
        target_cands = all_candidates[start_idx:end_idx]
        seen_dropped = sum(
            1 for c in target_cands
            if _wh_stable_id(c) not in new_ids_set
        )
        hrec["data"]["seen_dropped"] = seen_dropped

    # 5. Rank: score all candidates against PURPOSE + per-candidate evidence
    wr = _import_web_rank()
    rank_query = purpose_text
    scored_pool = wr.score_candidates(
        new_candidates,
        rank_query,
        registry=registry,
        allow_remote_ollama=allow_remote_ollama,
        today=today_s,
        learned=learned or None,
        weights=config.get("learn") if learned else None,
        category_weights=config.get("category_weights"),
    )

    # Record the rank/scores trace step
    # Determine path: check whether ollama embedding was available
    try:
        from rerank import ollama_url as _olu, ollama_alive as _ola, DEFAULT_MODEL as _dm
        _olu_url = _olu(allow_remote_ollama)
        _alive, _models = _ola(_olu_url)
        rank_path = "embedding" if (_alive and _dm in _models) else "fallback"
    except Exception:
        rank_path = "fallback"

    trace.add(
        "rank",
        "scores",
        path=rank_path,
        candidates=[
            {"ident": _candidate_ident(c), "score": float(c.get("score", 0.0))}
            for c in scored_pool
        ],
        n=len(scored_pool),
    )

    # 6. Select (threads trace into selection steps)
    selected = wd.select_candidates(scored_pool, config, ingest_rows=ingest_rows, trace=trace)

    # 6b. Persist the seen cache with ONLY the items actually staged this run
    #     (prior cross-run seen + newly-staged). Un-staged candidates are left out
    #     so they can resurface on later runs. Dry runs never write.
    staged_ids = {_wh_stable_id(c) for c in selected if _wh_stable_id(c)}
    if not dry_run and staged_ids:
        persisted_seen = {k: True for k in prior_seen_keys}
        for sid in staged_ids:
            persisted_seen[sid] = True
        try:
            sp = Path(seen_p)
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(
                json.dumps(persisted_seen, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"[web_crawl] could not write seen cache: {exc}", file=sys.stderr)

    # 7. dry_run early return
    if dry_run:
        return {
            "plan": plan,
            "selected": selected,
            "would_write": f"{today_s}.md",
            "trace": trace,
        }

    # 8. Enqueue each selected candidate and write nightly report
    ii = _import_ingest_index()
    enqueued_ids: list[str] = []

    for c in selected:
        ident = _candidate_ident(c)
        if not ident:
            continue
        title = c.get("title", "")
        origin_ids = c.get("origin_ids") or []
        # Compact one-liner for the ingest_index Rationale column
        # (cap at ~200 chars so the column stays readable)
        summary_text = _content_summary(c)
        rationale = summary_text[:200].rstrip() if len(summary_text) > 200 else summary_text
        score = float(c.get("score", 0.0))
        published = c.get("published") or None

        # Resolve the working URL to pass to enqueue.
        # For arXiv candidates whose url field is empty, derive from ident.
        enqueue_url = c.get("url") or ""
        if not enqueue_url and ident.startswith("arxiv:"):
            arxiv_id = ident[len("arxiv:"):]
            enqueue_url = f"https://arxiv.org/abs/{arxiv_id}"

        try:
            row = ii.enqueue(
                ident,
                title=title,
                rationale=rationale,
                score=score,
                discovered_by="web",
                objective_ids=list(origin_ids),
                published=published,
                url=enqueue_url,
                source_id=c.get("source_id", ""),
                engine=c.get("engine", ""),
            )
            enqueued_ids.append(row.get("id", ident))
        except Exception as exc:
            print(f"[web_crawl] enqueue({ident!r}) failed: {exc}", file=sys.stderr)

    # Write nightly report (with embedded decision trace and learning briefing)
    report_path = _write_nightly_report(
        vault_root, today_s, selected, trace=trace, briefing=briefing
    )

    return {
        "selected": selected,
        "report_path": str(report_path),
        "enqueued": enqueued_ids,
        "trace": trace,
    }


# ---------------------------------------------------------------------------
# Vault root resolution (mirrors web_decision._resolve_vault_root)
# ---------------------------------------------------------------------------


def _resolve_vault_root(vault_arg: str | None) -> str:
    """Resolve vault root.

    Precedence:
      1. --vault CLI argument (used as path if it is an existing dir, else vault name)
      2. $VAULT_ROOT / $VAULT_PATH env vars
      3. agents.vault_config.vault_path()
    """
    import os
    if vault_arg:
        p = Path(vault_arg)
        if p.is_dir():
            return str(p)
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    python scripts/web_crawl.py --vault <root> [--dry-run] [--allow-remote-ollama]
                                  [--json] [--limit N] [--explain] [--trace-out PATH]
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description=(
            "Second Brain Phase-3A FREE crawl orchestrator. "
            "Chains web_decision -> web_harvest -> web_rank -> ingest_index."
        )
    )
    parser.add_argument("--vault", default=None, help="Vault root path or name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Plan and rank but do not write to the ingest index or nightly report.",
    )
    parser.add_argument(
        "--allow-remote-ollama",
        action="store_true",
        dest="allow_remote_ollama",
        help="Allow a remote OLLAMA_URL for embedding (potential data exfil).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the result dict as JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=8,
        dest="per_source_limit",
        metavar="N",
        help="Max candidates per source / per query call (default: 8).",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        dest="explain",
        help=(
            "After the run, print the full decision trace (harvest + rank + selection) "
            "to STDERR. Stdout remains the normal JSON/summary output."
        ),
    )
    parser.add_argument(
        "--trace-out",
        default=None,
        dest="trace_out",
        metavar="PATH",
        help="Write the rendered decision trace to PATH (creates parent dirs).",
    )
    parser.add_argument(
        "--perplexity",
        action="store_true",
        dest="use_perplexity",
        help=(
            "Enable Tier-2 Perplexity harvest (GATED: requires paid_scrape.enabled=true "
            "in .claude/web/web-config.json AND PERPLEXITY_API_KEY set; exits 2 if either "
            "is missing). Spends real $ capped at max_calls_per_crawl; cost-logged."
        ),
    )
    parser.add_argument(
        "--no-reformulate",
        action="store_true",
        dest="no_reformulate",
        default=False,
        help=(
            "Force-skip query reformulation regardless of config. "
            "Use for testing or to ensure the exact seed-query path."
        ),
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 2

    vault_root = _resolve_vault_root(args.vault)

    result = crawl(
        vault_root,
        dry_run=args.dry_run,
        allow_remote_ollama=args.allow_remote_ollama,
        today=date.today().isoformat(),
        per_source_limit=args.per_source_limit,
        use_perplexity=args.use_perplexity,
        use_reformulate=False if args.no_reformulate else None,
    )

    trace = result.get("trace")

    if args.as_json:
        # Exclude trace from JSON output (not JSON-serializable cleanly)
        out = {k: v for k, v in result.items() if k != "trace"}
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    else:
        selected = result.get("selected", [])
        if args.dry_run:
            print(
                f"[dry-run] plan: {len(result.get('plan', {}).get('targets', []))} targets, "
                f"{len(selected)} selected, "
                f"would write: {result.get('would_write', '')}"
            )
        else:
            print(
                f"[crawl] {len(selected)} candidates enqueued, "
                f"report: {result.get('report_path', '')}"
            )
        for c in selected:
            score = c.get("score", 0.0)
            print(
                f"  [{c.get('lane', '?'):<8}] score={score:.3f}  "
                f"{c.get('title', '')[:70]}"
            )

    # --explain: print trace to STDERR
    if args.explain and trace is not None:
        try:
            _, _render_trace = _import_decision_trace()
            print(_render_trace(trace), file=sys.stderr)
        except Exception as exc:
            print(f"[web_crawl] could not render trace: {exc}", file=sys.stderr)

    # --trace-out: write trace to file
    if args.trace_out and trace is not None:
        try:
            _, _render_trace = _import_decision_trace()
            trace_md = _render_trace(trace)
            out_path = Path(args.trace_out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(trace_md, encoding="utf-8")
            print(f"[trace] written to {out_path}", file=sys.stderr)
        except Exception as exc:
            print(f"[web_crawl] could not write trace to {args.trace_out}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
