#!/usr/bin/env python3
"""web_rank.py -- Candidate scorer (web-rank engine) for Second Brain v0.2.

Ranks web crawl candidates by relevance to a query string (vault PURPOSE + target
expected_evidence/title). Two scoring paths:

  1. Embedding path (ollama reachable + nomic-embed-text pulled):
       embed query once + each candidate's (title + ". " + snippet);
       score = cosine(query_emb, cand_emb).
       Individual embed failures fall back to the deterministic score.

  2. Deterministic fallback (ollama down / unreachable -- the default on this
       machine):
       score = weighted sum of:
         (a) keyword overlap: overlap coefficient between query tokens and
             (title + snippet) tokens (lowercase, split on non-alphanumerics,
             drop len<4 + English stopwords).
         (b) registry-relevance bonus: if registry is given and candidate
             source_id maps to a registry entry, add (relevance / 5) * W_REL.
         (c) recency bonus: days since published vs today; more recent = higher.

Reuses primitives from scripts/rerank.py:
  ollama_url(allow_remote), ollama_alive(url), embed_one(url, model, text),
  cosine(a, b), DEFAULT_MODEL.

Candidate dict shape (produced by web_harvest.py):
  {title, url, source_id, id_type, published, snippet, engine}
  (+ may carry lane, origin_ids already)
All existing keys are preserved; a "score" float is added.

CLI:
  cat candidates.json | python scripts/web_rank.py --query "..." [--registry PATH]
  python scripts/web_rank.py --query "..." --registry .claude/web/ < candidates.json

Exit codes:
  0  -- success
  2  -- usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path shim: make scripts/ importable so `import rerank` works
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from rerank import ollama_url, ollama_alive, embed_one, cosine, DEFAULT_MODEL  # noqa: E402

# ---------------------------------------------------------------------------
# Weight constants (deterministic fallback)
# ---------------------------------------------------------------------------

# Weight for keyword-overlap component (0..1, overlap coefficient)
W_KW: float = 0.60

# Weight for registry-relevance bonus (relevance/5 * W_REL)
W_REL: float = 0.25

# Weight for recency bonus (max 1.0 * W_REC at age=0 days, decays over RECENCY_HALF_LIFE)
W_REC: float = 0.15

# Half-life in days for recency decay (score = W_REC * 2**(-age/RECENCY_HALF_LIFE))
RECENCY_HALF_LIFE: float = 60.0

# ---------------------------------------------------------------------------
# Stopwords (same set as web_decision.py for consistency)
# ---------------------------------------------------------------------------

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


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop tokens shorter than 4 chars and stopwords.

    Returns a list (not a set) to allow frequency-weighted overlap later; callers that need
    set semantics can wrap in frozenset().
    """
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) >= 4 and t not in _EN_STOPWORDS]


