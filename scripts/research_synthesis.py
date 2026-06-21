#!/usr/bin/env python3
"""research_synthesis.py -- shared helper for obj-synth and deep-synthesis skills.

Pure, decoupled, NO LLM calls.  No import of agents.objectives.  All roots are
passed as arguments so the helper is parallel-safe and fully unit-testable.

Public surface
--------------
gather_local_context(topic_or_question, wiki_root, objective_nodes) -> list[dict]
    Keyword-scored scan over local wiki/ files + pre-loaded objective_nodes.
    Returns a list of excerpt dicts (see below).  No network, no LLM.

excerpts_to_wiki_baseline(excerpts) -> str
    Format excerpt list as the {wiki_baseline} block expected by both research
    pipeline prompts.

fill_analysis_prompt(purpose, today, open_questions, active_topics,
                     wiki_baseline, wiki_gaps) -> str
    Fill RESEARCH_ANALYSIS_PROMPT with the supplied values.

fill_synthesis_prompt(purpose, today, open_questions, active_topics,
                      gap_analysis, existing_directions) -> str
    Fill RESEARCH_SYNTHESIS_PROMPT with the supplied values.

parse_directions / parse_proposals
    Re-exported from scripts.prompts.pipeline_prompts -- import here so callers
    only need one import.

Excerpt dict schema
-------------------
{
  "path": str,            # vault-relative path, e.g. "wiki/concepts/Foo.md"
  "abs_path": str,        # absolute path on disk
  "score": int,           # keyword hit score
  "excerpt": str,         # first MAX_EXCERPT_CHARS chars of the file
  "source": "wiki" | "objective",
}

Design constraints (from plans/phase-2-objectives.md RESOLVED block)
---------------------------------------------------------------------
- R5+R7: ONE helper used by both obj-synth and deep-synthesis; no duplication.
- NO web.  NO Perplexity.  NO LLM call inside the helper.
- Prefer taking roots as args; use agents.vault_config ONLY as a fallback when
  wiki_root is None.
- Decoupled from agents.objectives: objective_nodes are passed as pre-loaded
  dicts (the skill/agent loads them via `python -m agents.objectives frontier
  --json`; the helper just filters them).
"""

from __future__ import annotations

import re
import os
import sys
from pathlib import Path
from typing import Optional

# Ensure repo root on sys.path so sibling package imports resolve when called
# directly (e.g. `python scripts/research_synthesis.py`).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.prompts.pipeline_prompts import (  # noqa: E402
    RESEARCH_ANALYSIS_PROMPT,
    RESEARCH_SYNTHESIS_PROMPT,
    parse_directions,
    parse_proposals,
)

