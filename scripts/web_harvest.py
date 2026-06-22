"""web_harvest.py -- Tier-0 FREE web harvester for Second Brain v0.2.

Contract: ALL sources here are $0 / key-less.
- Tier-0 (this module): RSS + free paper APIs + GitHub releases + forum APIs.
- Tier-1 (out of scope here): single-URL Claude WebFetch, invoked by the agent/skill
  at runtime when one canonical URL is already known.
- Tier-2 (out of scope here): paid scrapers (Phase 3B).

Every function returns a list of candidate dicts with the PINNED shape:
  {
    "title": str,
    "url": str,
    "source_id": str,      # registry id or stable item id (e.g. "arxiv:2401.09670")
    "id_type": str,        # "arxiv" | "doi" | "url" | "github"
    "published": str,      # ISO-8601 date string, or ""
    "snippet": str,        # abstract / summary / first chars
    "engine": str,         # "rss" | "arxiv" | "openalex" | "semantic_scholar" |
                           # "crossref" | "hackernews" | "reddit" | "lobsters" |
                           # "github"
  }

Lane and origin_ids are assigned later by the DECISION layer -- not here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path shim so lib.sources.* resolves from scripts/research/
# ---------------------------------------------------------------------------
_RESEARCH_DIR = Path(__file__).resolve().parent / "research"
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

# feedparser is installed; import lazily to allow mocking in tests
try:
    import feedparser as _feedparser_mod
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False

# requests is installed
try:
    import requests as _requests_mod
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

# ---------------------------------------------------------------------------
# Candidate shape helpers
# ---------------------------------------------------------------------------

_CANDIDATE_KEYS = ("title", "url", "source_id", "id_type", "published", "snippet", "engine")


def _candidate(
    title: str,
    url: str,
    source_id: str,
    id_type: str,
    published: str,
    snippet: str,
    engine: str,
) -> dict:
    return {
        "title": title or "",
        "url": url or "",
        "source_id": source_id or "",
        "id_type": id_type,
        "published": published or "",
        "snippet": snippet or "",
        "engine": engine,
    }


def _iso_date(raw: str | None) -> str:
    """Best-effort: keep only the YYYY-MM-DD portion if parseable, else ''."""
    if not raw:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw[:10] if len(raw) >= 10 else raw


def _arxiv_id_from_url(url: str) -> str | None:
    """Extract arXiv id like '2401.09670' from an arXiv URL, or return None."""
    m = re.search(r"arxiv\.org/abs/([0-9]+\.[0-9v]+)", url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# poll_rss
# ---------------------------------------------------------------------------

def poll_rss(
    feed_url: str,
    *,
    since: str | None = None,
    limit: int = 50,
    source_id: str | None = None,
) -> list[dict]:
    """Parse an RSS/Atom feed via feedparser; return up to `limit` candidates.

    Args:
        feed_url: URL or local file path accepted by feedparser.
        since: ISO-8601 date string (YYYY-MM-DD). Entries published before this
               date are filtered out. Entries without a published date are kept.
        limit: Maximum number of candidates to return.
        source_id: Optional registry id to use as source_id. Defaults to the
                   feed's hostname (or the raw url for local paths).

    Returns:
        list of candidate dicts with engine="rss".
    """
    if not _FEEDPARSER_OK:
        print("[web_harvest] feedparser not installed; poll_rss skipped", file=sys.stderr)
        return []

    try:
        feed = _feedparser_mod.parse(feed_url)
    except Exception as exc:
        print(f"[web_harvest] feedparser error for {feed_url}: {exc}", file=sys.stderr)
        return []

    # Derive a stable source_id from the feed host when none provided
    if source_id is None:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(feed_url)
            source_id = parsed.hostname or feed_url
        except Exception:
            source_id = feed_url

    candidates: list[dict] = []
    for entry in feed.entries:
        pub_raw = ""
        # feedparser normalises published to 'published' (struct_time in
        # 'published_parsed', string in 'published').
        if hasattr(entry, "published"):
            pub_raw = entry.published or ""
        elif hasattr(entry, "updated"):
            pub_raw = entry.updated or ""

        pub_iso = _iso_date(pub_raw)

        # Apply since filter
        if since and pub_iso and pub_iso < since:
            continue

        url = ""
        if hasattr(entry, "link"):
            url = entry.link or ""

        title = ""
        if hasattr(entry, "title"):
            title = entry.title or ""

        snippet = ""
        if hasattr(entry, "summary"):
            snippet = (entry.summary or "")[:500]
        elif hasattr(entry, "content") and entry.content:
            snippet = (entry.content[0].get("value") or "")[:500]

        candidates.append(_candidate(
            title=title,
            url=url,
            source_id=source_id,
            id_type="url",
            published=pub_iso,
            snippet=snippet,
            engine="rss",
        ))

        if len(candidates) >= limit:
            break

    return candidates


# ---------------------------------------------------------------------------
# query_papers
# ---------------------------------------------------------------------------

_PAPER_ENGINES = ("arxiv", "openalex", "semantic_scholar", "crossref")


def _load_paper_source(engine: str):
    """Lazily load the paper source class. Raises ValueError for unknown engine."""
    if engine == "arxiv":
        from lib.sources.arxiv import ArxivSource
        return ArxivSource()
    if engine == "openalex":
        from lib.sources.openalex import OpenAlexSource
        return OpenAlexSource()
    if engine == "semantic_scholar":
        from lib.sources.semantic_scholar import SemanticScholarSource
        return SemanticScholarSource()
    if engine == "crossref":
        from lib.sources.crossref import CrossRefSource
        return CrossRefSource()
    raise ValueError(f"Unknown paper engine: {engine!r}. Valid: {_PAPER_ENGINES}")


def _result_to_paper_candidate(result: Any, engine: str) -> dict:
    """Normalize a lib.sources Result to the candidate shape."""
    url = result.url or ""
    abstract = result.abstract or result.snippet or ""
    posted_at = result.posted_at or (str(result.year) if result.year else "")

    # Determine id_type + source_id
    arxiv_id = _arxiv_id_from_url(url)
    if arxiv_id:
        id_type = "arxiv"
        source_id = f"arxiv:{arxiv_id}"
    else:
        doi = (result.extra or {}).get("doi") if hasattr(result, "extra") else None
        if doi:
            id_type = "doi"
            source_id = doi
        else:
            id_type = "url"
            source_id = url

    return _candidate(
        title=result.title or "",
        url=url,
        source_id=source_id,
        id_type=id_type,
        published=_iso_date(posted_at),
        snippet=abstract[:500],
        engine=engine,
    )


def query_papers(
    query: str,
    *,
    engine: str = "arxiv",
    limit: int = 10,
) -> list[dict]:
    """Query a paper API and return candidates.

    Args:
        query: Search string.
        engine: One of "arxiv", "openalex", "semantic_scholar", "crossref".
        limit: Maximum number of candidates to return.

    Returns:
        list of candidate dicts. Returns [] on network/import errors.
    """
    try:
        source = _load_paper_source(engine)
        results = source.search(query, n=limit)
    except ValueError as exc:
        print(f"[web_harvest] {exc}", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"[web_harvest] query_papers({engine!r}) failed: {exc}", file=sys.stderr)
        return []

    return [_result_to_paper_candidate(r, engine) for r in results]


# ---------------------------------------------------------------------------
# query_forum
# ---------------------------------------------------------------------------

_FORUM_ENGINES = ("hackernews", "reddit", "lobsters")


def _load_forum_source(engine: str):
    """Lazily load the forum source class. Raises ValueError for unknown engine."""
    if engine == "hackernews":
        from lib.sources.hackernews import HackerNewsSource
        return HackerNewsSource()
    if engine == "reddit":
        from lib.sources.reddit import RedditSource
        return RedditSource()
    if engine == "lobsters":
        from lib.sources.lobsters import LobstersSource
        return LobstersSource()
    raise ValueError(f"Unknown forum engine: {engine!r}. Valid: {_FORUM_ENGINES}")


def _result_to_forum_candidate(result: Any, engine: str) -> dict:
    """Normalize a lib.sources Result to the candidate shape."""
    url = result.url or ""
    snippet = result.snippet or result.abstract or ""
    posted_at = result.posted_at or ""
    # Forum items identified by URL
    return _candidate(
        title=result.title or "",
        url=url,
        source_id=url,
        id_type="url",
        published=_iso_date(str(posted_at) if posted_at else ""),
        snippet=snippet[:500],
        engine=engine,
    )


def query_forum(
    query: str,
    *,
    engine: str = "hackernews",
    limit: int = 10,
) -> list[dict]:
    """Query a forum/discourse API and return candidates.

    Args:
        query: Search string.
        engine: One of "hackernews", "reddit", "lobsters".
        limit: Maximum number of candidates to return.

    Returns:
        list of candidate dicts. Returns [] on network/import errors.
    """
    try:
        source = _load_forum_source(engine)
        results = source.search(query, n=limit)
    except ValueError as exc:
        print(f"[web_harvest] {exc}", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"[web_harvest] query_forum({engine!r}) failed: {exc}", file=sys.stderr)
        return []

    return [_result_to_forum_candidate(r, engine) for r in results]


# ---------------------------------------------------------------------------
# poll_github_releases
# ---------------------------------------------------------------------------

_GH_API = "https://api.github.com/repos/{owner}/{repo}/releases"


def _parse_repo_url(repo_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL. Returns None on failure."""
    # Accept: https://github.com/owner/repo[.git][/...]
    #         github.com/owner/repo
    #         owner/repo  (bare)
    m = re.search(r"github\.com[/:]([^/]+)/([^/.\s]+)", repo_url)
    if m:
        return m.group(1), m.group(2).rstrip(".git")
    # bare "owner/repo"
    m2 = re.match(r"^([^/\s]+)/([^/\s]+)$", repo_url.strip())
    if m2:
        return m2.group(1), m2.group(2)
    return None


