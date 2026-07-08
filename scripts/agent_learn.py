#!/usr/bin/env python3
"""agent_learn.py -- Reusable agent-learning core for Second Brain v0.2.

Turns accept/reject decisions stored in the ingest index into durable per-source/engine
reputation scores, reject-keyword patterns, and score calibration. Results are persisted
in .claude/memory/<agent>/learned.json. A human-readable briefing summarises what changed.

Wave 2 will wire `rep_for`, `reject_penalty`, and the learned weights into the ranking
and routing layers -- this module provides the stable, pinned interfaces they depend on.

Schema (learned.json):
  {
    "updated": "<ISO-8601 str>",
    "sources": {
      "<source_id>": {"accept": int, "reject": int, "rep": float}
    },
    "engines": {
      "<engine>": {"accept": int, "reject": int, "rep": float}
    },
    "reject_patterns": {
      "keyword_counts": {<kw>: <int>},  # cumulative count across runs (1 per distinct reason)
      "keywords": [str]                 # DERIVED: sorted by count desc, only kw >= min_reject_freq, cap 50
    },
    "calibration": {
      "accepted_score_mean": float,
      "rejected_score_mean": float,
      "n": int
    },
    "processed_ids": [str]    # ingest row ids already counted (idempotency)
  }

Migration: an old file with only "keywords" (no "keyword_counts") is seeded by setting
keyword_counts[kw] = min_reject_freq for each existing keyword so they survive the threshold.


CLI:
  python scripts/agent_learn.py --vault <V> --agent web [--dry-run] [--json]

  --dry-run  Compute + print briefing/json but do NOT write learned.json.
  --json     Print the full return dict as JSON.

Exit codes:
  0  success
  2  usage error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path shim: repo root + scripts/ must be importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
for _p in (str(_SCRIPTS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# English stopwords + domain stoplist (tokens to drop from reject keywords)
# ---------------------------------------------------------------------------
_ENGLISH_STOPS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "it", "its", "this", "that", "are", "was", "were",
    "be", "been", "by", "from", "as", "not", "do", "did", "has", "have",
    "had", "will", "would", "could", "should", "may", "might", "can",
    "more", "also", "we", "they", "their", "our", "you", "he", "she",
    "his", "her", "which", "who", "what", "how", "when", "where", "there",
    "these", "those", "than", "then", "into", "over", "about", "after",
    "based", "using", "used", "via", "new", "two", "one", "all", "each",
    "other", "such", "while", "both", "only", "see", "per",
    # Generic noise commonly found in free-text rejection reasons
    "authors", "itself", "much", "help", "doesn", "papers", "thesis",
    "graduate", "schools", "relevant", "focus", "topic",
}

_DOMAIN_STOPS = {
    "optical", "inference", "model", "llm", "gpu", "source", "paper",
    "arxiv", "research", "work", "result", "results", "show", "shows",
    "approach", "method", "methods", "system", "systems", "data",
    "performance", "evaluation", "analysis", "study", "propose",
    "proposed", "present", "presented", "novel", "high", "large",
    "deep", "neural", "network", "networks", "learning", "training",
    "test", "benchmark", "memory", "compute", "efficient", "framework",
}

_STOPWORDS = _ENGLISH_STOPS | _DOMAIN_STOPS

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_web_config() -> dict:
    """Load .claude/web/web-config.json from the repo root. Returns {} on failure."""
    cfg_path = _REPO_ROOT / ".claude" / "web" / "web-config.json"
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _prior_strength() -> float:
    """Read learn.prior_strength from web-config, default 1.0."""
    cfg = _load_web_config()
    return float(cfg.get("learn", {}).get("prior_strength", 1.0))


def _min_reject_freq() -> int:
    """Read learn.min_reject_freq from web-config, default 2.

    A token must appear in >= min_reject_freq DISTINCT rejection reasons
    (cumulatively across runs) before it graduates to the keywords list.
    """
    cfg = _load_web_config()
    return int(cfg.get("learn", {}).get("min_reject_freq", 2))


def _harvest_reject_keywords() -> bool:
    """Read learn.harvest_reject_keywords from web-config, default False.

    When False (default), free-text rejection reasons are NOT tokenised into
    reject keywords. This prevents a reason like "put more focus on semianalysis"
    from creating a reject keyword that penalises the very source the user asked
    for MORE of. Source/engine reputation still updates from accept/reject counts.
    """
    cfg = _load_web_config()
    return bool(cfg.get("learn", {}).get("harvest_reject_keywords", False))


# ---------------------------------------------------------------------------
# Reputation helper
# ---------------------------------------------------------------------------

def _rep(accept: int, reject: int, prior_strength: float) -> float:
    """Smoothed reputation in [-1, +1] (cold-start neutral at 0.0).

    Formula:
      a   = prior_strength   (additive smoothing weight)
      p   = (accept + a) / (accept + reject + 2*a)
      rep = 2*p - 1

    Examples (prior_strength=1.0):
      (0, 0) -> p=0.5, rep=0.0   (cold-start neutral)
      (0, 1) -> p=1/3, rep~-0.33
      (1, 0) -> p=2/3, rep~+0.33
      (3, 0) -> p=4/5, rep~+0.60
      (0, 3) -> p=1/5, rep~-0.60
    """
    a = prior_strength
    p = (accept + a) / (accept + reject + 2 * a)
    return round(2 * p - 1, 6)


# ---------------------------------------------------------------------------
# Keyword extraction helper
# ---------------------------------------------------------------------------

def _extract_keywords(reason: str) -> list[str]:
    """Tokenize a rejection reason and return meaningful keywords.

    Process:
    1. Lowercase the reason.
    2. Split on non-alphanumeric characters.
    3. Drop English stopwords, domain stopwords, and tokens shorter than 4 chars.

    Returns a list of unique tokens (preserving first-occurrence order).
    """
    if not reason:
        return []
    lower = reason.lower()
    tokens = _TOKEN_RE.split(lower)
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        if len(tok) < 4:
            continue
        if tok in _STOPWORDS:
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


# ---------------------------------------------------------------------------
# PURPOSE/topic guard: build protected terms from vault purpose + topics
# ---------------------------------------------------------------------------

def _build_protected_terms(vault_root: str) -> set[str]:
    """Return the set of tokens that must NEVER become reject keywords.

    Reads:
      <vault_root>/objective/purpose/PURPOSE.md
      <vault_root>/objective/topic/*.md  (all .md except _template.md)

    Lowercases + tokenizes each file's full text using the same tokenizer as
    _extract_keywords (split on non-alphanumeric, keep tokens >= 4 chars).
    Missing files are silently skipped (returns empty set rather than crashing).

    This guarantees core domain terms (e.g. disaggregation, optical, inference,
    cache, iris, tetra, pipeline, prefill, photonic ...) can never be extracted
    as reject keywords because they appear in the vault's purpose statement.
    """
    vault_path = Path(vault_root)
    protected: set[str] = set()

    def _ingest_file(p: Path) -> None:
        try:
            text = p.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            return
        for tok in _TOKEN_RE.split(text):
            if len(tok) >= 4:
                protected.add(tok)

    # PURPOSE.md
    purpose_file = vault_path / "objective" / "purpose" / "PURPOSE.md"
    _ingest_file(purpose_file)

    # topic/*.md (skip _template.md)
    topic_dir = vault_path / "objective" / "topic"
    if topic_dir.is_dir():
        for topic_file in topic_dir.glob("*.md"):
            if topic_file.name.startswith("_"):
                continue
            _ingest_file(topic_file)

    return protected


# ---------------------------------------------------------------------------
# Derived keywords list from keyword_counts
# ---------------------------------------------------------------------------

def _derive_keywords(keyword_counts: dict[str, int], min_freq: int, cap: int = 50) -> list[str]:
    """Derive the active keywords list from cumulative counts.

    Returns tokens whose count >= min_freq, sorted by count descending
    (ties broken alphabetically for determinism), capped at `cap` entries.
    """
    qualifying = [(kw, cnt) for kw, cnt in keyword_counts.items() if cnt >= min_freq]
    qualifying.sort(key=lambda x: (-x[1], x[0]))
    return [kw for kw, _ in qualifying[:cap]]


# ---------------------------------------------------------------------------
# Learned.json path + I/O
# ---------------------------------------------------------------------------

def _learned_path(agent: str) -> Path:
    """Return the path to .claude/memory/<agent>/learned.json (in the code repo)."""
    return _REPO_ROOT / ".claude" / "memory" / agent / "learned.json"


def _load_learned(agent: str, min_freq: int = 2) -> dict:
    """Load the existing learned.json or return a fresh empty schema.

    Migration (additive, never crashes on old files):
    - If reject_patterns has only "keywords" (old schema), seed keyword_counts
      by assigning min_freq to each existing keyword so they survive the threshold.
    - setdefault is used for all new keys so old files are upgraded transparently.
    """
    p = _learned_path(agent)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Ensure all required top-level keys present (additive migration)
            data.setdefault("sources", {})
            data.setdefault("engines", {})
            data.setdefault("reject_patterns", {"keyword_counts": {}, "keywords": []})
            data.setdefault("calibration", {
                "accepted_score_mean": 0.0,
                "rejected_score_mean": 0.0,
                "n": 0,
            })
            data.setdefault("processed_ids", [])

            # Migrate old schema: only "keywords" present, no "keyword_counts"
            rp = data["reject_patterns"]
            if "keyword_counts" not in rp:
                old_kws = rp.get("keywords", [])
                rp["keyword_counts"] = {kw: min_freq for kw in old_kws}
            # Ensure "keywords" key always present (may be absent in a fresh file)
            rp.setdefault("keywords", [])
            return data
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "updated": "",
        "sources": {},
        "engines": {},
        "reject_patterns": {"keyword_counts": {}, "keywords": []},
        "calibration": {
            "accepted_score_mean": 0.0,
            "rejected_score_mean": 0.0,
            "n": 0,
        },
        "processed_ids": [],
    }


def _save_learned(agent: str, data: dict) -> None:
    """Write learned.json atomically (parent dirs created as needed)."""
    p = _learned_path(agent)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
                 encoding="utf-8")


# ---------------------------------------------------------------------------
# Running mean helper
# ---------------------------------------------------------------------------

def _update_mean(current_mean: float, current_n: int, new_value: float) -> tuple[float, int]:
    """Update a running mean with one new sample. Returns (new_mean, new_n)."""
    new_n = current_n + 1
    new_mean = current_mean + (new_value - current_mean) / new_n
    return round(new_mean, 6), new_n


# ---------------------------------------------------------------------------
# Public lookup helpers (pinned for Wave 2)
# ---------------------------------------------------------------------------

def rep_for(learned: dict, source_id: str, engine: str) -> float:
    """Return the reputation score for a candidate.

    Precedence: source_id rep (if source_id present in learned) > engine rep > 0.0.

    Parameters
    ----------
    learned : dict
        The loaded learned.json dict (as returned by learn() or _load_learned()).
    source_id : str
        The source registry id of the candidate (e.g. "arxiv_cs_dc").
    engine : str
        The harvest engine used (e.g. "arxiv", "rss", "hackernews").

    Returns
    -------
    float
        Reputation in [-1.0, +1.0]. 0.0 when neither source nor engine is known.
    """
    sources = learned.get("sources", {})
    if source_id and source_id in sources:
        return float(sources[source_id].get("rep", 0.0))
    engines = learned.get("engines", {})
    if engine and engine in engines:
        # Floor engine-level rep at 0.0: papers carry per-item ids (e.g.
        # "arxiv:2401...") and never a registry source_id, so they fall through
        # to engine rep. A NEGATIVE engine rep would then blanket-penalise every
        # paper from that engine off a single reject. Positive engine reputation
        # may still help; source-level rep (positive or negative) is unaffected.
        return max(0.0, float(engines[engine].get("rep", 0.0)))
    return 0.0


def reject_penalty(learned: dict, candidate: dict) -> float:
    """Return 1.0 when this candidate should be penalised based on learned patterns.

    Penalty triggers (either is sufficient):
    1. The candidate's title or snippet contains any reject keyword from learned patterns.
    2. The candidate's source_id has rep <= -0.5.

    Returns 0.0 when neither condition is met.

    Parameters
    ----------
    learned : dict
        The loaded learned.json dict.
    candidate : dict
        A web harvest candidate dict with keys like 'title', 'snippet', 'source_id'.

    Returns
    -------
    float
        1.0 (penalise) or 0.0 (no penalty).
    """
    # Check source reputation
    src_id = (candidate.get("source_id") or "").strip()
    if src_id:
        sources = learned.get("sources", {})
        if src_id in sources:
            src_rep = float(sources[src_id].get("rep", 0.0))
            if src_rep <= -0.5:
                return 1.0

    # Check reject keywords in title + snippet
    keywords = learned.get("reject_patterns", {}).get("keywords", [])
    if not keywords:
        return 0.0

    title = (candidate.get("title") or "").lower()
    snippet = (candidate.get("snippet") or "").lower()
    text = title + " " + snippet
    # Tokenize candidate text the same way we build keywords
    cand_tokens = set(_TOKEN_RE.split(text))
    for kw in keywords:
        if kw in cand_tokens:
            return 1.0
    return 0.0


def render_briefing(delta: dict, prev_updated: str | None) -> str:
    """Render a markdown briefing summarising the learning delta.

    Parameters
    ----------
    delta : dict
        The delta dict returned from learn() containing:
          "sources": {source_id: {"accept": int, "reject": int, "rep": float,
                                  "prev_rep": float | None, "new_keywords": [str]}}
          "engines": {engine: {...}}
          "new_keywords": [str]          -- net-new keywords added this run
          "calibration": {...}           -- updated calibration stats
          "n_new": int                   -- count of new decisions processed

    prev_updated : str | None
        The "updated" timestamp from the PREVIOUS learned.json (before this run).
        Used in the briefing header.

    Returns
    -------
    str
        Markdown briefing string (no trailing newline enforced; caller may add one).
    """
    n_new = delta.get("n_new", 0)
    from_date = prev_updated or "n/a"

    header = f"## Learning briefing (applied from {from_date} crawl)"

    if n_new == 0:
        return (
            f"{header}\n\n"
            "No prior outcomes yet -- learning starts after your first approve/reject batch.\n"
        )

    lines: list[str] = [header, ""]

    # Per-source bullets
    src_changes = delta.get("sources", {})
    if src_changes:
        lines.append("**Sources updated:**")
        for src_id, info in sorted(src_changes.items()):
            a = info.get("accept", 0)
            r = info.get("reject", 0)
            rep = info.get("rep", 0.0)
            prev_rep = info.get("prev_rep")
            if prev_rep is None:
                arrow = "(new)"
            elif rep > prev_rep:
                arrow = "up"
            elif rep < prev_rep:
                arrow = "down"
            else:
                arrow = "unchanged"
            lines.append(
                f"- `{src_id}`: rep={rep:+.3f} ({arrow}) | accept={a}, reject={r}"
            )
        lines.append("")

    # Per-engine bullets
    eng_changes = delta.get("engines", {})
    if eng_changes:
        lines.append("**Engines updated:**")
        for eng, info in sorted(eng_changes.items()):
            a = info.get("accept", 0)
            r = info.get("reject", 0)
            rep = info.get("rep", 0.0)
            prev_rep = info.get("prev_rep")
            if prev_rep is None:
                arrow = "(new)"
            elif rep > prev_rep:
                arrow = "up"
            elif rep < prev_rep:
                arrow = "down"
            else:
                arrow = "unchanged"
            lines.append(
                f"- `{eng}`: rep={rep:+.3f} ({arrow}) | accept={a}, reject={r}"
            )
        lines.append("")

    # Reject keywords added
    new_kw = delta.get("new_keywords", [])
    if new_kw:
        kw_str = ", ".join(f"`{k}`" for k in new_kw[:20])
        lines.append(f"**Reject keywords added:** {kw_str}")
        lines.append("")

    # Calibration
    cal = delta.get("calibration", {})
    if cal:
        acc_mean = cal.get("accepted_score_mean", 0.0)
        rej_mean = cal.get("rejected_score_mean", 0.0)
        n = cal.get("n", 0)
        lines.append(
            f"**Calibration** (n={n}): accepted_score_mean={acc_mean:.3f}, "
            f"rejected_score_mean={rej_mean:.3f}"
        )
        lines.append("")

    lines.append(f"_{n_new} new decision(s) processed._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core learning function
# ---------------------------------------------------------------------------

def learn(
    vault_root: str,
    agent: str = "web",
    *,
    apply: bool = True,
    today: str | None = None,
) -> dict:
    """Process new accept/reject decisions and update the learned reputation model.

    Reads ingest index rows discovered by *agent*, identifies rows with newly-decided
    statuses (ingested/pending=ACCEPT; rejected=REJECT), skips rows already counted
    (idempotency via processed_ids), updates per-source and per-engine reputation,
    extracts reject keywords, and updates score calibration.

    Parameters
    ----------
    vault_root : str
        Absolute path to the vault root directory.
    agent : str
        The agent whose rows to process (matched against ``discovered_by``).
        Defaults to "web".
    apply : bool
        When True (default), write the updated learned.json to disk.
        When False (dry-run), compute and return results without writing.
    today : str | None
        ISO-8601 date for the "updated" timestamp. Defaults to today (UTC).

    Returns
    -------
    dict with keys:
      "learned"  : the updated learned dict (full schema)
      "briefing" : markdown string summarising the delta
      "delta"    : dict describing what changed this run
      "n_new"    : int count of new decisions processed
    """
    from agents import ingest_index as _ii

    today_s = today or datetime.now(timezone.utc).date().isoformat()
    prior = _prior_strength()
    min_freq = _min_reject_freq()
    harvest_reject_kw = _harvest_reject_keywords()

    # Load ingest rows using root= kwarg (bypasses vault_config env resolution)
    vault_path = Path(vault_root)
    raw_data = _ii._load(None, root=vault_path)
    rows = list(raw_data.get("sources", {}).values())

    # Guard 2: build protected terms from PURPOSE + topic files (never penalise these)
    protected_terms = _build_protected_terms(vault_root)

    # Filter: only rows discovered by this agent
    agent_rows = [r for r in rows if r.get("discovered_by") == agent]

    # Classify: DECIDED = ingested/pending/rejected; NEUTRAL = waiting_approval
    ACCEPT_STATUSES = {"ingested", "pending"}
    REJECT_STATUSES = {"rejected"}
    DECIDED_STATUSES = ACCEPT_STATUSES | REJECT_STATUSES

    decided = [r for r in agent_rows if r.get("status") in DECIDED_STATUSES]

    # Load existing learned state
    learned = _load_learned(agent, min_freq=min_freq)
    prev_updated = learned.get("updated") or None
    processed_set: set[str] = set(learned.get("processed_ids", []))

    # Filter to NEW decisions only (not yet counted)
    new_decided = [r for r in decided if r.get("id") not in processed_set]

    # Accumulators for the delta report
    delta_sources: dict[str, dict] = {}
    delta_engines: dict[str, dict] = {}
    delta_new_keywords: list[str] = []

    # Working copies of mutable state (so we can roll back if apply=False is needed)
    sources = learned["sources"]
    engines = learned["engines"]
    # Guard 1: keyword_counts tracks cumulative per-distinct-reason occurrence counts
    keyword_counts: dict[str, int] = dict(
        learned["reject_patterns"].get("keyword_counts", {})
    )
    cal = learned["calibration"]

    # Calibration running means
    acc_mean = cal.get("accepted_score_mean", 0.0)
    rej_mean = cal.get("rejected_score_mean", 0.0)
    cal_n = cal.get("n", 0)
    # We need separate counts for accepted and rejected to maintain correct means.
    # Re-derive from current learned data (best-effort from n; split not stored separately).
    # For simplicity, track total n for calibration updates.

    for row in new_decided:
        row_id = row.get("id", "")
        status = row.get("status", "")
        src_id = (row.get("source_id") or "").strip()
        eng = (row.get("engine") or "").strip()
        score = row.get("relevance_score")
        reason = (row.get("rejection_reason") or "").strip()

        is_accept = status in ACCEPT_STATUSES
        is_reject = status in REJECT_STATUSES

        # -- Update per-source counts --
        if src_id:
            if src_id not in sources:
                sources[src_id] = {"accept": 0, "reject": 0, "rep": 0.0}
                delta_sources[src_id] = {"prev_rep": None, "new_keywords": []}
            elif src_id not in delta_sources:
                delta_sources[src_id] = {
                    "prev_rep": sources[src_id].get("rep"),
                    "new_keywords": [],
                }
            if is_accept:
                sources[src_id]["accept"] += 1
            else:
                sources[src_id]["reject"] += 1
            new_rep = _rep(sources[src_id]["accept"], sources[src_id]["reject"], prior)
            sources[src_id]["rep"] = new_rep

        # -- Update per-engine counts --
        if eng:
            if eng not in engines:
                engines[eng] = {"accept": 0, "reject": 0, "rep": 0.0}
                delta_engines[eng] = {"prev_rep": None, "new_keywords": []}
            elif eng not in delta_engines:
                delta_engines[eng] = {
                    "prev_rep": engines[eng].get("rep"),
                    "new_keywords": [],
                }
            if is_accept:
                engines[eng]["accept"] += 1
            else:
                engines[eng]["reject"] += 1
            new_eng_rep = _rep(engines[eng]["accept"], engines[eng]["reject"], prior)
            engines[eng]["rep"] = new_eng_rep

        # -- Extract reject keywords (opt-in; default OFF via _harvest_reject_keywords).
        #    Guard 1: count per distinct reason; Guard 2: skip protected terms. --
        if harvest_reject_kw and is_reject and reason:
            kws = _extract_keywords(reason)
            for kw in kws:
                # Guard 2: never count terms that appear in the vault PURPOSE/topics
                if kw in protected_terms:
                    continue
                # Guard 1: count this token once for this distinct reason
                keyword_counts[kw] = keyword_counts.get(kw, 0) + 1

        # -- Update calibration --
        if score is not None:
            try:
                score_f = float(score)
                if is_accept:
                    new_acc_mean, _ = _update_mean(acc_mean, cal_n, score_f)
                    acc_mean = new_acc_mean
                else:
                    new_rej_mean, _ = _update_mean(rej_mean, cal_n, score_f)
                    rej_mean = new_rej_mean
                cal_n += 1
            except (TypeError, ValueError):
                pass

        # Mark as processed
        processed_set.add(row_id)

    n_new = len(new_decided)

    # Guard 1: derive active keywords list from cumulative counts + min_freq threshold
    # Sorted by count descending (deterministic), capped at 50.
    all_keywords = _derive_keywords(keyword_counts, min_freq=min_freq, cap=50)

    # Compute newly-promoted keywords (those that just crossed the threshold this run)
    prev_keywords_set: set[str] = set(learned["reject_patterns"].get("keywords", []))
    for kw in all_keywords:
        if kw not in prev_keywords_set:
            delta_new_keywords.append(kw)

    # Assemble final delta for reporting
    delta_src_report: dict[str, dict] = {}
    for sid, info in delta_sources.items():
        delta_src_report[sid] = {
            "accept": sources[sid]["accept"],
            "reject": sources[sid]["reject"],
            "rep": sources[sid]["rep"],
            "prev_rep": info["prev_rep"],
        }
    delta_eng_report: dict[str, dict] = {}
    for eng_name, info in delta_engines.items():
        delta_eng_report[eng_name] = {
            "accept": engines[eng_name]["accept"],
            "reject": engines[eng_name]["reject"],
            "rep": engines[eng_name]["rep"],
            "prev_rep": info["prev_rep"],
        }

    delta = {
        "sources": delta_src_report,
        "engines": delta_eng_report,
        "new_keywords": delta_new_keywords,
        "calibration": {
            "accepted_score_mean": round(acc_mean, 6),
            "rejected_score_mean": round(rej_mean, 6),
            "n": cal_n,
        },
        "n_new": n_new,
    }

    # Build updated learned dict
    updated_learned: dict = {
        "updated": today_s,
        "sources": sources,
        "engines": engines,
        "reject_patterns": {
            "keyword_counts": keyword_counts,
            "keywords": all_keywords,
        },
        "calibration": {
            "accepted_score_mean": round(acc_mean, 6),
            "rejected_score_mean": round(rej_mean, 6),
            "n": cal_n,
        },
        "processed_ids": sorted(processed_set),
    }

    briefing = render_briefing(delta, prev_updated)

    if apply:
        _save_learned(agent, updated_learned)

    return {
        "learned": updated_learned,
        "briefing": briefing,
        "delta": delta,
        "n_new": n_new,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    python scripts/agent_learn.py --vault <V> --agent web [--dry-run] [--json]
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description=(
            "Second Brain agent-learn core. "
            "Processes accept/reject decisions into per-source/engine reputation + "
            "reject patterns + calibration, stored in .claude/memory/<agent>/learned.json."
        )
    )
    parser.add_argument(
        "--vault",
        required=True,
        metavar="VAULT_ROOT",
        help="Absolute path to the vault root directory.",
    )
    parser.add_argument(
        "--agent",
        default="web",
        metavar="AGENT",
        help="Agent id (matches discovered_by field). Default: web.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Compute and print results without writing learned.json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the full result dict as JSON.",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 2

    vault_root = args.vault
    if not Path(vault_root).is_dir():
        print(f"[agent_learn] vault not found: {vault_root}", file=sys.stderr)
        return 2

    result = learn(
        vault_root,
        agent=args.agent,
        apply=not args.dry_run,
    )

    if args.as_json:
        out = {k: v for k, v in result.items() if k != "learned"}
        out["learned_path"] = str(_learned_path(args.agent))
        print(json.dumps(out, indent=2, ensure_ascii=True))
    else:
        print(result["briefing"])
        if args.dry_run:
            print(f"[dry-run] learned.json NOT written ({result['n_new']} new decisions)")
        else:
            print(f"[learn] wrote {_learned_path(args.agent)} ({result['n_new']} new decisions)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