# Re-export so callers can `from scripts.research_synthesis import parse_directions`
__all__ = [
    "gather_local_context",
    "excerpts_to_wiki_baseline",
    "fill_analysis_prompt",
    "fill_synthesis_prompt",
    "parse_directions",
    "parse_proposals",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Vault sub-dirs scanned for wiki context (mirrors OSB VAULT_SCAN_DIRS, adapted
# to our schema: wiki/ only; objective/ content is passed as pre-loaded nodes).
WIKI_SCAN_DIRS = ["wiki"]

MAX_WIKI_HITS = 8          # top-N wiki pages returned
MAX_EXCERPT_CHARS = 1500   # chars per excerpt (matches OSB MAX_BASELINE_CHARS_PER_NOTE)

# Objective node types included when scanning objective_nodes for context.
OBJECTIVE_CONTEXT_TYPES = {"research_question", "direction", "topic", "purpose"}


# ---------------------------------------------------------------------------
# Vault-root resolution (lazy, fallback only)
# ---------------------------------------------------------------------------

def _resolve_wiki_root(wiki_root: Optional[Path | str]) -> Path:
    """Return the wiki_root to use.  Takes an explicit arg; falls back to
    agents.vault_config only when None is passed."""
    if wiki_root is not None:
        return Path(wiki_root)
    # Env vars set by `eval "$(python -m agents.vault_config env)"`
    for ev in ("VAULT_ROOT", "VAULT_PATH"):
        val = os.environ.get(ev)
        if val:
            return Path(val)
    # Last resort: subprocess call to vault_config (avoids hard import of agents)
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agents.vault_config", "path"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    raise RuntimeError(
        "Cannot resolve vault root: pass wiki_root explicitly or set $VAULT_ROOT."
    )


# ---------------------------------------------------------------------------
# Keyword scorer (adapted from OSB vault_scan -- local wiki files only)
# ---------------------------------------------------------------------------

def _keywords(text: str) -> list[str]:
    """Split text into lowercase tokens longer than 2 chars."""
    return [w for w in re.split(r"\s+", text.lower()) if len(w) > 2]


def _score_path_and_content(path: Path, vault_root: Path, keywords: list[str]) -> int:
    """Return a combined keyword score for a file."""
    try:
        content = path.read_text(errors="ignore").lower()
    except OSError:
        return 0
    content_score = sum(content.count(k) for k in keywords)
    rel = str(path.relative_to(vault_root)).lower()
    path_score = sum(k in rel for k in keywords) * 5
    return content_score + path_score


# ---------------------------------------------------------------------------
# Core: gather_local_context
# ---------------------------------------------------------------------------

def gather_local_context(
    topic_or_question: str,
    wiki_root: Optional[Path | str] = None,
    objective_nodes: Optional[list[dict]] = None,
) -> list[dict]:
    """Keyword-scored scan over local wiki/ files + pre-loaded objective_nodes.

    Parameters
    ----------
    topic_or_question : str
        Free-text topic, question text, or question id (e.g. "Q-0001 latency
        floor inference").  Used to derive scoring keywords.
    wiki_root : Path | str | None
        Absolute path to the vault root (the folder that contains wiki/).
        Resolved via env / vault_config when None.
    objective_nodes : list[dict] | None
        Pre-loaded objective node dicts (as returned by `agents.objectives
        frontier`).  Nodes whose `type` is in OBJECTIVE_CONTEXT_TYPES and whose
        body text / title scores > 0 are included in the result.  Pass [] or
        None to skip objective context.

    Returns
    -------
    list[dict]
        Excerpt dicts sorted by score descending.  Each dict:
          path, abs_path, score, excerpt, source
    """
    vault_root = _resolve_wiki_root(wiki_root)
    keywords = _keywords(topic_or_question)
    if not keywords:
        return []

    hits: list[dict] = []

    # -- wiki/ scan -------------------------------------------------------
    for subdir in WIKI_SCAN_DIRS:
        root = vault_root / subdir
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            # Skip templates and index files
            if path.name.startswith("_template") or path.name in {
                "index.md", "hot.md", "log.md",
            }:
                continue
            score = _score_path_and_content(path, vault_root, keywords)
            if score <= 0:
                continue
            try:
                excerpt = path.read_text(errors="ignore")[:MAX_EXCERPT_CHARS].strip()
            except OSError:
                excerpt = ""
            rel = str(path.relative_to(vault_root))
            hits.append({
                "path": rel,
                "abs_path": str(path),
                "score": score,
                "excerpt": excerpt,
                "source": "wiki",
            })

    # -- objective_nodes scan ---------------------------------------------
    for node in (objective_nodes or []):
        ntype = node.get("type", "")
        if ntype not in OBJECTIVE_CONTEXT_TYPES:
            continue
        # Build a searchable text from node fields
        node_text = " ".join(str(v) for v in node.values() if isinstance(v, str))
        node_kw = _keywords(node_text)
        # Score: count keyword overlaps between the query keywords and node text
        score = sum(node_text.lower().count(k) for k in keywords)
        if score <= 0:
            continue
        node_id = node.get("id", "")
        node_path = node.get("path", f"objective/{ntype}/{node_id}.md")
        hits.append({
            "path": node_path,
            "abs_path": node.get("abs_path", node_path),
            "score": score,
            "excerpt": node_text[:MAX_EXCERPT_CHARS].strip(),
            "source": "objective",
        })

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:MAX_WIKI_HITS]


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def excerpts_to_wiki_baseline(excerpts: list[dict]) -> str:
    """Format excerpt list as the {wiki_baseline} block for pipeline prompts.

    Output format mirrors OSB load_baseline:
      ### [[path]] (score=N)

      <excerpt>

    Returns the fallback string when excerpts is empty.
    """
    if not excerpts:
        return "(vault has no existing notes referencing this topic)"
    chunks = []
    for h in excerpts:
        chunks.append(
            f"### [[{h['path']}]] (score={h['score']})\n\n{h['excerpt'].strip()}"
        )
    return "\n\n---\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Prompt-fill functions
# ---------------------------------------------------------------------------

def fill_analysis_prompt(
    purpose: str,
    today: str,
    open_questions: str,
    active_topics: str,
    wiki_baseline: str,
    wiki_gaps: str = "(none)",
) -> str:
    """Fill RESEARCH_ANALYSIS_PROMPT with the supplied values.

    All arguments are plain strings (already formatted by the caller).
    No LLM call -- returns the filled prompt string ready to send to a model.
    """
    return RESEARCH_ANALYSIS_PROMPT.format(
        purpose=purpose,
        today=today,
        open_questions=open_questions,
        active_topics=active_topics,
        wiki_baseline=wiki_baseline,
        wiki_gaps=wiki_gaps,
    )


def fill_synthesis_prompt(
    purpose: str,
    today: str,
    open_questions: str,
    active_topics: str,
    gap_analysis: str,
    existing_directions: str = "(none)",
) -> str:
    """Fill RESEARCH_SYNTHESIS_PROMPT with the supplied values.

    All arguments are plain strings (already formatted by the caller).
    No LLM call -- returns the filled prompt string ready to send to a model.
    """
    return RESEARCH_SYNTHESIS_PROMPT.format(
        purpose=purpose,
        today=today,
        open_questions=open_questions,
        active_topics=active_topics,
        gap_analysis=gap_analysis,
        existing_directions=existing_directions,
    )