def _overlap_coefficient(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Overlap coefficient = |intersection| / min(|A|, |B|).

    Uses set semantics (no double-counting within a doc).
    Returns 0.0 when either set is empty.
    """
    q_set = frozenset(query_tokens)
    d_set = frozenset(doc_tokens)
    if not q_set or not d_set:
        return 0.0
    inter = len(q_set & d_set)
    return inter / min(len(q_set), len(d_set))


def _recency_score(published: str, today: str) -> float:
    """Return a 0..1 recency bonus using exponential decay.

    published: ISO-8601 date string (YYYY-MM-DD prefix) or "".
    today:     ISO-8601 date string (YYYY-MM-DD).
    Missing / unparseable published date returns 0.0.
    """
    if not published:
        return 0.0
    # Accept "2026-06-15T..." or "2026-06-15"
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", published)
    if not m:
        return 0.0
    try:
        pub_date = date.fromisoformat(m.group(1))
        today_date = date.fromisoformat(today[:10])
    except ValueError:
        return 0.0
    age_days = (today_date - pub_date).days
    if age_days < 0:
        # Future-dated: treat as 0 days old
        age_days = 0
    # Exponential decay: score = 2**(-age / half_life), in range (0, 1]
    import math
    return math.pow(2.0, -age_days / RECENCY_HALF_LIFE)


def _registry_relevance(source_id: str, registry: dict | None) -> float:
    """Look up source_id across all registry sections; return relevance/5 or 0.0."""
    if not registry or not source_id:
        return 0.0
    for section_key, section in registry.items():
        # paper-publisher and blog-newsfeed use "sources" list
        # github-repos uses "repositories" list
        for list_key in ("sources", "repositories"):
            entries = section.get(list_key, [])
            for entry in entries:
                if entry.get("id") == source_id:
                    try:
                        rel = float(entry.get("relevance", 0))
                        return min(rel / 5.0, 1.0)
                    except (TypeError, ValueError):
                        return 0.0
    return 0.0


def _deterministic_score(
    candidate: dict,
    query_tokens: list[str],
    registry: dict | None,
    today: str,
) -> float:
    """Compute the deterministic fallback score for a single candidate."""
    title = candidate.get("title") or ""
    snippet = candidate.get("snippet") or ""
    doc_tokens = _tokenize(title + " " + snippet)

    kw_score = _overlap_coefficient(query_tokens, doc_tokens)
    rel_score = _registry_relevance(candidate.get("source_id", ""), registry)
    rec_score = _recency_score(candidate.get("published", ""), today)

    return W_KW * kw_score + W_REL * rel_score + W_REC * rec_score


def _today_str(today: str | None) -> str:
    """Return today as YYYY-MM-DD, using override if provided."""
    if today:
        return today[:10]
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_candidates(
    candidates: list[dict],
    query: str,
    *,
    registry: dict | None = None,
    allow_remote_ollama: bool = False,
    today: str | None = None,
    learned: dict | None = None,
    weights: dict | None = None,
) -> list[dict]:
    """Score and rank web crawl candidates by relevance to query.

    Parameters
    ----------
    candidates:
        List of candidate dicts (web_harvest.py shape). Each dict is copied;
        originals are not mutated.
    query:
        Relevance query string (vault PURPOSE + target expected_evidence/title).
    registry:
        Optional registry dict (keyed by "paper-publisher", "blog-newsfeed",
        "github-repos"). Used for the relevance bonus in the deterministic path.
    allow_remote_ollama:
        Passed through to ollama_url(); default False (localhost-only).
    today:
        ISO-8601 date string used as recency reference. Defaults to today's date.
    learned:
        Optional learned.json dict (from agent_learn.learn() or loaded directly).
        When provided, adds a reputation prior to the base score:
          score = base + w_rep * rep_for(learned, source_id, engine)
                       - w_rej * reject_penalty(learned, candidate)
        When None (default), behaviour is identical to before -- no change.
    weights:
        Optional dict with keys ``w_rep`` and ``w_rej`` that override the
        defaults (w_rep=0.15, w_rej=0.2) from web-config learn block.
        Ignored when learned is None.

    Returns
    -------
    New list of candidate dicts each with "score" (float) added,
    sorted by score descending. Stable sort on equal scores (tie-break by
    source_id for deterministic ordering).
    """
    if not candidates:
        return []

    today_s = _today_str(today)
    query_tokens = _tokenize(query)

    # Resolve learned-prior weights (only used when learned is provided)
    _w_rep: float = 0.15
    _w_rej: float = 0.2
    if learned and weights:
        _w_rep = float(weights.get("w_rep", _w_rep))
        _w_rej = float(weights.get("w_rej", _w_rej))

    # Lazy import of agent_learn (only when learned is provided)
    _agent_learn = None
    if learned:
        try:
            import sys as _sys
            import os as _os
            _scripts = str(Path(__file__).resolve().parent)
            if _scripts not in _sys.path:
                _sys.path.insert(0, _scripts)
            import agent_learn as _agent_learn
        except ImportError:
            _agent_learn = None

    def _apply_learned_prior(cand: dict, base: float) -> float:
        """Apply learned reputation/reject adjustment to a base score."""
        if not learned or _agent_learn is None:
            return base
        src_id = cand.get("source_id", "") or ""
        engine = cand.get("engine", "") or ""
        rep = _agent_learn.rep_for(learned, src_id, engine)
        rej = _agent_learn.reject_penalty(learned, cand)
        return base + _w_rep * rep - _w_rej * rej

    # Shallow-copy all candidates so we don't mutate caller's dicts
    result = [dict(c) for c in candidates]

    # ---- Try embedding path ----
    try:
        url = ollama_url(allow_remote_ollama)
        alive, models = ollama_alive(url)
    except SystemExit:
        # ollama_url() may sys.exit(2) if remote url not allowed; treat as down
        alive = False
        models = []

    if alive and DEFAULT_MODEL in models:
        # Embed query once
        try:
            q_emb = embed_one(url, DEFAULT_MODEL, query)
        except Exception:
            q_emb = None

        if q_emb:
            for cand in result:
                title = cand.get("title") or ""
                snippet = cand.get("snippet") or ""
                cand_text = title + ". " + snippet
                try:
                    c_emb = embed_one(url, DEFAULT_MODEL, cand_text)
                    base = float(cosine(q_emb, c_emb))
                except Exception:
                    # Fallback to deterministic for this candidate
                    base = _deterministic_score(cand, query_tokens, registry, today_s)
                cand["base_score"] = base
                cand["score"] = _apply_learned_prior(cand, base)

            # Sort: desc by score, tie-break by source_id (stable)
            result.sort(key=lambda c: (-c["score"], c.get("source_id", "")))
            return result

    # ---- Deterministic fallback ----
    for cand in result:
        base = _deterministic_score(cand, query_tokens, registry, today_s)
        cand["base_score"] = base
        cand["score"] = _apply_learned_prior(cand, base)

    # Sort: desc by score, tie-break by source_id for deterministic stability
    result.sort(key=lambda c: (-c["score"], c.get("source_id", "")))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="web_rank",
        description="Score and rank web crawl candidates by relevance to a query.",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Relevance query string (vault PURPOSE + target evidence/title).",
    )
    parser.add_argument(
        "--registry",
        default=None,
        help=(
            "Path to .claude/web/ directory containing sources/ subdirectory, "
            "or a single registry JSON file."
        ),
    )
    parser.add_argument(
        "--allow-remote-ollama",
        action="store_true",
        help="Accept non-localhost OLLAMA_URL (potential data exfil).",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 2

    if not args.query.strip():
        print("ERROR: --query must be non-empty", file=sys.stderr)
        return 2

    # Read candidates JSON from stdin
    try:
        raw = sys.stdin.read()
        candidates = json.loads(raw)
        if not isinstance(candidates, list):
            print("ERROR: stdin must be a JSON array of candidate dicts", file=sys.stderr)
            return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2

    # Load registry if provided
    registry: dict | None = None
    if args.registry:
        reg_path = Path(args.registry)
        if reg_path.is_dir():
            # Treat as .claude/web/ directory
            sources_dir = reg_path / "sources"
            if not sources_dir.is_dir():
                sources_dir = reg_path
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
                        pass
        elif reg_path.is_file():
            try:
                registry = json.loads(reg_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"ERROR: cannot read registry file: {exc}", file=sys.stderr)
                return 2
        else:
            print(f"ERROR: --registry path not found: {args.registry}", file=sys.stderr)
            return 2

    ranked = score_candidates(
        candidates,
        args.query,
        registry=registry,
        allow_remote_ollama=args.allow_remote_ollama,
    )
    print(json.dumps(ranked, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
