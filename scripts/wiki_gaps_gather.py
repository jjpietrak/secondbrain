#!/usr/bin/env python3
"""wiki_gaps_gather.py -- deterministic wiki-state gather for the wiki-gaps skill.

Walks wiki/ (skipping infrastructure files), extracts each page's frontmatter,
"## For future Claude" preamble, and "## Open Questions" section verbatim, then
assembles the JSON payload that WIKI_GAP_PROMPT expects.

Public surface
--------------
gather_wiki_state(vault_root) -> dict
    Main entry point.  Returns a payload dict with keys:
        purpose      : str  (vault purpose, or "not yet defined")
        today        : str  (ISO date, e.g. "2026-06-21")
        active_topics: str  (formatted topic list, or "none")
        wiki_state   : str  (formatted wiki-state block for WIKI_GAP_PROMPT)

        open_question_count: int  (count of open-question items harvested)
        page_count          : int (pages included in wiki_state)

Exclusion rules
---------------
Skips files whose name matches:
  - _template (any name starting with "_template")
  - index.md, hot.md, log.md, gaps.md  (infrastructure files)
  - any file whose frontmatter "type" is in SKIP_TYPES

Section extraction
------------------
"## For future Claude" preamble: the block between this header and the next "##"
heading (or end of file if the preamble is the last section).  Included verbatim.

"## Open Questions" section: the block between this header and the next "##" heading.
Each bullet under this heading is one harvested open-question item.

Vault-root resolution
---------------------
Explicit CLI arg takes precedence; then $VAULT_ROOT / $VAULT_PATH env vars;
then subprocess call to agents.vault_config.

CLI usage
---------
    python scripts/wiki_gaps_gather.py <vault_root> [--output-json]
    python scripts/wiki_gaps_gather.py --output-json   # uses env / vault_config
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Repo-root on sys.path so sibling package imports work when called directly.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Infrastructure files to always skip (vault-relative names, no subfolder).
SKIP_NAMES = {"index.md", "hot.md", "log.md", "gaps.md"}

# Frontmatter type values that indicate infrastructure pages (skip them too).
SKIP_TYPES = {"index", "hot", "gaps_report"}

# Max chars per page included in wiki_state (keeps the prompt manageable).
MAX_PAGE_CHARS = 3000

# Max total chars for the wiki_state block (safety cap for very large vaults).
MAX_WIKI_STATE_CHARS = 80_000

# Section headers we look for (lowercase for matching).
FFC_HEADER = "## for future claude"
OQ_HEADER = "## open questions"


# ---------------------------------------------------------------------------
# Vault-root resolution
# ---------------------------------------------------------------------------

def _resolve_vault_root(explicit: Optional[str | Path] = None) -> Path:
    """Resolve the vault root path.  Priority: explicit arg > env > vault_config."""
    if explicit is not None:
        return Path(explicit)
    for ev in ("VAULT_ROOT", "VAULT_PATH"):
        val = os.environ.get(ev)
        if val:
            return Path(val)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agents.vault_config", "path"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        if result.returncode == 0:
            p = result.stdout.strip()
            if p:
                return Path(p)
    except Exception:
        pass
    raise RuntimeError(
        "Cannot resolve vault root: pass it as a CLI arg or set $VAULT_ROOT."
    )


# ---------------------------------------------------------------------------
# Frontmatter parser (minimal, dependency-free)
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text).

    Handles only the YAML frontmatter block between leading "---" fences.
    Values are kept as raw strings (no full YAML parse to avoid PyYAML dep
    at test time - tests supply controlled fixtures).
    """
    fm: dict = {}
    body = content
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return fm, content
    # Find the closing fence.
    rest = stripped[3:]  # skip opening "---"
    close = rest.find("\n---")
    if close == -1:
        return fm, content
    fm_text = rest[:close]
    body = rest[close + 4:].lstrip()
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip().lower()] = val.strip()
    return fm, body


# ---------------------------------------------------------------------------
# Section extractor
# ---------------------------------------------------------------------------

