#!/usr/bin/env python3
"""deep_synth_helper.py -- deterministic helper for the deep-synthesis skill.

NO LLM calls.  No import of agents.objectives.  All vault roots and input data
are passed as arguments so the helper is fully unit-testable and parallel-safe.

Public surface
--------------
resolve_output_path(vault_root, scope, today) -> Path
    Resolve the research/ output path for a deep-synthesis run.

    scope is a dict with ONE of:
      {"question": "Q-0001"}  -> research/Q-0001.md
      {"concept":  "kv-cache"} -> research/kv-cache.md  (concept slug scope)
      {"deep":     "some topic text"} -> research/deep/YYYY-MM-DD-<slug>.md

format_research_report_frontmatter(scope, today) -> str
    Return the YAML frontmatter block (---...---) for a research report file.
    Does NOT write anything.

format_direction_frontmatter(direction_id, today, fields) -> str
    Return the YAML frontmatter block for an objective/direction/ node.
    fields: dict with keys from parse_directions() e.g. serves_question, related, etc.

format_proposal_frontmatter(proposal_id, today, proposal_dict) -> str
    Return the YAML frontmatter block for an objective/research_question_proposal/ node.
    proposal_dict: one item from parse_proposals(), keys: proposal, rationale, from_gap.

format_already_answerable_surface(already_answerable_list) -> str
    Format the "Already Answerable" list for printing to stdout (not a vault write).
    Returns an empty string if the list is empty or ["none"].

slugify(text) -> str
    Convert free text into a filesystem-safe lowercase slug (ASCII only, hyphens).

Design constraints
------------------
- Pure helper: NO LLM, NO web, NO agents.objectives import.
- vault-relative paths only in frontmatter (never absolute paths).
- All string outputs use ASCII only (no em-dashes, curly quotes, Unicode math).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

__all__ = [
    "resolve_output_path",
    "format_research_report_frontmatter",
    "format_direction_frontmatter",
    "format_proposal_frontmatter",
    "format_already_answerable_surface",
    "slugify",
    "concept_slugs",
]

# ---------------------------------------------------------------------------
# concept-slug extraction + related: block rendering
# ---------------------------------------------------------------------------

# Mirror of agents.objectives.concept_slugs (this helper must not import that
# module).  Accepts ``[[wiki/concepts/<slug>]]`` and bare ``[[concepts/<slug>]]``,
# stripping any ``|alias`` / ``#anchor`` suffix.
_CONCEPT_LINK_RE = re.compile(
    r"\[\[\s*(?:wiki/)?concepts/([^\]|#]+?)\s*(?:[|#][^\]]*)?\]\]",
    re.IGNORECASE,
)


def concept_slugs(value) -> list[str]:
    """Extract concept slugs (order-preserving, de-duplicated) from text or a list.

    `value` may be raw prose, a frontmatter value, a bracketed inline list, or a
    Python list of link strings / bare slugs.  Bare slug strings (no ``[[...]]``)
    are accepted verbatim when passed inside a list.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(slug: str) -> None:
        slug = slug.strip()
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)

    if isinstance(value, (list, tuple)):
        for item in value:
            s = str(item)
            links = _CONCEPT_LINK_RE.findall(s)
            if links:
                for slug in links:
                    _add(slug)
            elif "[[" not in s:
                # bare slug like "kv-cache"
                _add(s)
    else:
        for slug in _CONCEPT_LINK_RE.findall(str(value or "")):
            _add(slug)

    return out


