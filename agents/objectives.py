#!/usr/bin/env python3
"""Markdown-native objective store for Second Brain v0.2.

Scans the `objective/` folder tree, parses node frontmatter, and provides
functions for the research agent and research skills to query the objective
graph without ever reading JSON.

Node types and id schemes:
  purpose                    -> id: purpose (single file)
  topic                      -> id: T-NNNN
  research_question          -> id: Q-NNNN, solved: yes|no
  decision                   -> id: D-NNNN, status: active|superseded|archived
  research_question_proposal -> id: QP-NNNN, status: pending|approved|rejected
  direction                  -> id: DIR-NNNN, status: open|crawled|superseded
  agent_todo                 -> id: TODO-NNNN, status: open|done|superseded

Public API:

  scan_objectives(vault_root)   -> list[dict]
    Walk objective/*/  (skip _template* and flat files), parse frontmatter,
    return one dict per node.

  build_index(vault_root, dry_run=False) -> str
    Render objective/index.md (locked write). Returns rendered content.

  open_frontier(vault_root) -> dict
    Return {"research_questions": [...], "directions": [...]} sorted by priority.
    Only unsolved research_questions and open directions are included.
    Priority order: high > medium > low; tiebreak: Q- before DIR-, then by
    created ascending (older first).

  next_id(vault_root, node_type) -> str
    Read next_id counter from objective/index.md frontmatter, increment, write
    back (locked). Returns formatted id string e.g. "Q-0001", "DIR-0003".

  read_decisions(vault_root) -> list[dict]
    Load active objective/decision/*.md files, return list sorted by scope
    (all first, then wiki, research, web).

CLI verbs:
  python -m agents.objectives scan          - reconcile nodes to index.md
  python -m agents.objectives frontier      - print open-frontier payload as JSON
  python -m agents.objectives decisions     - list active decision nodes
  python -m agents.objectives status        - counts per node type
  python -m agents.objectives next-id TYPE  - emit + increment next id for type

Options: --vault <name>, --vault-root PATH, --json, --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Vault root resolution (mirrors wiki_index.py + wiki_init.py)
# ---------------------------------------------------------------------------

def _resolve_vault_root(override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    for ev in ("VAULT_ROOT", "VAULT_PATH"):
        val = os.environ.get(ev)
        if val:
            return Path(val)
    code_root = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
    py = code_root / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    try:
        result = subprocess.run(
            [str(py), "-m", "agents.vault_config", "path"],
            cwd=str(code_root),
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except Exception as exc:
        sys.exit(f"objectives: cannot resolve vault root: {exc}")


# ---------------------------------------------------------------------------
# Frontmatter parsing (mirrors wiki_index.py idiom)
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)


def _parse_fm(text: str) -> dict[str, str]:
    """Parse YAML-ish frontmatter into a flat str->str dict.

    Handles simple scalar values, quoted strings, and inline lists.
    Does NOT parse nested mappings (next_id block is handled separately).
    """
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        raw = v.strip()
        if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
            raw = raw[1:-1]
        fm[k.strip().lower()] = raw
    return fm


def _parse_next_id_block(text: str) -> dict[str, int]:
    """Parse the next_id: block from objective/index.md frontmatter.

    The block is:
      next_id:
        topic: 1
        research_question: 1
        ...
    Returns a dict of type -> int.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}
    yaml_body = m.group(1)
    # Find the next_id: block - everything under it until the next top-level key or end.
    block_m = re.search(r"^next_id:\s*\n((?:  .+\n?)*)", yaml_body, re.MULTILINE)
    if not block_m:
        return {}
    result: dict[str, int] = {}
    for line in block_m.group(1).splitlines():
        stripped = line.strip()
        if ":" not in stripped:
            continue
        k, _, v = stripped.partition(":")
        try:
            result[k.strip()] = int(v.strip())
        except ValueError:
            pass
    return result


def _body(text: str) -> str:
    """Return the body (everything after the closing ---)."""
    m = _FM_RE.match(text)
    if not m:
        return text
    return text[m.end():]


# ---------------------------------------------------------------------------
# Node type -> id prefix + directory mapping
# ---------------------------------------------------------------------------

_TYPE_TO_PREFIX = {
    "topic": "T",
    "research_question": "Q",
    "decision": "D",
    "research_question_proposal": "QP",
    "direction": "DIR",
    "agent_todo": "TODO",
}

_TYPE_TO_DIR = {
    "purpose": "purpose",
    "topic": "topic",
    "research_question": "research_question",
    "decision": "decision",
    "research_question_proposal": "research_question_proposal",
    "direction": "direction",
    "agent_todo": "agent_todo",
}

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

_SCOPE_RANK = {"all": 0, "wiki": 1, "research": 2, "web": 3}


def _format_id(node_type: str, n: int) -> str:
    prefix = _TYPE_TO_PREFIX.get(node_type)
    if not prefix:
        raise ValueError(f"unknown node type for id: {node_type!r}")
    return f"{prefix}-{n:04d}"


# ---------------------------------------------------------------------------
# Locking (mirrors wiki_index.py _locked_write pattern)
# ---------------------------------------------------------------------------

