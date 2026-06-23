#!/usr/bin/env python3
"""web_crawl.py -- End-to-end Phase-3A FREE ($0) crawl orchestrator for Second Brain v0.2.

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

CLI:
  python scripts/web_crawl.py --vault <root> [--dry-run] [--allow-remote-ollama]
                               [--json] [--limit N] [--explain] [--trace-out PATH]

  --explain      print the full decision trace to STDERR after the run.
  --trace-out    write the rendered trace markdown to PATH (export on request).

Exit codes:
  0  -- success
  2  -- usage error
"""

from __future__ import annotations

import argparse
import json
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
    routed = target.get("routed_sources", [])
    fillable = target.get("fillable_by", [])
    origin_ids = target.get("origin_ids", [])
    target_id = target.get("target_id", "")

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

    # -- tag candidate with lane + origin_ids --
    def _tag(c: dict) -> dict:
        c["lane"] = lane
        c["origin_ids"] = list(origin_ids)
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
                for q in (queries or [_q()]):
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
        for q in (queries[:2] if queries else [_q()]):
            results = _call(
                wh.query_forum, "hackernews", q,
                q, engine="hackernews", limit=per_source_limit
            )
            candidates.extend(_tag(c) for c in results)

    # --- research lane fallback: empty routed_sources -> arxiv ---
    if lane == "research" and not routed:
        for q in (queries or [_q()]):
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
# Nightly report writer
# ---------------------------------------------------------------------------

_REPORT_TEMPLATE = """\
---
type: nightly_report
date: {today}
written_by: web
phase: 3a
---

# Nightly Crawl Report -- {today}

{sections}

---

User: approve 0-5 via `python -m agents.ingest_index approve <id>` / reject with \
`reject <id> --reason ...`
"""

_SECTION_TEMPLATE = """\
## {title}

- **source_id**: `{source_id}`
- **lane**: {lane}
- **origin**: {origin}
- **score**: {score:.4f}
- **url**: {url}
- **snippet**: {snippet}
- **rationale**: {rationale}
"""


def _write_nightly_report(
    vault_root: str,
    today: str,
    selected: list[dict],
    trace=None,  # DecisionTrace | None
) -> Path:
    """Write meta/nightly_report/<today>.md with one section per selected candidate.

    If trace is provided, append a '## Decision trace' section rendered from it.
    """
    report_dir = Path(vault_root) / "meta" / "nightly_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{today}.md"

    sections = []
    for c in selected:
        origin_ids = c.get("origin_ids") or []
        rationale = (
            f"lane={c.get('lane', '')}; "
            + "origin: " + (", ".join(origin_ids) if origin_ids else "n/a")
        )
        snippet = (c.get("snippet") or "")[:200].replace("\n", " ").strip()
        sections.append(
            _SECTION_TEMPLATE.format(
                title=c.get("title", "(no title)"),
                source_id=c.get("source_id", ""),
                lane=c.get("lane", ""),
                origin=", ".join(origin_ids) if origin_ids else "n/a",
                score=float(c.get("score", 0.0)),
                url=c.get("url", ""),
                snippet=snippet or "(no snippet)",
                rationale=rationale,
            )
        )

    content = _REPORT_TEMPLATE.format(
        today=today,
        sections="\n".join(sections) if sections else "_No candidates selected._",
    )

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
) -> dict:
    """End-to-end Phase-3A FREE crawl pipeline.

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

    # 2. Build plan (threads trace into parse/merge/lanes/route steps)
    wd = _import_web_decision()
    plan = wd.build_plan(vault_root, config, registry, trace=trace)
    targets = plan.get("targets", [])

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

    # 4. Dedup seen (persist cache only when not dry_run)
    seen_p = _seen_path()
    new_ids_set: set[str] = set()
    new_candidates, updated_seen = wh.dedup_seen(all_candidates, seen_path=seen_p)
    # Compute the set of stable ids that survived dedup
    from web_harvest import _stable_id as _wh_stable_id, candidate_ident as _candidate_ident
    new_ids_set = {_wh_stable_id(c) for c in new_candidates}

    # Back-fill seen_dropped into each harvest/target trace record
    harvest_recs = trace.find("harvest", "target")
    for (target_id, start_idx, end_idx), hrec in zip(_target_cand_counts, harvest_recs):
        target_cands = all_candidates[start_idx:end_idx]
        seen_dropped = sum(
            1 for c in target_cands
            if _wh_stable_id(c) not in new_ids_set
        )
        hrec["data"]["seen_dropped"] = seen_dropped

    if not dry_run and new_candidates:
        # Persist the updated seen cache
        try:
            sp = Path(seen_p)
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(
                json.dumps(updated_seen, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"[web_crawl] could not write seen cache: {exc}", file=sys.stderr)

    # 5. Rank: score all candidates against PURPOSE + per-candidate evidence
    wr = _import_web_rank()
    rank_query = purpose_text
    scored_pool = wr.score_candidates(
        new_candidates,
        rank_query,
        registry=registry,
        allow_remote_ollama=allow_remote_ollama,
        today=today_s,
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
        rationale = (
            f"lane={c.get('lane', '')}; "
            + "origin: " + (", ".join(origin_ids) if origin_ids else "n/a")
        )
        score = float(c.get("score", 0.0))

        try:
            row = ii.enqueue(
                ident,
                title=title,
                rationale=rationale,
                score=score,
                discovered_by="web",
                objective_ids=list(origin_ids),
            )
            enqueued_ids.append(row.get("id", ident))
        except Exception as exc:
            print(f"[web_crawl] enqueue({ident!r}) failed: {exc}", file=sys.stderr)

    # Write nightly report (with embedded decision trace)
    report_path = _write_nightly_report(vault_root, today_s, selected, trace=trace)

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