def poll_github_releases(
    repo_url: str,
    *,
    since: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Fetch GitHub releases (no auth; anonymous rate limit: 60 req/hr).

    Args:
        repo_url: GitHub repo URL or 'owner/repo'.
        since: ISO-8601 date string. Releases published before this date are
               filtered out.
        limit: Maximum number of candidates to return.

    Returns:
        list of candidate dicts with engine="github". Returns [] on any error.
    """
    if not _REQUESTS_OK:
        print("[web_harvest] requests not installed; poll_github_releases skipped", file=sys.stderr)
        return []

    parsed = _parse_repo_url(repo_url)
    if not parsed:
        print(f"[web_harvest] Cannot parse repo URL: {repo_url!r}", file=sys.stderr)
        return []

    owner, repo = parsed
    api_url = _GH_API.format(owner=owner, repo=repo)

    try:
        resp = _requests_mod.get(
            api_url,
            params={"per_page": min(limit, 100)},
            headers={"Accept": "application/vnd.github+json"},
            timeout=15,
        )
    except Exception as exc:
        print(f"[web_harvest] GitHub API request failed: {exc}", file=sys.stderr)
        return []

    if resp.status_code == 429:
        print("[web_harvest] GitHub API rate-limited (429); returning []", file=sys.stderr)
        return []

    if resp.status_code != 200:
        print(
            f"[web_harvest] GitHub API non-200 ({resp.status_code}) for {api_url}",
            file=sys.stderr,
        )
        return []

    try:
        releases = resp.json()
    except Exception as exc:
        print(f"[web_harvest] GitHub API JSON parse error: {exc}", file=sys.stderr)
        return []

    if not isinstance(releases, list):
        print("[web_harvest] GitHub API returned unexpected shape", file=sys.stderr)
        return []

    candidates: list[dict] = []
    repo_id = f"{owner}/{repo}"
    for rel in releases:
        pub_raw = rel.get("published_at") or rel.get("created_at") or ""
        pub_iso = _iso_date(pub_raw)

        if since and pub_iso and pub_iso < since:
            continue

        tag = rel.get("tag_name") or ""
        name = rel.get("name") or tag
        html_url = rel.get("html_url") or ""
        body = (rel.get("body") or "")[:500]

        candidates.append(_candidate(
            title=f"{repo}: {name}" if name else repo,
            url=html_url,
            source_id=repo_id,
            id_type="github",
            published=pub_iso,
            snippet=body,
            engine="github",
        ))

        if len(candidates) >= limit:
            break

    return candidates


# ---------------------------------------------------------------------------
# dedup_seen
# ---------------------------------------------------------------------------

_DEFAULT_SEEN_PATH = str(
    Path(__file__).resolve().parent.parent / ".claude" / "web" / "cache" / "seen.json"
)


def _stable_id(candidate: dict) -> str:
    """Return a stable dedup key: arxiv/doi id if present, else url."""
    id_type = candidate.get("id_type", "url")
    source_id = candidate.get("source_id", "")
    url = candidate.get("url", "")
    if id_type in ("arxiv", "doi") and source_id:
        return source_id
    return url or source_id


def dedup_seen(
    candidates: list[dict],
    seen_path: str = _DEFAULT_SEEN_PATH,
) -> tuple[list[dict], dict]:
    """Drop candidates already recorded in the seen-cache; return new ones + updated cache.

    Args:
        candidates: list of candidate dicts to filter.
        seen_path: Path to the seen-cache JSON file. Missing/empty file is treated
                   as an empty cache.

    Returns:
        (new_candidates, updated_seen_dict) where updated_seen_dict maps
        stable_id -> True for ALL known items (old + new).
        The caller is responsible for persisting updated_seen_dict to seen_path.
    """
    # Load existing seen cache
    seen: dict[str, bool] = {}
    try:
        p = Path(seen_path)
        if p.exists() and p.stat().st_size > 0:
            with p.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                seen = {str(k): bool(v) for k, v in data.items()}
    except Exception as exc:
        # Corrupt or unreadable cache: treat as empty, don't crash
        print(f"[web_harvest] dedup_seen: could not load {seen_path}: {exc}", file=sys.stderr)
        seen = {}

    new_candidates: list[dict] = []
    for c in candidates:
        sid = _stable_id(c)
        if sid and sid not in seen:
            new_candidates.append(c)
            seen[sid] = True
        elif not sid:
            # No stable id: keep it (can't reliably dedup)
            new_candidates.append(c)

    return new_candidates, seen


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Subcommands:
      rss <feed_url> [--since YYYY-MM-DD] [--limit N]
      papers <query> [--engine arxiv|openalex|semantic_scholar|crossref] [--limit N]
      forum <query> [--engine hackernews|reddit|lobsters] [--limit N]
      github <repo_url> [--since YYYY-MM-DD] [--limit N]

    Exit codes: 0 = ok, 2 = usage error. Network failures degrade gracefully
    (message to stderr, emit [], exit 0).
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="web_harvest",
        description="Tier-0 free web harvester for Second Brain.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rss = sub.add_parser("rss", help="Poll an RSS/Atom feed")
    p_rss.add_argument("feed_url")
    p_rss.add_argument("--since", default=None, help="ISO-8601 date cutoff (YYYY-MM-DD)")
    p_rss.add_argument("--limit", type=int, default=50)
    p_rss.add_argument("--source-id", default=None, dest="source_id")

    p_papers = sub.add_parser("papers", help="Query a paper API")
    p_papers.add_argument("query")
    p_papers.add_argument(
        "--engine",
        default="arxiv",
        choices=list(_PAPER_ENGINES),
        help="Paper source engine",
    )
    p_papers.add_argument("--limit", type=int, default=10)

    p_forum = sub.add_parser("forum", help="Query a forum API")
    p_forum.add_argument("query")
    p_forum.add_argument(
        "--engine",
        default="hackernews",
        choices=list(_FORUM_ENGINES),
        help="Forum source engine",
    )
    p_forum.add_argument("--limit", type=int, default=10)

    p_gh = sub.add_parser("github", help="Poll GitHub releases")
    p_gh.add_argument("repo_url", help="GitHub repo URL or owner/repo")
    p_gh.add_argument("--since", default=None, help="ISO-8601 date cutoff (YYYY-MM-DD)")
    p_gh.add_argument("--limit", type=int, default=10)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 2

    if args.cmd == "rss":
        results = poll_rss(
            args.feed_url,
            since=args.since,
            limit=args.limit,
            source_id=args.source_id,
        )
        _print_json(results)

    elif args.cmd == "papers":
        results = query_papers(args.query, engine=args.engine, limit=args.limit)
        _print_json(results)

    elif args.cmd == "forum":
        results = query_forum(args.query, engine=args.engine, limit=args.limit)
        _print_json(results)

    elif args.cmd == "github":
        results = poll_github_releases(args.repo_url, since=args.since, limit=args.limit)
        _print_json(results)

    else:
        print(f"Unknown subcommand: {args.cmd}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