def _extract_section(body: str, header_lower: str) -> str:
    """Extract the body of the section starting with header_lower.

    Returns the text between that header line and the next "## " heading
    (or end of body).  Returns "" if the section is absent.
    The returned text does NOT include the header line itself.

    Matches a heading when the normalized line STARTS WITH the target
    (case-insensitive), tolerating trailing parenthetical/suffix after the
    heading text (e.g., "## Open questions (TBD for [[iris-tetra]])").
    """
    lines = body.splitlines()
    in_section = False
    section_lines: list[str] = []
    for line in lines:
        line_lower = line.strip().lower()
        # Check if this line starts with the target header.
        # Allow trailing parenthetical suffixes.
        if line_lower.startswith(header_lower):
            # Verify it's an actual level-2 heading, not a substring inside body text.
            if line.strip().startswith("## "):
                in_section = True
                continue
        if in_section:
            # Stop at the next level-2 heading (## ).
            if line.startswith("## "):
                break
            section_lines.append(line)
    return "\n".join(section_lines).strip()


# ---------------------------------------------------------------------------
# Open-question bullet counter
# ---------------------------------------------------------------------------

def _count_oq_bullets(oq_text: str) -> int:
    """Count the number of bullet items in an open-questions section."""
    return sum(
        1 for line in oq_text.splitlines()
        if line.strip().startswith(("-", "*", "+"))
    )


# ---------------------------------------------------------------------------
# Per-page entry builder
# ---------------------------------------------------------------------------

def _build_page_entry(path: Path, vault_root: Path) -> Optional[dict]:
    """Build a per-page entry dict for one wiki page.

    Returns None if the page should be skipped.
    Keys: rel_path, title, ffc, open_questions, excerpt, oq_count
    """
    # Skip infrastructure names regardless of subfolder.
    if path.name.startswith("_template") or path.name in SKIP_NAMES:
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    fm, body = _parse_frontmatter(content)
    if fm.get("type", "") in SKIP_TYPES:
        return None
    rel_path = str(path.relative_to(vault_root))
    title = path.stem.replace("-", " ").replace("_", " ").title()
    ffc = _extract_section(body, FFC_HEADER)
    oq_text = _extract_section(body, "## open questions")
    oq_count = _count_oq_bullets(oq_text)
    # Excerpt: first MAX_PAGE_CHARS chars of the body (includes ffc + oq in order).
    excerpt = body[:MAX_PAGE_CHARS].strip()
    return {
        "rel_path": rel_path,
        "title": title,
        "ffc": ffc,
        "open_questions": oq_text,
        "excerpt": excerpt,
        "oq_count": oq_count,
    }


# ---------------------------------------------------------------------------
# Purpose reader
# ---------------------------------------------------------------------------

def _read_purpose(vault_root: Path) -> str:
    """Read vault purpose from objective/purpose/PURPOSE.md or vault_config."""
    purpose_path = vault_root / "objective" / "purpose" / "PURPOSE.md"
    if purpose_path.exists():
        try:
            content = purpose_path.read_text(encoding="utf-8", errors="ignore")
            _, body = _parse_frontmatter(content)
            body = body.strip()
            if body:
                return body[:2000]  # cap to avoid bloating the prompt
        except OSError:
            pass
    # Fallback: vault_config purpose one-liner.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agents.vault_config", "purpose"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        if result.returncode == 0:
            p = result.stdout.strip()
            if p:
                return p
    except Exception:
        pass
    return "not yet defined"


# ---------------------------------------------------------------------------
# Active topics reader
# ---------------------------------------------------------------------------

def _read_active_topics(vault_root: Path) -> str:
    """Read active topics from objective/topic/*.md.

    Returns a formatted string for {active_topics} in the prompt.
    Returns "none" if no topics exist.
    """
    topics_dir = vault_root / "objective" / "topic"
    if not topics_dir.exists():
        return "none"
    entries: list[str] = []
    for topic_path in sorted(topics_dir.glob("*.md")):
        if topic_path.name.startswith("_template"):
            continue
        try:
            content = topic_path.read_text(encoding="utf-8", errors="ignore")
            fm, body = _parse_frontmatter(content)
            status = fm.get("status", "active")
            if status not in {"active", ""}:
                continue  # skip paused/completed topics
            tid = fm.get("id", topic_path.stem)
            # First non-empty body line as the topic summary.
            summary = next(
                (ln.strip() for ln in body.splitlines() if ln.strip()), topic_path.stem
            )
            entries.append(f"- {tid}: {summary}")
        except OSError:
            continue
    return "\n".join(entries) if entries else "none"


