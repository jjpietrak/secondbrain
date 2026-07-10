#!/usr/bin/env python3
"""obj_synth_helper.py -- CLI helper for the obj-synth skill.

This module is a thin CLI wrapper around scripts.research_synthesis that:
  1. Loads the objective frontier JSON (from a file or via agents.objectives).
  2. Calls gather_local_context() with the combined question/topic text.
  3. Formats the {wiki_baseline} block.
  4. Formats {open_questions} and {active_concepts} strings for the pipeline prompts.
  5. Formats {existing_directions} string from open direction nodes.
  6. Optionally fills and prints the RESEARCH_ANALYSIS_PROMPT or
     RESEARCH_SYNTHESIS_PROMPT (useful for debugging / manual runs).

It does NOT call the LLM - it only prepares the prompt strings.
The research agent (running the obj-synth skill) fills and evaluates the prompts.

Usage
-----
  # Gather wiki context for a given topic/question text + frontier JSON
  python scripts/obj_synth_helper.py gather-context \
      --text "inference disaggregation latency memory bandwidth" \
      --vault-root /path/to/vault \
      [--frontier-json /tmp/frontier.json]

  # Format open_questions string from frontier JSON
  python scripts/obj_synth_helper.py format-questions \
      --frontier-json /tmp/frontier.json

  # Format existing_directions string from frontier JSON
  python scripts/obj_synth_helper.py format-directions \
      --frontier-json /tmp/frontier.json

  # Fill RESEARCH_ANALYSIS_PROMPT and print (all required args)
  python scripts/obj_synth_helper.py fill-analysis \
      --purpose "..." --open-questions "..." --active-concepts "..." \
      --wiki-baseline "..." [--wiki-gaps "..."]

  # Fill RESEARCH_SYNTHESIS_PROMPT and print
  python scripts/obj_synth_helper.py fill-synthesis \
      --purpose "..." --open-questions "..." --active-concepts "..." \
      --gap-analysis "..." [--existing-directions "..."]

All outputs are to stdout (UTF-8). Vault-root defaults to env VAULT_ROOT/VAULT_PATH
or agents.vault_config fallback. Pass --vault-root to override.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# Ensure repo root on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.research_synthesis import (  # noqa: E402
    gather_local_context,
    excerpts_to_wiki_baseline,
    fill_analysis_prompt,
    fill_synthesis_prompt,
    parse_directions,
    parse_proposals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.date.today().isoformat()


def _load_frontier(frontier_json_path: str | None, vault_root: str | None) -> dict:
    """Load frontier from a JSON file or by calling agents.objectives frontier."""
    if frontier_json_path:
        with open(frontier_json_path, encoding="utf-8") as fh:
            return json.load(fh)
    # Fall back to calling agents.objectives
    import subprocess
    py = str(_REPO_ROOT / ".venv" / "bin" / "python")
    cmd = [py, "-m", "agents.objectives", "frontier", "--json"]
    if vault_root:
        cmd += ["--vault-root", vault_root]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if result.returncode != 0:
        raise RuntimeError(
            f"agents.objectives frontier failed: {result.stderr.strip()}"
        )
    return json.load(result.stdout) if isinstance(result.stdout, str) else json.loads(result.stdout)


def _format_open_questions(frontier: dict) -> str:
    """Format research_questions list as the {open_questions} block for prompts."""
    rqs = frontier.get("research_questions", [])
    if not rqs:
        return "(no open research questions)"
    lines = []
    for q in rqs:
        qid = q.get("id", "")
        body = q.get("body", "").strip()
        # Take first non-empty line of body as the question text
        first_line = next((l for l in body.splitlines() if l.strip()), "")
        priority = q.get("priority", "-")
        # Subject axis: concept slugs from the node's related: links (topic retired).
        concepts = q.get("concepts") or []
        concepts_str = f" [concepts: {', '.join(concepts)}]" if concepts else ""
        lines.append(f"{qid}: {first_line} [priority: {priority}]{concepts_str}")
    return "\n".join(lines)


def _format_existing_directions(frontier: dict) -> str:
    """Format open directions list as the {existing_directions} block for prompts."""
    dirs = frontier.get("directions", [])
    if not dirs:
        return "(none)"
    lines = []
    for d in dirs:
        did = d.get("id", "")
        body = d.get("body", "").strip()
        first_line = next((l for l in body.splitlines() if l.strip()), "")
        serves = d.get("serves_question", "")
        priority = d.get("priority", "-")
        serves_str = f" [serves: {serves}]" if serves else ""
        lines.append(f"{did}: {first_line} [priority: {priority}]{serves_str}")
    return "\n".join(lines)


def _format_active_concepts_from_vault(vault_root: str | None) -> str:
    """Read wiki/concepts/*.md and format the {active_concepts} block.

    The subject axis is now the set of concept pages under wiki/concepts/ (the
    retired objective/topic/ nodes are gone). Each active concept is listed as
    ``<slug>: <title>`` where <slug> is the concept page stem (the value used in
    ``[[wiki/concepts/<slug>]]`` links).
    """
    if not vault_root:
        return "(active concepts unavailable - no vault root)"
    vr = Path(vault_root)
    concept_dir = vr / "wiki" / "concepts"
    if not concept_dir.exists():
        return "(no concepts dir found)"
    import re
    lines = []
    for md in sorted(concept_dir.glob("*.md")):
        if md.name.startswith("_") or md.name in {"index.md"}:
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        slug = md.stem
        title = md.stem
        # Title: first heading in body, else the slug
        heading_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if heading_m:
            title = heading_m.group(1).strip()
        lines.append(f"{slug}: {title}")
    return "\n".join(lines) if lines else "(no active concepts)"


def _read_purpose(vault_root: str | None) -> str:
    """Read objective/purpose/PURPOSE.md body."""
    if not vault_root:
        return "(purpose unavailable - no vault root)"
    vr = Path(vault_root)
    purpose_path = vr / "objective" / "purpose" / "PURPOSE.md"
    if not purpose_path.exists():
        return "(PURPOSE.md not found)"
    text = purpose_path.read_text(encoding="utf-8", errors="replace")
    import re
    # Strip frontmatter
    fm_m = re.match(r"^---\n.*?\n---(?:\n|$)", text, re.DOTALL)
    if fm_m:
        return text[fm_m.end():].strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Verb implementations
# ---------------------------------------------------------------------------

def _cmd_gather_context(args: argparse.Namespace) -> int:
    """Gather wiki context for the given text, print wiki_baseline block."""
    frontier: dict = {}
    if args.frontier_json or not args.text:
        try:
            frontier = _load_frontier(args.frontier_json, args.vault_root)
        except Exception as exc:
            print(f"Warning: could not load frontier: {exc}", file=sys.stderr)

    # Build objective_nodes list from frontier for objective context scoring
    obj_nodes = (
        frontier.get("research_questions", [])
        + frontier.get("directions", [])
    )

    text = args.text or ""
    if not text and frontier:
        # Derive from question texts
        parts = []
        for q in frontier.get("research_questions", []):
            body = q.get("body", "").strip()
            first = next((l for l in body.splitlines() if l.strip()), "")
            parts.append(first)
        text = " ".join(parts)

    excerpts = gather_local_context(
        topic_or_question=text,
        wiki_root=args.vault_root,
        objective_nodes=obj_nodes,
    )
    baseline = excerpts_to_wiki_baseline(excerpts)
    print(baseline)
    return 0


def _cmd_format_questions(args: argparse.Namespace) -> int:
    """Format open_questions string from frontier JSON."""
    frontier = _load_frontier(args.frontier_json, args.vault_root)
    print(_format_open_questions(frontier))
    return 0


def _cmd_format_directions(args: argparse.Namespace) -> int:
    """Format existing_directions string from frontier JSON."""
    frontier = _load_frontier(args.frontier_json, args.vault_root)
    print(_format_existing_directions(frontier))
    return 0


def _cmd_fill_analysis(args: argparse.Namespace) -> int:
    """Fill RESEARCH_ANALYSIS_PROMPT and print."""
    purpose = args.purpose or _read_purpose(args.vault_root)
    today = _today()
    open_questions = args.open_questions or "(none)"
    active_concepts = args.active_concepts or _format_active_concepts_from_vault(args.vault_root)
    wiki_baseline = args.wiki_baseline or "(no wiki context provided)"
    wiki_gaps = args.wiki_gaps or "(none)"
    prompt = fill_analysis_prompt(
        purpose=purpose,
        today=today,
        open_questions=open_questions,
        active_concepts=active_concepts,
        wiki_baseline=wiki_baseline,
        wiki_gaps=wiki_gaps,
    )
    print(prompt)
    return 0


def _cmd_fill_synthesis(args: argparse.Namespace) -> int:
    """Fill RESEARCH_SYNTHESIS_PROMPT and print."""
    purpose = args.purpose or _read_purpose(args.vault_root)
    today = _today()
    open_questions = args.open_questions or "(none)"
    active_concepts = args.active_concepts or _format_active_concepts_from_vault(args.vault_root)
    gap_analysis = args.gap_analysis or "(no gap analysis provided)"
    existing_directions = args.existing_directions or "(none)"
    prompt = fill_synthesis_prompt(
        purpose=purpose,
        today=today,
        open_questions=open_questions,
        active_concepts=active_concepts,
        gap_analysis=gap_analysis,
        existing_directions=existing_directions,
    )
    print(prompt)
    return 0


def _cmd_parse_directions(args: argparse.Namespace) -> int:
    """Parse directions from a synthesis output file and print as JSON."""
    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    dirs = parse_directions(text)
    print(json.dumps(dirs, indent=2))
    return 0


def _cmd_parse_proposals(args: argparse.Namespace) -> int:
    """Parse proposals from a synthesis output file and print as JSON."""
    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    props = parse_proposals(text)
    print(json.dumps(props, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="CLI helper for the obj-synth skill (prompt prep + context gather)."
    )
    ap.add_argument("--vault-root", default=None,
                    help="Override vault root path (else VAULT_ROOT env or vault_config)")

    sub = ap.add_subparsers(dest="cmd", required=True)

    gc = sub.add_parser("gather-context",
                        help="Gather wiki context and print wiki_baseline block")
    gc.add_argument("--text", default=None,
                    help="Query text (topic/question combined). Derived from frontier if omitted.")
    gc.add_argument("--frontier-json", default=None,
                    help="Path to frontier JSON from 'objectives frontier --json'")
    gc.add_argument("--vault-root", default=None,
                    help="Override vault root path (overrides top-level --vault-root)")

    fq = sub.add_parser("format-questions",
                        help="Format open_questions string from frontier JSON")
    fq.add_argument("--frontier-json", default=None,
                    help="Path to frontier JSON (else calls agents.objectives)")

    fd = sub.add_parser("format-directions",
                        help="Format existing_directions string from frontier JSON")
    fd.add_argument("--frontier-json", default=None,
                    help="Path to frontier JSON (else calls agents.objectives)")

    fa = sub.add_parser("fill-analysis", help="Fill RESEARCH_ANALYSIS_PROMPT and print")
    fa.add_argument("--purpose", default=None)
    fa.add_argument("--open-questions", default=None)
    fa.add_argument("--active-concepts", default=None)
    fa.add_argument("--wiki-baseline", default=None)
    fa.add_argument("--wiki-gaps", default=None)

    fs = sub.add_parser("fill-synthesis", help="Fill RESEARCH_SYNTHESIS_PROMPT and print")
    fs.add_argument("--purpose", default=None)
    fs.add_argument("--open-questions", default=None)
    fs.add_argument("--active-concepts", default=None)
    fs.add_argument("--gap-analysis", default=None)
    fs.add_argument("--existing-directions", default=None)

    pd = sub.add_parser("parse-directions",
                        help="Parse directions from synthesis output (file or stdin)")
    pd.add_argument("--input-file", default=None, help="Path to synthesis output text file")

    pp = sub.add_parser("parse-proposals",
                        help="Parse proposals from synthesis output (file or stdin)")
    pp.add_argument("--input-file", default=None, help="Path to synthesis output text file")

    args = ap.parse_args(argv)

    dispatch = {
        "gather-context": _cmd_gather_context,
        "format-questions": _cmd_format_questions,
        "format-directions": _cmd_format_directions,
        "fill-analysis": _cmd_fill_analysis,
        "fill-synthesis": _cmd_fill_synthesis,
        "parse-directions": _cmd_parse_directions,
        "parse-proposals": _cmd_parse_proposals,
    }
    fn = dispatch.get(args.cmd)
    if fn is None:
        print(f"unknown command: {args.cmd}", file=sys.stderr)
        return 2
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
