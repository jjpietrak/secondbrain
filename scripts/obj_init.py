#!/usr/bin/env python3
"""obj-init: scaffold the objective/ folder tree for a Second Brain vault (Phase 2).

Two modes:

  --dry-run  (default) - print what would be created; do NOT write anything to the live vault.
             Uses a /tmp copy (mirrors wiki_init.py reconcile pattern): snapshot -> apply to
             copy -> print diff summary -> print STOP. Zero writes to the live vault.

  --apply    - create all missing folders + templates + index.md + hot.md on the LIVE vault.
             Additive + idempotent: existing files are NEVER overwritten.

Node types and folders created:

    objective/purpose/          _template.md
    objective/topic/            _template.md
    objective/research_question/_template.md
    objective/decision/         _template.md
    objective/research_question_proposal/  _template.md
    objective/direction/        _template.md
    objective/agent_todo/       _template.md
    objective/index.md          (seed frontmatter; next_id counters)
    objective/hot.md            (seed frontmatter)

All folders and files are created only if they do not already exist. An existing
`_template.md` is NEVER overwritten (the plan's guideline #5 and idempotency rule).

Resolve $VAULT_ROOT via agents.vault_config; never hard-code a vault path.

CLI:
  python scripts/obj_init.py [--vault-root PATH] [--dry-run]
  python scripts/obj_init.py [--vault-root PATH] --apply
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Folder list (relative to vault root)
# ---------------------------------------------------------------------------

OBJECTIVE_FOLDERS = [
    "objective/purpose",
    "objective/topic",
    "objective/research_question",
    "objective/decision",
    "objective/research_question_proposal",
    "objective/direction",
    "objective/agent_todo",
]


# ---------------------------------------------------------------------------
# Template factories (one per node type)
# ---------------------------------------------------------------------------

def _purpose_template() -> str:
    return """\
---
type: purpose
id: purpose
created: {{date}}
updated: {{date}}
status: active
vault: {{vault}}
---

## For future Claude
This is the vault PURPOSE file. It is the single most important context file
in the vault. It defines the research mission, scope, and what success looks
like. Read this first on every task. There is exactly ONE purpose per vault.
Edit only the body (the frontmatter status field is user-controlled).

# Vault purpose

## Mission
<!-- One paragraph: what this vault is for and what it tracks. -->

## Scope
<!-- What is IN scope and explicitly OUT of scope. -->

## Success criteria
<!-- How would you know the vault's mission is accomplished? -->
"""


def _topic_template() -> str:
    return """\
---
type: topic
id: T-NNNN
created: {{date}}
updated: {{date}}
status: active
related_questions: []
---

## For future Claude
This is a TOPIC node. Topics are confirmed research directions or sub-problems
under the vault purpose. They are USER-ONLY (never created by agents).
status: active | paused | completed
related_questions: list of Q-NNNN ids that fall under this topic.

# Topic: {{title}}

## Summary
<!-- One paragraph: what this topic covers and why it matters. -->

## Open questions
<!-- Q-NNNN wikilinks pointing to research_question nodes. -->
-

## Key findings so far
-
"""


def _research_question_template() -> str:
    return """\
---
type: research_question
id: Q-NNNN
created: {{date}}
updated: {{date}}
solved: "no"
topic: T-NNNN
priority: medium
answer_ref: ""
---

## For future Claude
This is a RESEARCH QUESTION node. It is USER-ONLY.
solved: "yes" | "no" -- only the USER sets this; agents NEVER flip it autonomously.
priority: high | medium | low
answer_ref: filled by question-solve skill pointing to research/<question_id>.md

# Research question

<!-- State the precise, falsifiable question this node tracks. -->

## Background
<!-- Why does this question matter? What is already known? -->

## Acceptance criteria
<!-- What evidence or analysis would count as a satisfactory answer? -->
"""


def _decision_template() -> str:
    return """\
---
type: decision
id: D-NNNN
created: {{date}}
updated: {{date}}
status: active
scope: all
---

## For future Claude
This is a DECISION node. Decisions are agent behavioral constraints authored by
the user. They are read at every research agent task start and applied as hard
constraints. status: active | superseded | archived.
scope: all | wiki | research | web -- which agents this decision constrains.

# Decision

<!-- State the constraint in plain imperative prose. Example:
     "Do not fetch web content from the research agent (no WebSearch/WebFetch)."
-->

## Rationale
<!-- Why was this decision made? -->
"""


def _research_question_proposal_template() -> str:
    return """\