def _render_related_block(slugs: list[str]) -> str:
    """Render a `related:` frontmatter block from a list of concept slugs.

    Empty -> ``related: []`` (single line). Non-empty -> a YAML block list of
    quoted ``[[wiki/concepts/<slug>]]`` wikilinks matching the objective schema.
    """
    if not slugs:
        return "related: []"
    lines = ["related:"]
    for slug in slugs:
        lines.append(f'  - "[[wiki/concepts/{slug}]]"')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert free text into a filesystem-safe lowercase slug.

    - Lowercase
    - Replace non-alphanumeric runs with hyphens
    - Strip leading/trailing hyphens
    - Truncate to 60 chars (avoids overly long filenames)
    - ASCII only
    """
    s = text.lower()
    # Drop non-ASCII by encoding to ASCII with 'ignore'
    s = s.encode("ascii", "ignore").decode("ascii")
    # Replace runs of non-alphanumeric chars with a hyphen
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:60] or "untitled"


# ---------------------------------------------------------------------------
# resolve_output_path
# ---------------------------------------------------------------------------

def resolve_output_path(
    vault_root: Path,
    scope: dict,
    today: Optional[str] = None,
) -> Path:
    """Resolve the research/ output path for a deep-synthesis run.

    scope is a dict with ONE of the following keys:

      {"question": "Q-0001"}
          -> <vault_root>/research/Q-0001.md

      {"concept": "kv-cache"}
          -> <vault_root>/research/kv-cache.md
          (the concept slug becomes the filename; it is slugified defensively so
          a title or a bare slug both resolve deterministically)

      {"deep": "some topic text"}
          -> <vault_root>/research/deep/YYYY-MM-DD-<slug>.md

    vault_root : Path
        Absolute path to the vault root (the folder that contains research/).
    scope : dict
        One of the three shapes above.
    today : str | None
        ISO date string (YYYY-MM-DD). Defaults to date.today().isoformat().

    Returns the absolute Path to the output file (the parent dir is NOT created
    by this function - the skill creates it via mkdir -p before writing).
    """
    if today is None:
        today = date.today().isoformat()

    if "question" in scope:
        qid = scope["question"].strip()
        return vault_root / "research" / f"{qid}.md"

    if "concept" in scope:
        slug = slugify(scope["concept"])
        return vault_root / "research" / f"{slug}.md"

    if "deep" in scope:
        slug = slugify(scope["deep"])
        return vault_root / "research" / "deep" / f"{today}-{slug}.md"

    raise ValueError(
        f"scope must have one of 'question', 'concept', or 'deep'; got: {scope!r}"
    )


# ---------------------------------------------------------------------------
# format_research_report_frontmatter
# ---------------------------------------------------------------------------

def format_research_report_frontmatter(
    scope: dict,
    today: Optional[str] = None,
    concept_slug: str = "",
    question_id: str = "",
) -> str:
    """Return the YAML frontmatter block for a research report file.

    Fills the schema from plans/phase-2-objectives.md Section 6.2, with the
    subject axis carried by concept links in `related:` (the retired `topic:`
    field is gone):
      type: research_report
      written_by: research
      related: [[wiki/concepts/<slug>]] links (or []) tying the report to its
               subject concept(s)
      serves_question: Q-NNNN (empty string if not provided)
      created: YYYY-MM-DD
      status: draft

    The subject concept is taken from ``concept_slug`` (explicit) or ``scope["concept"]``;
    serves_question from ``question_id`` (explicit) or ``scope["question"]``.
    """
    if today is None:
        today = date.today().isoformat()

    # Resolve the subject concept and served question from explicit args or scope.
    # The concept value may be a bare slug ("kv-cache") or a full wikilink, so
    # pass it as a single-item list (concept_slugs accepts bare slugs in lists).
    raw_concept = concept_slug or scope.get("concept", "")
    slugs = concept_slugs([raw_concept]) if raw_concept else []
    q = question_id or scope.get("question", "")

    lines = [
        "---",
        "type: research_report",
        "written_by: research",
        _render_related_block(slugs),
        f"serves_question: {q}",
        f"created: {today}",
        "status: draft",
        "---",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_direction_frontmatter
# ---------------------------------------------------------------------------

def format_direction_frontmatter(
    direction_id: str,
    today: str,
    fields: dict,
    question_id: str = "",
) -> str:
    """Return YAML frontmatter for an objective/direction/ node.

    direction_id : str
        Formatted id string, e.g. "DIR-0001".
    today : str
        ISO date string.
    fields : dict
        As returned by parse_directions() - the 'fields' sub-dict.
        Expected keys: serves_question, related, targets_gap, reasoning_pattern,
        expected_evidence, seed_queries, solves_when, priority.
    question_id : str
        Optional override for serves_question (if the skill knows the concrete Q-id
        at write time and the field is missing or generic).

    Returns the frontmatter block string only (no body content).
    """
    serves = question_id or fields.get("serves_question", "")
    # Subject axis: concept links in related: (the retired topics: field is gone).
    slugs = concept_slugs(fields.get("related", ""))
    targets_gap = fields.get("targets_gap", "")
    priority_raw = fields.get("priority", "medium")
    # priority field may be "high  -  justification text" -- take first token
    priority = priority_raw.split()[0].lower() if priority_raw else "medium"
    if priority not in ("high", "medium", "low"):
        priority = "medium"

    lines = [
        "---",
        f"type: direction",
        f"id: {direction_id}",
        f"created: {today}",
        f"updated: {today}",
        "written_by: research",
        f"serves_question: {serves}",
        _render_related_block(slugs),
        f"targets_gap: {targets_gap}",
        f"priority: {priority}",
        "status: open",
        "---",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_proposal_frontmatter
# ---------------------------------------------------------------------------

def format_proposal_frontmatter(
    proposal_id: str,
    today: str,
    proposal_dict: dict,
) -> str:
    """Return YAML frontmatter for an objective/research_question_proposal/ node.

    proposal_id : str
        Formatted id string, e.g. "QP-0001".
    today : str
        ISO date string.
    proposal_dict : dict
        As returned by parse_proposals() - keys: proposal, rationale, from_gap.
    """
    from_gap = proposal_dict.get("from_gap", "").strip()

    lines = [
        "---",
        "type: research_question_proposal",
        f"id: {proposal_id}",
        f"created: {today}",
        f"updated: {today}",
        "written_by: research",
        f"from_gap: {from_gap}",
        "status: pending",
        "---",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_already_answerable_surface
# ---------------------------------------------------------------------------

def format_already_answerable_surface(already_answerable: list[str]) -> str:
    """Format the Already Answerable list for printing to stdout.

    Returns an empty string if the list is empty or contains only "none" entries.
    The output is plain text suitable for the research agent to print after synthesis.

    already_answerable : list[str]
        Items extracted from the '## Already Answerable' section of synthesis output.
        Each item is a question id or a free-text description.  Items that are
        literally "none" (case-insensitive) are filtered out.
    """
    real_items = [
        item.strip(" -")
        for item in already_answerable
        if item.strip(" -").lower() not in ("", "none")
    ]
    if not real_items:
        return ""

    lines = [
        "ALREADY ANSWERABLE - the vault can likely answer these (consider question-solve):",
    ]
    for item in real_items:
        lines.append(f"  - {item}")
    return "\n".join(lines)