# ---------------------------------------------------------------------------
# Wiki-state formatter
# ---------------------------------------------------------------------------

def _format_wiki_state(pages: list[dict]) -> str:
    """Format the wiki-state block for WIKI_GAP_PROMPT.

    Each page entry includes:
    - A header with the vault-relative path as a wikilink.
    - The "## For future Claude" preamble (verbatim, if present).
    - The "## Open Questions" section (verbatim, if present).
    - The body excerpt (first MAX_PAGE_CHARS chars, truncated if needed).
    """
    chunks: list[str] = []
    total = 0
    for page in pages:
        header = f"### [[{page['rel_path']}]]"
        parts = [header]
        if page["ffc"]:
            parts.append(f"#### For future Claude\n{page['ffc']}")
        if page["open_questions"]:
            parts.append(f"#### Open Questions\n{page['open_questions']}")
        # Excerpt: show the start of the body (includes any non-section prose).
        if page["excerpt"]:
            parts.append(page["excerpt"])
        entry = "\n\n".join(parts)
        # Check total cap.
        if total + len(entry) > MAX_WIKI_STATE_CHARS:
            chunks.append("... (additional pages omitted - total char cap reached)")
            break
        chunks.append(entry)
        total += len(entry)
    return "\n\n---\n\n".join(chunks) if chunks else "(no wiki pages found)"


# ---------------------------------------------------------------------------
# Main gather function
# ---------------------------------------------------------------------------

def gather_wiki_state(vault_root: Optional[str | Path] = None) -> dict:
    """Gather the wiki state needed to fill WIKI_GAP_PROMPT.

    Parameters
    ----------
    vault_root : str | Path | None
        Absolute path to the vault root.  Resolved from env / vault_config when None.

    Returns
    -------
    dict with keys:
        purpose, today, active_topics, wiki_state,
        open_question_count, page_count
    """
    root = _resolve_vault_root(vault_root)
    wiki_dir = root / "wiki"
    today = date.today().isoformat()

    # Gather all wiki pages (recursive, all subfolders).
    pages: list[dict] = []
    if wiki_dir.exists():
        for path in sorted(wiki_dir.rglob("*.md")):
            entry = _build_page_entry(path, root)
            if entry is not None:
                pages.append(entry)

    # Sort: pages with open-question items first (highest signal), then alphabetical.
    pages.sort(key=lambda p: (-p["oq_count"], p["rel_path"]))

    open_question_count = sum(p["oq_count"] for p in pages)
    wiki_state = _format_wiki_state(pages)

    purpose = _read_purpose(root)
    active_topics = _read_active_topics(root)

    return {
        "purpose": purpose,
        "today": today,
        "active_topics": active_topics,
        "wiki_state": wiki_state,
        "open_question_count": open_question_count,
        "page_count": len(pages),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Gather wiki state for the wiki-gaps skill (deterministic, no LLM)."
    )
    parser.add_argument(
        "vault_root",
        nargs="?",
        default=None,
        help="Absolute path to the vault root (overrides $VAULT_ROOT).",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Print the payload as JSON to stdout (default: human-readable summary).",
    )
    args = parser.parse_args()

    payload = gather_wiki_state(args.vault_root)

    if args.output_json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(f"Vault root  : {_resolve_vault_root(args.vault_root)}")
        print(f"Pages scanned: {payload['page_count']}")
        print(f"Open-question items: {payload['open_question_count']}")
        print(f"Today       : {payload['today']}")
        print(f"Purpose     : {payload['purpose'][:120]}...")
        print(f"Topics      :\n{payload['active_topics']}")


if __name__ == "__main__":
    main()
