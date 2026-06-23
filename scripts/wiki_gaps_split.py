#!/usr/bin/env python3
"""wiki_gaps_split.py -- split a gap-analysis markdown into per-gap files.

This is the WRITER for the per-gap structure.  It reads the structured text
produced by WIKI_GAP_PROMPT (via wiki_gaps_fill.py) and splits it into:

  wiki/gap/GAP-NN-<slug>.md   -- one file per Knowledge Gap block
  wiki/gap/index.md           -- Coverage Map + Stale + Self-contained +
                                  Open-Question Harvest + a Gap-files table

web_decision.parse_gaps reads wiki/gap/*.md (the individual gap files) to
build the crawl plan.

Public surface
--------------
slugify(title: str) -> str
    Lowercase, alphanumeric separated by hyphens, max ~45 chars.

parse_analysis(md: str) -> dict
    Parse a gap-analysis markdown (WIKI_GAP_PROMPT output or gaps.md) into::

        {
          "gaps": [
            {
              "id": "GAP-01",
              "title": "...",
              "shows_up_in": [...],
              "missing": "...",
              "fillable_by": [...],
              "topics": [...],
              "priority": "high|medium|low",
              "why": "...",
            },
            ...
          ],
          "coverage_map": "...",
          "stale": "...",
          "self_contained": "...",
          "open_questions": "...",
        }

    Robust to missing sections (returns empty string / empty list).

split(md, vault_root, *, apply=False, today=None) -> dict
    Render per-gap content and the index.  When apply=False (dry-run),
    returns {"files": [{path, content}, ...], "index": {path, content}}
    and writes NOTHING.  When apply=True, writes the files under
    vault_root/wiki/gap/ and returns {"written": [path, ...]}.
    Never deletes files it did not generate (the caller removes gaps.md).

CLI
---
    python scripts/wiki_gaps_split.py --analysis <path|-> --vault <root> [--apply] [--json]

    -  or --stdin: read analysis markdown from stdin.
    --apply: write files (default: dry-run, print plan).
    --json: emit JSON output instead of human-readable plan.

Exit codes
----------
  0  success
  2  usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(title: str, max_len: int = 45) -> str:
    """Convert a gap title to a filesystem-safe slug.

    Lowercase, alphanumeric tokens separated by hyphens, collapsed, stripped,
    max ``max_len`` characters (hard-truncated at a hyphen boundary).

    >>> slugify("Optical prior art -- citations [3]-[6] not ingested")
    'optical-prior-art-citations-3-6-not-ingested'
    """
    lower = title.lower()
    slug = _SLUG_STRIP.sub("-", lower).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


# ---------------------------------------------------------------------------
# Section extraction helpers
# ---------------------------------------------------------------------------

def _extract_section(md: str, header: str) -> str:
    """Return the body of a ## <header> section (up to the next ## heading).

    Returns empty string if the section is not found.
    Strips the trailing READY terminator (if the section is the last one).
    """
    pattern = re.compile(
        r"^##\s+" + re.escape(header) + r"[^\n]*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(md)
    if not m:
        return ""
    body = m.group(1).strip()
    # Remove trailing READY terminator (produced by WIKI_GAP_PROMPT)
    body = re.sub(r"\n*\bREADY\s*$", "", body).strip()
    return body


def _extract_open_questions_section(md: str) -> str:
    """Extract ## Open-Question Harvest section body."""
    pattern = re.compile(
        r"^##\s+Open-Question Harvest[^\n]*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(md)
    if not m:
        return ""
    return m.group(1).strip()


# ---------------------------------------------------------------------------
# Frontmatter + field parsers
# ---------------------------------------------------------------------------

_PRIORITY_WORD_RE = re.compile(r"^(high|medium|low)\b", re.IGNORECASE)
_TOPIC_RE = re.compile(r"T-\d+")
_FILLABLE_TAG_RE = re.compile(r"\b(arxiv|web|github|forum|x)\b", re.IGNORECASE)


def _parse_gap_block(gap_id: str, title: str, block: str) -> dict:
    """Extract structured fields from a single ### GAP-NN: ... block body."""

    def _field(name: str) -> str:
        m = re.search(
            rf"^-\s+{re.escape(name)}:\s*(.+?)$", block, re.MULTILINE | re.DOTALL
        )
        if not m:
            return ""
        val = m.group(1)
        stop = re.search(r"\n-\s+\w", val)
        if stop:
            val = val[: stop.start()]
        return val.strip()

    shows_up_in_raw = _field("shows_up_in")
    missing = _field("missing")
    fillable_by_raw = _field("fillable_by")
    topic_raw = _field("topic")
    priority_raw = _field("priority")

    # shows_up_in: split on ';' then strip whitespace
    shows_up_in = [s.strip() for s in re.split(r"\s*;\s*", shows_up_in_raw) if s.strip()]

    # fillable_by: bare engine tags only (strip parenthetical notes)
    fillable_by = list(
        dict.fromkeys(t.lower() for t in _FILLABLE_TAG_RE.findall(fillable_by_raw))
    )

    # topics: T-NNNN patterns
    topics = _TOPIC_RE.findall(topic_raw)

    # priority: first word only; why = remainder
    pm = _PRIORITY_WORD_RE.match(priority_raw)
    priority = pm.group(1).lower() if pm else "medium"
    # "why" is the text after the first word of the priority line
    why_raw = priority_raw[pm.end():].strip(" -\t") if pm else ""
    # strip leading separators like " - ", "- ", "--"
    why = re.sub(r"^[-\s]+", "", why_raw).strip()

    return {
        "id": gap_id,
        "title": title,
        "shows_up_in": shows_up_in,
        "missing": missing,
        "fillable_by": fillable_by,
        "topics": topics,
        "priority": priority,
        "why": why,
    }


# ---------------------------------------------------------------------------
# parse_analysis
# ---------------------------------------------------------------------------

def parse_analysis(md: str) -> dict:
    """Parse a gap-analysis markdown into a structured dict.

    Parameters
    ----------
    md : str
        The raw gap-analysis text (WIKI_GAP_PROMPT output or an existing
        gaps.md body).

    Returns
    -------
    dict with keys:
      gaps           : list of gap dicts (see module docstring)
      coverage_map   : str
      stale          : str
      self_contained : str
      open_questions : str
    """
    # -- Knowledge Gaps section --
    kg_match = re.search(r"^##\s+Knowledge Gaps\s*$", md, re.MULTILINE)
    gaps: list[dict] = []
    if kg_match:
        kg_text = md[kg_match.end():]
        next_sec = re.search(r"^##\s+", kg_text, re.MULTILINE)
        if next_sec:
            kg_text = kg_text[: next_sec.start()]

        blocks = re.split(r"(?=^###\s+GAP-)", kg_text, flags=re.MULTILINE)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            hm = re.match(r"^###\s+(GAP-\d+):\s*(.+)$", block, re.MULTILINE)
            if not hm:
                continue
            gap_id = hm.group(1)
            title = hm.group(2).strip()
            gaps.append(_parse_gap_block(gap_id, title, block))

    coverage_map = _extract_section(md, "Coverage Map")
    stale = _extract_section(md, "Stale / Unverified")
    self_contained = _extract_section(md, "Self-contained")
    open_questions = _extract_open_questions_section(md)

    return {
        "gaps": gaps,
        "coverage_map": coverage_map,
        "stale": stale,
        "self_contained": self_contained,
        "open_questions": open_questions,
    }


# ---------------------------------------------------------------------------
# Gap-file renderer
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _parse_yaml_scalar(raw: str) -> str:
    """Parse a raw YAML scalar value (possibly quoted by safe_dump) back to a plain str.

    For example, the string ``'2026-06-23'`` (with surrounding single quotes as
    emitted by yaml.safe_dump for ISO-date strings) is returned as ``2026-06-23``.
    """
    try:
        val = yaml.safe_load("v: " + raw)["v"]
        return str(val)
    except Exception:
        return raw.strip("'\"")


def _render_frontmatter(fm_dict: OrderedDict) -> str:
    """Render a frontmatter dict as a YAML block wrapped in --- fences.

    Uses yaml.safe_dump so embedded double-quotes, colons, brackets, and
    unicode all round-trip safely through yaml.safe_load.  Key order is
    preserved (Python 3.7+ dicts / OrderedDict).
    """
    body = yaml.safe_dump(
        dict(fm_dict),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return "---\n" + body + "---"


def _render_gap_file(
    gap: dict,
    created: str,
    updated: str,
) -> str:
    """Render the full content of a wiki/gap/GAP-NN-<slug>.md file."""
    fm: OrderedDict = OrderedDict([
        ("type", "gap"),
        ("id", gap["id"]),
        ("title", gap["title"]),
        ("status", "open"),
        ("topics", gap["topics"]),
        ("fillable_by", gap["fillable_by"]),
        ("priority", gap["priority"]),
        ("shows_up_in", gap["shows_up_in"]),
        ("created", created),
        ("updated", updated),
        ("written_by", "wiki"),
    ])

    lines = [
        _render_frontmatter(fm),
        "",
        "## Missing",
        gap["missing"] or "(no missing text captured)",
        "",
    ]

    if gap.get("why"):
        lines += [
            "## Why",
            gap["why"],
            "",
        ]

    # Open questions section is omitted when empty
    # (caller injects per-gap OQ lines if desired; here we leave it to the LLM)

    return "\n".join(lines)


def _render_index(
    parsed: dict,
    gaps: list[dict],
    created: str,
    updated: str,
) -> str:
    """Render wiki/gap/index.md."""
    fm: OrderedDict = OrderedDict([
        ("type", "gaps_index"),
        ("written_by", "wiki"),
        ("created", created),
        ("updated", updated),
    ])
    header = _render_frontmatter(fm) + "\n\n"

    sections: list[str] = [header]

    if parsed["coverage_map"]:
        sections.append("## Coverage Map\n" + parsed["coverage_map"] + "\n")

    if parsed["stale"]:
        sections.append("## Stale / Unverified\n" + parsed["stale"] + "\n")

    if parsed["self_contained"]:
        sections.append("## Self-contained\n" + parsed["self_contained"] + "\n")

    if parsed["open_questions"]:
        sections.append(
            "## Open-Question Harvest (TOP PRIORITY)\n"
            + parsed["open_questions"]
            + "\n"
        )

    # Gap files table
    table_lines = [
        "## Gap files",
        "",
        "| File | Priority | Topics |",
        "| --- | --- | --- |",
    ]
    for g in gaps:
        slug = slugify(g["title"])
        link = f"[[wiki/gap/{g['id']}-{slug}]]"
        priority = g["priority"]
        topics = ", ".join(g["topics"]) if g["topics"] else "-"
        table_lines.append(f"| {link} | {priority} | {topics} |")

    sections.append("\n".join(table_lines) + "\n")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# split()
# ---------------------------------------------------------------------------

def split(
    md: str,
    vault_root: Any,
    *,
    apply: bool = False,
    today: str | None = None,
) -> dict:
    """Split a gap-analysis markdown into per-gap files.

    Parameters
    ----------
    md : str
        Gap-analysis markdown text.
    vault_root : str or Path
        Vault root directory (wiki/gap/ will be created inside it).
    apply : bool
        When False (default), return the planned file contents and write nothing.
        When True, write the files under vault_root/wiki/gap/.
    today : str or None
        ISO date string to use as ``updated`` (default: today's date).

    Returns
    -------
    When apply=False:
        {"files": [{path, content}, ...], "index": {path, content}}
    When apply=True:
        {"written": [absolute_path_str, ...]}
    """
    vault_root = Path(vault_root)
    gap_dir = vault_root / "wiki" / "gap"
    today_str = today or date.today().isoformat()

    parsed = parse_analysis(md)

    # Build per-gap file list
    files: list[dict] = []
    for gap in parsed["gaps"]:
        slug = slugify(gap["title"])
        filename = f"{gap['id']}-{slug}.md"
        path = gap_dir / filename

        # Preserve created: if the file already exists
        created_str = today_str
        if apply and path.exists():
            existing_text = path.read_text(encoding="utf-8")
            cm = re.search(r"^created:\s*(.+)$", existing_text, re.MULTILINE)
            if cm:
                created_str = _parse_yaml_scalar(cm.group(1).strip())
        elif not apply:
            # In dry-run mode, try to check if it would exist
            if path.exists():
                existing_text = path.read_text(encoding="utf-8")
                cm = re.search(r"^created:\s*(.+)$", existing_text, re.MULTILINE)
                if cm:
                    created_str = _parse_yaml_scalar(cm.group(1).strip())

        content = _render_gap_file(gap, created=created_str, updated=today_str)
        files.append({"path": str(path), "content": content})

    # Build index
    # Determine index created date
    index_path = gap_dir / "index.md"
    index_created = today_str
    if index_path.exists():
        existing_idx = index_path.read_text(encoding="utf-8")
        cm = re.search(r"^created:\s*(.+)$", existing_idx, re.MULTILINE)
        if cm:
            index_created = _parse_yaml_scalar(cm.group(1).strip())

    index_content = _render_index(parsed, parsed["gaps"], created=index_created, updated=today_str)
    index_entry = {"path": str(index_path), "content": index_content}

    if not apply:
        return {"files": files, "index": index_entry}

    # Apply: write all files
    gap_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for f in files:
        p = Path(f["path"])
        p.write_text(f["content"], encoding="utf-8")
        written.append(str(p))

    index_path.write_text(index_content, encoding="utf-8")
    written.append(str(index_path))

    return {"written": written}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Split a gap-analysis markdown into per-gap wiki/gap/*.md files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--analysis",
        metavar="PATH|-",
        default=None,
        help="Path to the gap-analysis markdown file, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read analysis markdown from stdin (equivalent to --analysis -).",
    )
    parser.add_argument(
        "--vault",
        metavar="ROOT",
        required=True,
        help="Vault root directory.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write files (default: dry-run, print plan).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output.",
    )
    args = parser.parse_args(argv)

    # Read analysis markdown
    if args.stdin or args.analysis == "-":
        md = sys.stdin.read()
    elif args.analysis:
        md = Path(args.analysis).read_text(encoding="utf-8")
    else:
        parser.error("Provide --analysis <path> or --stdin / --analysis -")
        return 2

    result = split(md, args.vault, apply=args.apply)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.apply:
            written = result.get("written", [])
            print(f"Wrote {len(written)} file(s):")
            for p in written:
                print(f"  {p}")
        else:
            files = result.get("files", [])
            index = result.get("index", {})
            print(f"Dry-run: {len(files)} gap file(s) + 1 index would be written.")
            print(f"  Index: {index.get('path', '?')}")
            for f in files:
                print(f"  Gap:   {f['path']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
