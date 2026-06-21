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
    }
    fn = dispatch.get(args.verb)
    if fn is None:
        print(f"unknown verb: {args.verb}", file=sys.stderr)
        return 2
    return fn(vault_root, args)


if __name__ == "__main__":
    sys.exit(main())