def _acquire_lock(vault_root: Path, rel: str) -> bool:
    code_root = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
    lock_script = code_root / "scripts" / "wiki-lock.sh"
    if not lock_script.exists():
        return True  # no lock available; proceed anyway
    r = subprocess.run(
        ["bash", str(lock_script), "acquire", rel],
        cwd=str(vault_root),
        capture_output=True,
    )
    return r.returncode == 0


def _release_lock(vault_root: Path, rel: str) -> None:
    code_root = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
    lock_script = code_root / "scripts" / "wiki-lock.sh"
    if not lock_script.exists():
        return
    subprocess.run(
        ["bash", str(lock_script), "release", rel],
        cwd=str(vault_root),
        capture_output=True,
    )


def _locked_write(vault_root: Path, rel: str, content: str, dry_run: bool = False) -> None:
    """Write content to vault_root/rel under the wiki-lock. Mirrors wiki_index.py pattern."""
    path = vault_root / rel
    if dry_run:
        print(content)
        return

    acquired = _acquire_lock(vault_root, rel)
    if not acquired:
        import time
        time.sleep(2)
        acquired = _acquire_lock(vault_root, rel)
    if not acquired:
        print(
            f"objectives: {rel} still held after retry -> skipping write",
            file=sys.stderr,
        )
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    finally:
        _release_lock(vault_root, rel)
    print(f"objectives: wrote {path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# scan_objectives
# ---------------------------------------------------------------------------

def scan_objectives(vault_root: Path) -> list[dict]:
    """Walk objective/*/  dirs, parse frontmatter, return one dict per node.

    Skips:
      - _template* files
      - hidden files (. prefix)
      - objective/index.md and objective/hot.md (flat management files)
      - Files directly inside objective/ (only subdirectory files are nodes)
    """
    obj_dir = vault_root / "objective"
    if not obj_dir.exists():
        return []

    nodes: list[dict] = []
    for subdir in sorted(obj_dir.iterdir()):
        if not subdir.is_dir():
            continue  # skip flat files (index.md, hot.md)
        type_name = subdir.name  # e.g. "topic", "direction"

        for md in sorted(subdir.rglob("*.md")):
            # skip templates and hidden files
            if md.name.startswith("_") or md.name.startswith("."):
                continue

            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            fm = _parse_fm(text)
            vault_rel = str(md.relative_to(vault_root)).replace("\\", "/")

            node: dict = {
                "type": fm.get("type", type_name),
                "id": fm.get("id", ""),
                "created": fm.get("created", ""),
                "updated": fm.get("updated", ""),
                "status": fm.get("status", ""),
                "priority": fm.get("priority", ""),
                "solved": fm.get("solved", ""),
                "scope": fm.get("scope", ""),
                "generated_by": fm.get("generated_by", ""),
                "serves_question": fm.get("serves_question", ""),
                "from_gap": fm.get("from_gap", ""),
                "answer_ref": fm.get("answer_ref", ""),
                "topic": fm.get("topic", ""),
                "path": vault_rel,
                "stem": md.stem,
                "body": _body(text),
            }
            nodes.append(node)

    return nodes


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------

def build_index(vault_root: Path, dry_run: bool = False) -> str:
    """Render objective/index.md (locked write). Returns rendered content."""
    nodes = scan_objectives(vault_root)
    today = datetime.date.today().isoformat()

    # Read existing next_id counters so we preserve them.
    index_path = vault_root / "objective" / "index.md"
    existing_next_id: dict[str, int] = {}
    if index_path.exists():
        try:
            existing_text = index_path.read_text(encoding="utf-8", errors="replace")
            existing_next_id = _parse_next_id_block(existing_text)
        except OSError:
            pass

    # Compute counts per type.
    type_counts: dict[str, int] = {}
    for n in nodes:
        t = n["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    # Build the index content.
    lines: list[str] = []
    lines.append("---")
    lines.append("type: index")
    lines.append(f"updated: {today}")
    lines.append("next_id:")
    for nt in ("topic", "research_question", "decision",
               "research_question_proposal", "direction", "agent_todo"):
        v = existing_next_id.get(nt, 1)
        lines.append(f"  {nt}: {v}")
    lines.append("ai-first: true")
    lines.append("---")
    lines.append("")
    lines.append("## For future Claude")
    lines.append(
        "This is the objective/ index file, maintained by agents/objectives.py. "
        "The frontmatter next_id counters are authoritative for new node ids. "
        "The body table logs agent operations on the objective graph."
    )
    lines.append("")
    lines.append("# Objective index")
    lines.append("")
    lines.append("## Node counts")
    lines.append("")
    lines.append("| Type | Count |")
    lines.append("|------|-------|")
    for t in ("purpose", "topic", "research_question", "decision",
              "research_question_proposal", "direction", "agent_todo"):
        c = type_counts.get(t, 0)
        lines.append(f"| {t} | {c} |")
    lines.append("")

    # research_question solved/unsolved split.
    rq_nodes = [n for n in nodes if n["type"] == "research_question"]
    solved_c = sum(1 for n in rq_nodes if n["solved"].lower() == "yes")
    open_c = sum(1 for n in rq_nodes if n["solved"].lower() != "yes")
    lines.append(f"**Research questions:** {open_c} open, {solved_c} solved")
    lines.append("")

    # All nodes table.
    lines.append("## All nodes")
    lines.append("")
    lines.append("| Id | Type | Status/Solved | Priority | Created | Path |")
    lines.append("|----|------|--------------|----------|---------|------|")
    for n in sorted(nodes, key=lambda x: (x["type"], x["id"])):
        status_solved = n["solved"] if n["type"] == "research_question" else n["status"]
        pri = n["priority"] or "-"
        lines.append(
            f"| {n['id']} | {n['type']} | {status_solved} | {pri} "
            f"| {n['created']} | {n['path']} |"
        )
    lines.append("")

    # Operation log placeholder (preserved from existing file if present).
    lines.append("## Operation log")
    lines.append("")
    # Preserve existing log rows if the file already exists.
    if index_path.exists():
        try:
            existing_body = _body(index_path.read_text(encoding="utf-8", errors="replace"))
            # Find the operation log section in the existing body.
            log_section = re.search(
                r"## Operation log\s*\n(.*?)(?=\n## |\Z)",
                existing_body,
                re.DOTALL,
            )
            if log_section:
                log_content = log_section.group(1).strip()
                if log_content:
                    lines.append(log_content)
                    lines.append("")
        except OSError:
            pass

    if not any("| Operation" in l for l in lines):
        lines.append("| Operation | Node | Agent | Date | Notes |")
        lines.append("|-----------|------|-------|------|-------|")
        lines.append("")

    content = "\n".join(lines)
    _locked_write(vault_root, "objective/index.md", content, dry_run=dry_run)
    return content


# ---------------------------------------------------------------------------
# open_frontier
# ---------------------------------------------------------------------------

def open_frontier(vault_root: Path) -> dict:
    """Return open frontier: unsolved research_questions + open directions, ranked.

    Priority ranking: high(0) > medium(1) > low(2).
    Tiebreak: Q- nodes before DIR- nodes, then created date ascending (older first).
    """
    nodes = scan_objectives(vault_root)

    rq_open = [
        n for n in nodes
        if n["type"] == "research_question" and n["solved"].lower() != "yes"
    ]
    dir_open = [
        n for n in nodes
        if n["type"] == "direction" and n["status"].lower() == "open"
    ]

    def _rank(node: dict) -> tuple:
        pri = _PRIORITY_RANK.get(node["priority"].lower() if node["priority"] else "", 1)
        # research_question sorts before direction at same priority
        type_order = 0 if node["type"] == "research_question" else 1
        created = node["created"] or "9999-99-99"
        return (pri, type_order, created)

    all_open = sorted(rq_open + dir_open, key=_rank)

    return {
        "research_questions": sorted(rq_open, key=_rank),
        "directions": sorted(dir_open, key=_rank),
        "combined_ranked": all_open,
    }


# ---------------------------------------------------------------------------
# read_decisions
# ---------------------------------------------------------------------------

def read_decisions(vault_root: Path) -> list[dict]:
    """Load active objective/decision/*.md nodes, sorted by scope (all first).

    Only nodes with status == active (or empty status) are returned.
    """
    nodes = scan_objectives(vault_root)
    decisions = [
        n for n in nodes
        if n["type"] == "decision"
        and n["status"].lower() in ("active", "")
    ]
    decisions.sort(key=lambda n: _SCOPE_RANK.get(n["scope"].lower() if n["scope"] else "", 99))
    return decisions


# ---------------------------------------------------------------------------
# next_id
# ---------------------------------------------------------------------------

def next_id(vault_root: Path, node_type: str) -> str:
    """Read next_id counter for node_type from index.md, increment, write back (locked).

    Returns the formatted id string, e.g. "Q-0001".
    Raises ValueError if node_type is not in the known set.
    """
    if node_type not in _TYPE_TO_PREFIX:
        raise ValueError(
            f"unknown node type {node_type!r}; valid: {list(_TYPE_TO_PREFIX.keys())}"
        )

    index_path = vault_root / "objective" / "index.md"
    rel = "objective/index.md"

    acquired = _acquire_lock(vault_root, rel)
    if not acquired:
        import time
        time.sleep(2)
        acquired = _acquire_lock(vault_root, rel)
    if not acquired:
        raise RuntimeError("objectives: could not acquire lock on objective/index.md for next_id")

    try:
        if not index_path.exists():
            raise FileNotFoundError(
                f"objective/index.md not found at {index_path}; "
                "run scripts/obj_init.py --apply first"
            )

        text = index_path.read_text(encoding="utf-8", errors="replace")
        counters = _parse_next_id_block(text)
        n = counters.get(node_type, 1)
        new_n = n + 1

        # Rewrite just the counter line in the frontmatter block.
        # We use a targeted replace to avoid re-rendering the whole file.
        def _replace_counter(m: re.Match) -> str:
            block = m.group(0)
            updated = re.sub(
                rf"^(\s*{re.escape(node_type)}:\s*)\d+",
                lambda mc: mc.group(1) + str(new_n),
                block,
                flags=re.MULTILINE,
            )
            return updated

        new_text = re.sub(
            r"^next_id:\s*\n(?:  .+\n?)*",
            _replace_counter,
            text,
            flags=re.MULTILINE,
        )
        index_path.write_text(new_text, encoding="utf-8")

    finally:
        _release_lock(vault_root, rel)

    return _format_id(node_type, n)


# ---------------------------------------------------------------------------
# relink  -- add YAML aliases + written_by + ## Links body section
# + hot.md prose linkify
# ---------------------------------------------------------------------------

# Regex matching bare objective-node ids (4-digit suffix).
# Covers QP, DIR, Q, T, D, TODO.  Does NOT match TBD-N (different prefix).
_HOT_ID_RE = re.compile(r"\b(QP|DIR|Q|T|D|TODO)-\d{4}\b")


def _linkify_prose(text: str, id_relpath_map: dict[str, str]) -> str:
    """Wrap bare objective-node ids in `text` with full path-qualified wikilinks.

    Rules:
    - Only matches ids of the form (QP|DIR|Q|T|D|TODO)-DDDD (4 digits).
    - Ids already inside an existing [[...]] wikilink are left untouched.
    - Ids not present in id_relpath_map are left bare.
    - Idempotent: already-wrapped ids are skipped.

    Strategy: split the text into wikilink spans and non-wikilink spans;
    only run the replacement regex on the non-wikilink spans.
    """
    # Tokenise into alternating segments: wikilink tokens and plain text.
    # [[...]] may contain pipes and path separators but not newlines (safe assumption).
    wikilink_re = re.compile(r"\[\[[^\]]*\]\]")
    result_parts: list[str] = []
    last_end = 0
    for m in wikilink_re.finditer(text):
        # Process plain text segment before this wikilink.
        plain = text[last_end:m.start()]
        result_parts.append(_replace_bare_ids(plain, id_relpath_map))
        # Keep the wikilink token verbatim.
        result_parts.append(m.group(0))
        last_end = m.end()
    # Trailing plain text after the last wikilink.
    result_parts.append(_replace_bare_ids(text[last_end:], id_relpath_map))
    return "".join(result_parts)


def _replace_bare_ids(segment: str, id_relpath_map: dict[str, str]) -> str:
    """Replace bare ids in a plain-text segment (no wikilinks present)."""
    def _sub(m: re.Match) -> str:
        node_id = m.group(0)
        relpath = id_relpath_map.get(node_id)
        if relpath:
            return f"[[{relpath}]]"
        # Not in map -- leave bare.
        return node_id

    return _HOT_ID_RE.sub(_sub, segment)


def _relink_hot_md(
    vault_root: Path,
    id_relpath_map: dict[str, str],
    apply: bool = False,
) -> dict | None:
    """Process objective/hot.md:

    1. Prose linkify: in the body, wrap bare objective-node ids with full
       path-qualified wikilinks from id_relpath_map.
    2. Frontmatter: rename generated_by -> written_by (if generated_by present
       and written_by absent).

    Returns a change dict (same schema as relink node records) or None if the
    file does not exist.  If apply=False, the file is not written.
    """
    hot_path = vault_root / "objective" / "hot.md"
    if not hot_path.exists():
        return None

    try:
        original_text = hot_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    fm_match = _FM_RE.match(original_text)
    if not fm_match:
        # No frontmatter -- just linkify the whole text.
        new_text = _linkify_prose(original_text, id_relpath_map)
        changed = new_text != original_text
        record = {
            "path": "objective/hot.md",
            "aliases_cleaned": [],
            "written_by": "",
            "generated_by_removed": False,
            "links_preview": None,
            "changed": changed,
        }
        if apply and changed:
            hot_path.write_text(new_text, encoding="utf-8")
        return record

    fm_block = fm_match.group(1)
    body = original_text[fm_match.end():]
    fm = _parse_fm(original_text)

    # --- 1. Frontmatter: generated_by -> written_by rename ---
    current_written_by = fm.get("written_by", "").strip()
    current_generated_by = fm.get("generated_by", "").strip()

    fm_updates: dict[str, object] = {}
    if current_generated_by and not current_written_by:
        # Rename: add written_by, remove generated_by.
        fm_updates["written_by"] = current_generated_by
        fm_updates["generated_by"] = None  # remove

    new_fm_block = _serialize_fm(fm_block, fm_updates) if fm_updates else fm_block

    # --- 2. Prose linkify body ---
    new_body = _linkify_prose(body, id_relpath_map)

    new_text = f"---\n{new_fm_block}\n---\n{new_body}"
    changed = new_text != original_text

    record = {
        "path": "objective/hot.md",
        "aliases_cleaned": [],
        "written_by": fm_updates.get("written_by", current_written_by) or current_written_by,
        "generated_by_removed": "generated_by" in fm_updates,
        "links_preview": None,
        "changed": changed,
    }
    if apply and changed:
        hot_path.write_text(new_text, encoding="utf-8")
    return record

# Types where written_by = USER (user-authored nodes).
_USER_AUTHORED_TYPES = {"purpose", "topic", "research_question", "decision"}

# Agent-authored types get written_by from generated_by (fallback: "research").
_AGENT_AUTHORED_TYPES = {"direction", "research_question_proposal", "agent_todo"}

# Sentinel comments that delimit the auto-generated links block.
_LINKS_START = "<!-- links:auto -->"
_LINKS_END = "<!-- /links:auto -->"


def _parse_inline_list(raw: str) -> list[str]:
    """Parse a YAML inline list '[A, B, C]' or a comma-separated 'A, B, C' into items.

    Returns a list of stripped, non-empty strings.
    """
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [s.strip() for s in raw.split(",") if s.strip()]


def _extract_ids_from_text(text: str, pattern: str) -> list[str]:
    """Return all distinct ids matching `pattern` found in `text`, in order of appearance."""
    seen: set[str] = set()
    result: list[str] = []
    for m in re.finditer(pattern, text):
        val = m.group(0)
        if val not in seen:
            seen.add(val)
            result.append(val)
    return result


def _serialize_fm(fm_raw: str, updates: dict[str, object]) -> str:
    """Apply `updates` to a frontmatter YAML block string (the text between --- delimiters).

    Rules:
    - If a key exists in `updates` with value None, remove that line entirely.
    - If a key exists, replace the line's value.
    - If a key is new, append before the closing line.
    - Values that are lists are serialized as '[item1, item2]'.
    - Values that are strings are emitted as-is.
    Returns the updated frontmatter block (without the surrounding --- markers).
    """
    lines = fm_raw.splitlines()
    used_keys: set[str] = set()
    result: list[str] = []

    for line in lines:
        if ":" not in line or line.lstrip().startswith("#"):
            result.append(line)
            continue
        k, _, _ = line.partition(":")
        key = k.strip().lower()
        if key in updates:
            used_keys.add(key)
            val = updates[key]
            if val is None:
                # Remove this line
                continue
            elif isinstance(val, list):
                result.append(f"{k.strip()}: [{', '.join(str(v) for v in val)}]")
            else:
                result.append(f"{k.strip()}: {val}")
        else:
            result.append(line)

    # Append any new keys not yet seen.
    for key, val in updates.items():
        if key not in used_keys and val is not None:
            if isinstance(val, list):
                result.append(f"{key}: [{', '.join(str(v) for v in val)}]")
            else:
                result.append(f"{key}: {val}")

    return "\n".join(result)


def _wikilink(node_id: str, id_relpath_map: dict[str, str]) -> str:
    """Return a full path-qualified wikilink for node_id.

    Uses id_relpath_map to resolve the path. Falls back to bare [[<id>]] if
    the id has no matching file (dangling reference).
    """
    relpath = id_relpath_map.get(node_id)
    if relpath:
        return f"[[{relpath}]]"
    return f"[[{node_id}]]"


def _build_links_section(
    node_type: str,
    fm: dict[str, str],
    body: str,
    id_relpath_map: dict[str, str],
) -> str | None:
    """Return the content for the ## Links section (without the heading line itself),
    or None if this node type should have no Links section.

    All wikilinks are FULL path-qualified using id_relpath_map, e.g.:
      <!-- links:auto -->
      - part of: [[objective/purpose/PURPOSE]]
      <!-- /links:auto -->

    Falls back to bare [[<id>]] for dangling references (id not in map).
    """
    if node_type == "purpose":
        return None

    link_lines: list[str] = []

    if node_type == "topic":
        link_lines.append(f"- part of: {_wikilink('purpose', id_relpath_map)}")
        rq_raw = fm.get("related_questions", "")
        for qid in _parse_inline_list(rq_raw):
            if re.match(r"^Q-\d{4}$", qid):
                link_lines.append(f"- question: {_wikilink(qid, id_relpath_map)}")

    elif node_type == "research_question":
        # Primary topic from frontmatter
        topic_fm = fm.get("topic", "").strip()
        if re.match(r"^T-\d{4}$", topic_fm):
            link_lines.append(f"- topic: {_wikilink(topic_fm, id_relpath_map)}")
        # Secondary topics from body preamble: "Secondary topic: T-NNNN"
        seen_topics: set[str] = {topic_fm}
        for m in re.finditer(r"Secondary topic:\s*(T-\d{4})", body):
            tid = m.group(1)
            if tid not in seen_topics:
                seen_topics.add(tid)
                link_lines.append(f"- topic: {_wikilink(tid, id_relpath_map)}")

    elif node_type == "direction":
        sq = fm.get("serves_question", "").strip()
        if re.match(r"^Q-\d{4}$", sq):
            link_lines.append(f"- serves: {_wikilink(sq, id_relpath_map)}")
        topics_raw = fm.get("topics", "")
        for tid in _parse_inline_list(topics_raw):
            if re.match(r"^T-\d{4}$", tid):
                link_lines.append(f"- topic: {_wikilink(tid, id_relpath_map)}")

    elif node_type == "research_question_proposal":
        from_gap = fm.get("from_gap", "")
        combined_text = from_gap + "\n" + body
        seen: set[str] = set()
        for qid in _extract_ids_from_text(combined_text, r"Q-\d{4}"):
            if qid not in seen:
                seen.add(qid)
                link_lines.append(f"- relates to: {_wikilink(qid, id_relpath_map)}")
        for tid in _extract_ids_from_text(combined_text, r"T-\d{4}"):
            if tid not in seen:
                seen.add(tid)
                link_lines.append(f"- topic: {_wikilink(tid, id_relpath_map)}")

    elif node_type == "decision":
        scope = fm.get("scope", "").strip()
        # If scope is a node id, link it; else link PURPOSE for known generic scopes.
        if re.match(r"^(T|Q|DIR)-\d{4}$", scope):
            link_lines.append(f"- governs: {_wikilink(scope, id_relpath_map)}")
        else:
            # all / research / wiki / web / empty -> governs PURPOSE
            link_lines.append(f"- governs: {_wikilink('purpose', id_relpath_map)}")

    elif node_type == "agent_todo":
        # No specific links spec; emit empty block so section exists but is neutral.
        pass

    if not link_lines and node_type not in ("agent_todo",):
        # Nothing to link -- still emit the markers so rerun is stable
        pass

    content = "\n".join([_LINKS_START] + link_lines + [_LINKS_END])
    return content


def _inject_links_section(body: str, links_block: str) -> str:
    """Insert or replace the auto-links block in the body.

    Placement: immediately after the `## For future Claude` block (its last non-blank
    line), or at the top of the body if absent.

    Re-running must replace, not duplicate: if the markers already exist, replace
    the content between them (inclusive).
    """
    # If markers already exist, replace between them.
    existing_re = re.compile(
        r"<!-- links:auto -->.*?<!-- /links:auto -->",
        re.DOTALL,
    )
    if existing_re.search(body):
        return existing_re.sub(links_block, body, count=1)

    # Find the end of the "## For future Claude" section.
    # The section spans from the heading to the next heading (any level) or end of string.
    ffc_re = re.compile(r"(## For future Claude\b.*?)(\n#{1,6} |\Z)", re.DOTALL)
    m = ffc_re.search(body)
    if m:
        insert_pos = m.start(2)
        return body[:insert_pos] + "\n" + links_block + "\n" + body[insert_pos:]

    # No "## For future Claude" -- insert at the very beginning of the body.
    return links_block + "\n" + body


def _build_id_relpath_map(vault_root: Path) -> dict[str, str]:
    """Build a map of node id -> vault-root-relative path WITHOUT .md extension.

    Scans all objective node files (same exclusions as relink) and maps each
    node's canonical id to its relpath from vault_root, forward-slashes, no .md.

    Example: "Q-0001" -> "objective/research_question/Q-0001-afd-dead-zone-optical-bw-target"
             "purpose" -> "objective/purpose/PURPOSE"
    """
    obj_dir = vault_root / "objective"
    if not obj_dir.exists():
        return {}

    id_map: dict[str, str] = {}

    for subdir in sorted(obj_dir.iterdir()):
        if not subdir.is_dir():
            continue
        node_type = subdir.name

        for md_path in sorted(subdir.rglob("*.md")):
            if md_path.name.startswith("_"):
                continue
            if md_path.stem in ("index", "hot"):
                continue

            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            fm = _parse_fm(text)
            node_id = fm.get("id", "").strip()
            if node_type == "purpose":
                canonical_id = "purpose"
            else:
                canonical_id = node_id

            if not canonical_id:
                continue

            # relpath from vault_root, forward slashes, strip .md extension
            relpath = str(md_path.relative_to(vault_root)).replace("\\", "/")
            if relpath.endswith(".md"):
                relpath = relpath[:-3]

            id_map[canonical_id] = relpath

    return id_map


def relink(
    vault_root: Path,
    apply: bool = False,
) -> list[dict]:
    """Add graph EDGES + provenance to every objective node.

    For each node file under objective/<type>/*.md (skipping _template*, index*, hot*):
      1. Clean up aliases: remove own-id alias if present; drop the key when empty.
         Preserve any other pre-existing aliases.
      2. Set written_by (and handle generated_by migration).
      3. (Re)generate the ## Links body section using FULL path-qualified wikilinks.

    Returns a list of per-file change dicts:
      {"path": str, "aliases_cleaned": list, "written_by": str,
       "generated_by_removed": bool, "links_preview": str | None,
       "changed": bool}

    If apply=False (dry-run), files are NOT written.
    If apply=True, files are written only when content actually changed.
    """
    obj_dir = vault_root / "objective"
    if not obj_dir.exists():
        return []

    # Build the id->relpath map once for all nodes so _build_links_section can
    # emit full path-qualified wikilinks.
    id_relpath_map = _build_id_relpath_map(vault_root)

    results: list[dict] = []

    for subdir in sorted(obj_dir.iterdir()):
        if not subdir.is_dir():
            continue
        node_type = subdir.name

        for md_path in sorted(subdir.rglob("*.md")):
            # Skip templates and management files.
            if md_path.name.startswith("_"):
                continue
            if md_path.stem in ("index", "hot"):
                continue

            try:
                original_text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            fm_match = _FM_RE.match(original_text)
            if not fm_match:
                continue

            fm_block = fm_match.group(1)  # raw YAML between the --- markers
            body = original_text[fm_match.end():]
            fm = _parse_fm(original_text)

            node_id = fm.get("id", "").strip()
            # Determine the canonical id for this type.
            if node_type == "purpose":
                canonical_id = "purpose"
            else:
                canonical_id = node_id

            if not canonical_id:
                continue  # No id, skip.

            # -------------------------------------------------------------------
            # 1. aliases -- remove own-id alias; preserve other aliases
            # -------------------------------------------------------------------
            aliases_raw = fm.get("aliases", "")
            existing_aliases = _parse_inline_list(aliases_raw) if aliases_raw else []
            # Remove exactly the own canonical_id from the aliases list.
            cleaned_aliases = [a for a in existing_aliases if a != canonical_id]
            aliases_cleaned: list[str] = (
                [canonical_id] if len(cleaned_aliases) < len(existing_aliases) else []
            )

            # Determine what to write for the aliases key:
            #   - no original aliases at all -> no update needed (don't add the key)
            #   - had aliases but after cleaning the list is empty -> remove the key (None)
            #   - had aliases and some remain after cleaning -> write the cleaned list
            if not aliases_raw:
                # Key was absent; do not add it.
                aliases_update: object = "SKIP"
            elif not cleaned_aliases:
                # All aliases were just the own id; remove the key entirely.
                aliases_update = None
            else:
                # Some other aliases remain.
                aliases_update = cleaned_aliases

            # -------------------------------------------------------------------
            # 2. written_by + generated_by migration
            # -------------------------------------------------------------------
            current_written_by = fm.get("written_by", "").strip()
            current_generated_by = fm.get("generated_by", "").strip()

            if node_type in _USER_AUTHORED_TYPES:
                new_written_by = "USER"
            else:
                # Agent-authored: use existing generated_by or default to "research"
                new_written_by = current_generated_by if current_generated_by else "research"

            # generated_by migration: write-side scripts no longer emit generated_by;
            # but EXISTING nodes still carry it. We migrate to written_by and then REMOVE
            # generated_by (no code reads it anymore; safe to drop).
            # Order: ensure written_by is set first (if written_by absent but generated_by
            # present, set written_by from generated_by per type), THEN drop generated_by.
            remove_generated_by = current_generated_by != ""  # remove if present

            # -------------------------------------------------------------------
            # 3. ## Links body section
            # -------------------------------------------------------------------
            links_block = _build_links_section(node_type, fm, body, id_relpath_map)
            links_preview = links_block

            # -------------------------------------------------------------------
            # Build the new file content
            # -------------------------------------------------------------------
            fm_updates: dict[str, object] = {}

            # aliases: only update if the key was present and needs changing
            if aliases_update != "SKIP":
                fm_updates["aliases"] = aliases_update  # None = remove, list = rewrite

            # written_by: set if not already matching
            if current_written_by != new_written_by:
                fm_updates["written_by"] = new_written_by

            # generated_by: remove if present (after written_by migration is complete)
            if remove_generated_by:
                fm_updates["generated_by"] = None  # None = remove

            new_fm_block = _serialize_fm(fm_block, fm_updates) if fm_updates else fm_block

            # Links body section
            new_body = body
            if links_block is not None:
                new_body = _inject_links_section(body, links_block)

            new_text = f"---\n{new_fm_block}\n---\n{new_body}"
            changed = new_text != original_text

            record: dict = {
                "path": str(md_path.relative_to(vault_root)).replace("\\", "/"),
                "aliases_cleaned": aliases_cleaned,
                "written_by": new_written_by,
                "generated_by_removed": remove_generated_by,
                "links_preview": links_preview,
                "changed": changed,
            }
            results.append(record)

            if apply and changed:
                md_path.write_text(new_text, encoding="utf-8")

    # Handle objective/hot.md separately (prose linkify + frontmatter rename).
    hot_record = _relink_hot_md(vault_root, id_relpath_map, apply=apply)
    if hot_record is not None:
        results.append(hot_record)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _verb_scan(vault_root: Path, args: argparse.Namespace) -> int:
    build_index(vault_root, dry_run=args.dry_run)
    return 0


def _verb_frontier(vault_root: Path, args: argparse.Namespace) -> int:
    result = open_frontier(vault_root)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        rqs = result["research_questions"]
        dirs = result["directions"]
        print(f"Open research questions ({len(rqs)}):")
        for n in rqs:
            solved = n["solved"]
            pri = n["priority"] or "-"
            print(f"  [{pri}] {n['id']} (solved:{solved}) {n['path']}")
        print(f"\nOpen directions ({len(dirs)}):")
        for n in dirs:
            pri = n["priority"] or "-"
            print(f"  [{pri}] {n['id']} {n['path']}")
    return 0


def _verb_decisions(vault_root: Path, args: argparse.Namespace) -> int:
    decisions = read_decisions(vault_root)
    if args.json:
        out = [
            {
                "id": d["id"],
                "scope": d["scope"],
                "status": d["status"],
                "body": d["body"].strip()[:300],
                "path": d["path"],
            }
            for d in decisions
        ]
        print(json.dumps(out, indent=2))
    else:
        print(f"Active decisions ({len(decisions)}):")
        for d in decisions:
            print(f"  {d['id']} scope={d['scope']} {d['path']}")
            snippet = d["body"].strip()[:120].replace("\n", " ")
            print(f"    {snippet}")
    return 0


def _verb_status(vault_root: Path, args: argparse.Namespace) -> int:
    nodes = scan_objectives(vault_root)
    counts: dict[str, int] = {}
    for n in nodes:
        t = n["type"]
        counts[t] = counts.get(t, 0) + 1

    rq_nodes = [n for n in nodes if n["type"] == "research_question"]
    solved = sum(1 for n in rq_nodes if n["solved"].lower() == "yes")
    unsolved = len(rq_nodes) - solved

    if args.json:
        out: dict = {"counts": counts, "research_question_solved": solved,
                     "research_question_open": unsolved}
        print(json.dumps(out, indent=2))
    else:
        print("Objective node counts:")
        for t in ("purpose", "topic", "research_question", "decision",
                  "research_question_proposal", "direction", "agent_todo"):
            print(f"  {t:35s}: {counts.get(t, 0)}")
        print(f"  research_question open/solved          : {unsolved}/{solved}")
    return 0


def _verb_next_id(vault_root: Path, args: argparse.Namespace) -> int:
    if not hasattr(args, "node_type") or not args.node_type:
        print("usage: objectives next-id <node_type>", file=sys.stderr)
        return 2
    try:
        id_str = next_id(vault_root, args.node_type)
        print(id_str)
        return 0
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"objectives next-id: {exc}", file=sys.stderr)
        return 1


def _verb_relink(vault_root: Path, args: argparse.Namespace) -> int:
    """Dry-run or apply the relink verb."""
    apply = getattr(args, "apply", False)
    results = relink(vault_root, apply=apply)

    changed_count = sum(1 for r in results if r["changed"])
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"relink [{mode}]: {len(results)} nodes scanned, {changed_count} would change")
    print()

    for r in results:
        if not r["changed"] and not apply:
            # In dry-run, only print nodes that would change (verbose would be noisy).
            pass
        status = "CHANGED" if r["changed"] else "ok"
        aliases_str = (
            f"  aliases_cleaned: {r['aliases_cleaned']}" if r["aliases_cleaned"] else ""
        )
        print(f"  [{status}] {r['path']}")
        print(f"    written_by: {r['written_by']}{aliases_str}")
        if r["links_preview"]:
            preview_lines = r["links_preview"].splitlines()
            preview = "\n      ".join(preview_lines[:6])
            if len(preview_lines) > 6:
                preview += f"\n      ... ({len(preview_lines) - 6} more lines)"
            print(f"    links:\n      {preview}")

    if apply:
        written = sum(1 for r in results if r["changed"])
        print(f"\nrelink: wrote {written} files")
    else:
        print("\n(dry-run: pass --apply to write changes)")

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Markdown-native objective store for Second Brain v0.2."
    )
    ap.add_argument("--vault", default=None, help="Vault name (resolved via vault_config)")
    ap.add_argument("--vault-root", default=None, help="Override vault root path directly")
    ap.add_argument("--json", action="store_true", help="Emit JSON output")
    ap.add_argument("--dry-run", action="store_true", help="Print without writing")

    sub = ap.add_subparsers(dest="verb", required=True)

    sub.add_parser("scan", help="Reconcile objective/ nodes to objective/index.md")
    sub.add_parser("frontier", help="Emit open-frontier payload")
    sub.add_parser("decisions", help="List active decision nodes")
    sub.add_parser("status", help="Count nodes per type")
    ni = sub.add_parser("next-id", help="Emit + increment next id for a node type")
    ni.add_argument("node_type", help="Node type: topic|research_question|decision|etc.")
    rl = sub.add_parser(
        "relink",
        help="Add written_by + ## Links sections to objective nodes; clean up own-id aliases",
    )
    rl.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes (default: dry-run)",
    )

    args = ap.parse_args(argv)

    # Resolve vault root.
    vault_root_str: str | None = args.vault_root
    if not vault_root_str and args.vault:
        # Use vault_config with vault name.
        code_root = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
        py = code_root / ".venv" / "bin" / "python"
        if not py.exists():
            py = Path(sys.executable)
        try:
            r = subprocess.run(
                [str(py), "-m", "agents.vault_config", "path", "--vault", args.vault],
                cwd=str(code_root),
                capture_output=True,
                text=True,
                check=True,
            )
            vault_root_str = r.stdout.strip()
        except Exception as exc:
            sys.exit(f"objectives: cannot resolve vault for {args.vault!r}: {exc}")

    vault_root = _resolve_vault_root(vault_root_str)

    dispatch = {
        "scan": _verb_scan,
        "frontier": _verb_frontier,
        "decisions": _verb_decisions,
        "status": _verb_status,
        "next-id": _verb_next_id,
        "relink": _verb_relink,
    }
    fn = dispatch.get(args.verb)
    if fn is None:
        print(f"unknown verb: {args.verb}", file=sys.stderr)
        return 2
    return fn(vault_root, args)


if __name__ == "__main__":
    sys.exit(main())