---
type: research_question_proposal
id: QP-NNNN
created: {{date}}
updated: {{date}}
written_by: research
from_gap: ""
status: pending
---

## For future Claude
This is a RESEARCH QUESTION PROPOSAL node. Written by the research agent when
synthesis reveals a gap no current research_question covers. Awaiting user
approval. status: pending | approved | rejected.
On user approval the question-promote skill creates a research_question node
and sets this file's status to approved.

# Proposed research question

<!-- The proposed question: precise, falsifiable. -->

## Rationale
<!-- Why does the vault need this question? Which gap or direction prompted it? -->

## Source direction
<!-- DIR-NNNN or gap title that surfaced this proposal. -->
"""


def _direction_template() -> str:
    return """\
---
type: direction
id: DIR-NNNN
created: {{date}}
updated: {{date}}
written_by: research
serves_question: Q-NNNN
topics: []
targets_gap: ""
priority: medium
status: open
---

## For future Claude
This is a DIRECTION node. Directions are crawl/reasoning trajectories proposed
by the research agent, not yet promoted to topics. Written by obj-synth or
deep-synthesis skills. status: open | crawled | superseded.
priority: high | medium | low.
The body fields (reasoning_pattern, expected_evidence, seed_queries, solves_when)
are machine-parseable by parse_directions() from scripts/prompts/pipeline_prompts.py.

# Direction

## reasoning_pattern
<!-- How to approach this direction: what to look for, what to infer. -->

## expected_evidence
<!-- What artifact or fact would confirm progress on this direction. -->

## seed_queries
<!-- Starting queries or wiki sections to read. -->

## solves_when
<!-- What condition would allow marking this direction as crawled or superseded. -->
"""


def _agent_todo_template() -> str:
    return """\
---
type: agent_todo
id: TODO-NNNN
created: {{date}}
updated: {{date}}
written_by: research
status: open
---

## For future Claude
This is an AGENT TODO node. Self-correcting instructions and reflections
written by the research agent for future research agent invocations.
status: open | done | superseded.

# Agent TODO

<!-- The concrete next action or self-correction. -->

## Context
<!-- What prompted this TODO and what the agent was doing at the time. -->
"""


def _index_seed(today: str) -> str:
    return f"""\
---
type: index
updated: {today}
next_id:
  topic: 1
  research_question: 1
  decision: 1
  research_question_proposal: 1
  direction: 1
  agent_todo: 1
ai-first: true
---

## For future Claude
This is the objective/ index file. It is maintained by agents/objectives.py.
The frontmatter next_id counters are the authoritative source for new node ids.
Do not edit next_id manually; use python -m agents.objectives next-id <type>.
The body table records one row per agent operation on the objective graph.

# Objective index

| Operation | Node | Agent | Date | Notes |
|-----------|------|-------|------|-------|
"""


def _hot_seed(today: str) -> str:
    return f"""\
---
type: hot
updated: {today}
written_by: research
---

## For future Claude
This is the objective hot-cache: the last objective-reasoning context written
by the research agent. It records the current focus, open threads, and proposed
next actions. Read this to orient before starting a research task.
Keep under ~500 words; older context graduates to objective/index.md log rows.

# Objective hot cache

## Last updated
{today}

## Current focus
-

## Open threads
-

## Proposed next actions
-
"""


# Map from vault-relative path to template factory function.
# Only paths inside objective/ subdirs (not the flat files).
TEMPLATES: dict[str, type] = {
    "objective/purpose/_template.md": _purpose_template,
    "objective/topic/_template.md": _topic_template,
    "objective/research_question/_template.md": _research_question_template,
    "objective/decision/_template.md": _decision_template,
    "objective/research_question_proposal/_template.md": _research_question_proposal_template,
    "objective/direction/_template.md": _direction_template,
    "objective/agent_todo/_template.md": _agent_todo_template,
}


# ---------------------------------------------------------------------------
# Vault root resolution (mirrors wiki_init.py)
# ---------------------------------------------------------------------------

def resolve_vault_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("VAULT_ROOT")
    if env:
        return Path(env)
    try:
        repo = Path(os.environ.get("CODE_PATH", "/home/jpietrak/second_brain"))
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from agents import vault_config  # type: ignore
        return Path(vault_config.vault_path())
    except Exception as exc:
        raise SystemExit(
            "could not resolve vault root: pass --vault-root or set $VAULT_ROOT "
            f"(vault_config import failed: {exc})"
        )


# ---------------------------------------------------------------------------
# Plan (change recorder)
# ---------------------------------------------------------------------------

class Plan:
    """Records what scaffold_tree changed."""

    def __init__(self) -> None:
        self.folders_created: list[str] = []
        self.templates_added: list[str] = []
        self.index_created: bool = False
        self.hot_created: bool = False

    def is_noop(self) -> bool:
        return not (
            self.folders_created
            or self.templates_added
            or self.index_created
            or self.hot_created
        )

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        lines.append(f"  folders created : {len(self.folders_created)} -> {self.folders_created}")
        lines.append(f"  templates added : {len(self.templates_added)} -> {self.templates_added}")
        lines.append(f"  index.md created: {self.index_created}")
        lines.append(f"  hot.md created  : {self.hot_created}")
        return lines


# ---------------------------------------------------------------------------
# Core scaffold (operates on target directory; used for both dry-run copy + apply)
# ---------------------------------------------------------------------------

def scaffold_tree(root: Path, plan: Plan) -> None:
    """Apply the objective/ scaffold to `root`. Idempotent (never overwrites)."""
    today = _dt.date.today().isoformat()

    # 1. Create missing objective/ subdirectories.
    for rel in OBJECTIVE_FOLDERS:
        d = root / rel
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            # .gitkeep so empty dirs persist under git.
            (d / ".gitkeep").write_text("", encoding="utf-8")
            plan.folders_created.append(rel)

    # 2. Write _template.md into each subfolder if absent. NEVER overwrite.
    for rel, factory in TEMPLATES.items():
        path = root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(factory(), encoding="utf-8")
            plan.templates_added.append(rel)

    # 3. Create objective/index.md with seed frontmatter if absent.
    index_path = root / "objective" / "index.md"
    if not index_path.exists():
        (root / "objective").mkdir(parents=True, exist_ok=True)
        index_path.write_text(_index_seed(today), encoding="utf-8")
        plan.index_created = True

    # 4. Create objective/hot.md with seed frontmatter if absent.
    hot_path = root / "objective" / "hot.md"
    if not hot_path.exists():
        (root / "objective").mkdir(parents=True, exist_ok=True)
        hot_path.write_text(_hot_seed(today), encoding="utf-8")
        plan.hot_created = True


# ---------------------------------------------------------------------------
# Dry-run-on-copy mode
# ---------------------------------------------------------------------------

def _snapshot(vault_root: Path, dest: Path) -> None:
    """Copy the live vault to dest, skipping .git and .obsidian."""
    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in {".git", ".obsidian"}}
    shutil.copytree(vault_root, dest, ignore=_ignore, symlinks=False)


def run_dry_run(vault_root: Path) -> Plan:
    """Snapshot the vault to /tmp, apply scaffold to the copy, print summary. Zero live writes."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        copy_dir = Path(td) / "obj-init-dryrun"
        if vault_root.exists():
            _snapshot(vault_root, copy_dir)
        else:
            copy_dir.mkdir(parents=True, exist_ok=True)

        plan = Plan()
        scaffold_tree(copy_dir, plan)

    print("== obj-init dry-run summary (NO writes to live vault) ==")
    if plan.is_noop():
        print("  (no-op: all objective/ structure already present)")
    else:
        for line in plan.summary_lines():
            print(line)
    print("STOP: review the plan above, then run --apply to create on the live vault.")
    return plan


def run_apply(vault_root: Path) -> Plan:
    """Apply scaffold to the live vault (additive + idempotent)."""
    vault_root.mkdir(parents=True, exist_ok=True)
    plan = Plan()
    scaffold_tree(vault_root, plan)
    print("== obj-init applied ==")
    if plan.is_noop():
        print("  (no-op: all objective/ structure already present)")
    else:
        for line in plan.summary_lines():
            print(line)
    return plan


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="obj-init: scaffold objective/ node folders for a Second Brain vault."
    )
    ap.add_argument(
        "--vault-root",
        default=None,
        help="Override vault root path (default: resolved via agents.vault_config)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Apply to the live vault (additive + idempotent). Default is dry-run.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Dry-run mode: snapshot + apply to copy, print summary, no live writes (default).",
    )

    args = ap.parse_args(argv)
    vault_root = resolve_vault_root(args.vault_root)

    if args.apply:
        run_apply(vault_root)
    else:
        run_dry_run(vault_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
