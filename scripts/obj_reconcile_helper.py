#!/usr/bin/env python3
"""scripts/obj_reconcile_helper.py -- helper logic for the obj-reconcile skill.

This module provides the deterministic (non-LLM) detection passes for
obj-reconcile:
  - detect_stale_directions(nodes)    -> Pass A: directions serving solved questions
  - detect_orphan_directions(nodes)   -> Pass A variant: serves_question not in graph
  - detect_duplicate_proposals(nodes) -> Pass C/D overlap heuristic (pre-judge step)
  - detect_proposal_vs_question(nodes)-> Pass D: proposal duplicates existing open RQ
  - detect_no_concept_questions(nodes)-> Pass E: research_question with no linked concept
  - token_overlap(text_a, text_b)     -> word-level Jaccard similarity [0.0, 1.0]
  - slug(text, max_len=40)            -> ASCII slug for file naming
  - apply_frontmatter_update(path, updates) -> in-place additive frontmatter edit
  - write_agent_todo(vault_root, slug_suffix, body, today) -> create TODO-NNNN node

CLI:
  python scripts/obj_reconcile_helper.py detect --vault-root PATH
    Runs all detection passes and prints findings as JSON (no writes).

  python scripts/obj_reconcile_helper.py apply --vault-root PATH [--dry-run]
    Runs detection + applies all resolutions (research-owned nodes only) + writes
    agent_todo flags. Does NOT update hot.md / index.md (SKILL.md handles those).

No LLM calls are made here. The validation adjudication step is performed by the
research agent following the SKILL.md procedure (the agent reads the findings JSON,
calls the proxy for ambiguous pairs, then passes resolved/rejected verdicts back to
apply_frontmatter_update or write_agent_todo directly).
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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_vault_root(override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    for ev in ("VAULT_ROOT", "VAULT_PATH"):
        val = os.environ.get(ev)
        if val:
            return Path(val)
    code_root = Path(os.environ.get("CODE_PATH", str(_REPO_ROOT)))
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
        sys.exit(f"obj_reconcile_helper: cannot resolve vault root: {exc}")


def _import_objectives():
    """Import agents.objectives, inserting repo root into sys.path if needed."""
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from agents import objectives  # noqa: PLC0415
    return objectives


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STOPWORDS = frozenset(
    "a an the and or of in on to for with by from is are was were be been"
    " this that it its we our which what how why".split()
)


def token_overlap(text_a: str, text_b: str) -> float:
    """Jaccard similarity over lowercased, stop-word-filtered word tokens.

    Returns a value in [0.0, 1.0]. 1.0 = identical token sets, 0.0 = disjoint.
    """
    def _tokens(t: str) -> frozenset:
        return frozenset(
            w for w in re.findall(r"[a-z0-9]+", t.lower())
            if w not in _STOPWORDS and len(w) > 2
        )

    ta = _tokens(text_a)
    tb = _tokens(text_b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def slug(text: str, max_len: int = 40) -> str:
    """Generate an ASCII slug from text. Lowercase, hyphens, max_len chars."""
    lowered = text.lower()
    cleaned = _NON_ALNUM.sub("-", lowered)
    trimmed = cleaned.strip("-")[:max_len]
    return trimmed.rstrip("-") or "item"


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)


def _parse_fm(text: str) -> dict[str, str]:
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


def _body(text: str) -> str:
    m = _FM_RE.match(text)
    return text[m.end():] if m else text


def apply_frontmatter_update(path: Path, updates: dict[str, str]) -> None:
    """Additively update frontmatter fields in a markdown file.

    Only updates or adds the keys listed in `updates`. Does NOT rewrite the
    entire file - preserves existing fields and the body verbatim.
    Raises FileNotFoundError if path does not exist.
    """
    text = path.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        raise ValueError(f"No frontmatter block found in {path}")

    fm_block = m.group(1)
    lines = fm_block.splitlines()
    updated_keys: set[str] = set()

    # Update existing lines.
    new_lines: list[str] = []
    for line in lines:
        if ":" in line and not line.lstrip().startswith("#"):
            k = line.partition(":")[0].strip().lower()
            if k in updates:
                new_lines.append(f"{k}: {updates[k]}")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    # Append any keys that were not already in the frontmatter.
    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}: {v}")

    new_fm = "---\n" + "\n".join(new_lines) + "\n---"
    # Reconstruct: new frontmatter + everything after the closing ---
    after_fm = text[m.end():]
    new_text = new_fm + ("\n" if not after_fm.startswith("\n") else "") + after_fm
    path.write_text(new_text, encoding="utf-8")


def write_agent_todo(
    vault_root: Path,
    slug_suffix: str,
    body: str,
    today: str,
    objectives_mod=None,
) -> str:
    """Create a new TODO-NNNN agent_todo node. Returns the assigned id string.

    `objectives_mod` is the `agents.objectives` module (injected for testing).
    If None, import it from the repo.
    """
    if objectives_mod is None:
        objectives_mod = _import_objectives()

    todo_id = objectives_mod.next_id(vault_root, "agent_todo")
    slug_part = slug(slug_suffix, max_len=35)
    filename = f"{todo_id}-{slug_part}.md"
    todo_dir = vault_root / "objective" / "agent_todo"
    todo_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = (
        f"---\n"
        f"type: agent_todo\n"
        f"id: {todo_id}\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"written_by: research\n"
        f"status: open\n"
        f"---\n"
    )
    content = frontmatter + "\n" + body.lstrip("\n")
    (todo_dir / filename).write_text(content, encoding="utf-8")
    return todo_id


# ---------------------------------------------------------------------------
# Detection passes (pure, no writes)
# ---------------------------------------------------------------------------

def detect_stale_directions(nodes: list[dict]) -> list[dict]:
    """Pass A: find directions serving a now-solved research_question.

    Returns a list of findings dicts:
      {type: "stale-solved", node_id, path, reason, serves_question}
    """
    solved_ids: set[str] = {
        n["id"]
        for n in nodes
        if n["type"] == "research_question" and n.get("solved", "").lower() == "yes"
    }
    rq_ids: set[str] = {
        n["id"] for n in nodes if n["type"] == "research_question"
    }

    findings: list[dict] = []
    for n in nodes:
        if n["type"] != "direction":
            continue
        status = n.get("status", "").lower()
        if status == "superseded":
            continue  # already resolved

        serves = n.get("serves_question", "").strip()
        if not serves:
            continue

        # serves_question may be a comma-separated list e.g. "Q-0001, Q-0002"
        served_ids = [s.strip() for s in serves.split(",") if s.strip()]

        for sid in served_ids:
            if sid in solved_ids:
                findings.append({
                    "type": "stale-solved",
                    "node_id": n["id"],
                    "path": n["path"],
                    "serves_question": sid,
                    "reason": f"serves_question {sid} is solved",
                })
                break  # one finding per direction is enough
            if sid not in rq_ids:
                findings.append({
                    "type": "stale-orphan",
                    "node_id": n["id"],
                    "path": n["path"],
                    "serves_question": sid,
                    "reason": f"serves_question {sid} does not exist in objective graph",
                })
                break

    return findings


def detect_duplicate_proposals(nodes: list[dict], overlap_threshold: float = 0.6) -> list[dict]:
    """Pass C: find pending proposals with substantially overlapping text.

    Returns pairs list:
      {type: "duplicate-proposals", node_a: {id, path}, node_b: {id, path},
       overlap: float, reason: str}
    """
    pending = [
        n for n in nodes
        if n["type"] == "research_question_proposal"
        and n.get("status", "").lower() == "pending"
    ]

    findings: list[dict] = []
    checked: set[tuple[str, str]] = set()
    for i, a in enumerate(pending):
        for b in pending[i + 1:]:
            key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
            if key in checked:
                continue
            checked.add(key)

            # Use the body (proposed question text) for comparison.
            ov = token_overlap(a.get("body", ""), b.get("body", ""))
            if ov >= overlap_threshold:
                findings.append({
                    "type": "duplicate-proposals",
                    "node_a": {"id": a["id"], "path": a["path"]},
                    "node_b": {"id": b["id"], "path": b["path"]},
                    "overlap": round(ov, 3),
                    "reason": f"token overlap {ov:.0%} >= threshold {overlap_threshold:.0%}",
                })

    return findings


def detect_proposal_vs_question(nodes: list[dict], overlap_threshold: float = 0.6) -> list[dict]:
    """Pass D: find pending proposals that duplicate an existing open research_question.

    Returns findings:
      {type: "redundant-proposal", node_id: QP-NNNN, path, duplicates_question: Q-NNNN,
       overlap: float, reason: str}
    """
    pending = [
        n for n in nodes
        if n["type"] == "research_question_proposal"
        and n.get("status", "").lower() == "pending"
    ]
    open_rqs = [
        n for n in nodes
        if n["type"] == "research_question"
        and n.get("solved", "").lower() != "yes"
    ]

    findings: list[dict] = []
    for qp in pending:
        for rq in open_rqs:
            ov = token_overlap(qp.get("body", ""), rq.get("body", ""))
            if ov >= overlap_threshold:
                findings.append({
                    "type": "redundant-proposal",
                    "node_id": qp["id"],
                    "path": qp["path"],
                    "duplicates_question": rq["id"],
                    "overlap": round(ov, 3),
                    "reason": (
                        f"proposal overlaps existing open question {rq['id']} "
                        f"(token overlap {ov:.0%})"
                    ),
                })
                break  # one match per proposal is enough

    return findings


def detect_no_concept_questions(nodes: list[dict]) -> list[dict]:
    """Pass E: find research_questions with no linked concept (user-only - only flag).

    A research_question is associated with the subject axis via one or more
    ``[[wiki/concepts/<slug>]]`` links in its ``related:`` frontmatter. A question
    whose ``related:`` carries no concept link is flagged.

    Returns findings:
      {type: "no-linked-concept", node_id: Q-NNNN, path, reason: str}
    """
    findings: list[dict] = []
    for n in nodes:
        if n["type"] != "research_question":
            continue
        # scan_objectives pre-extracts concept slugs from related:; fall back to
        # parsing the raw related: value if the node dict predates that field.
        concepts = n.get("concepts")
        if concepts is None:
            objectives = _import_objectives()
            concepts = objectives.concept_slugs(n.get("related", ""))
        if not concepts:
            findings.append({
                "type": "no-linked-concept",
                "node_id": n["id"],
                "path": n["path"],
                "reason": "research_question has no linked concept",
            })
    return findings


def run_all_detections(nodes: list[dict]) -> dict:
    """Run all five detection passes and return a combined findings dict."""
    return {
        "stale_directions": detect_stale_directions(nodes),
        "duplicate_proposals": detect_duplicate_proposals(nodes),
        "redundant_proposals": detect_proposal_vs_question(nodes),
        "no_concept_questions": detect_no_concept_questions(nodes),
    }


# ---------------------------------------------------------------------------
# Apply resolutions (research-owned nodes only)
# ---------------------------------------------------------------------------

def apply_resolutions(
    vault_root: Path,
    findings: dict,
    today: str,
    objectives_mod=None,
    dry_run: bool = False,
) -> dict:
    """Apply research-owned resolutions for all findings.

    - stale_directions: set status=superseded + superseded_reason
    - redundant_proposals: set status=rejected + rejection_reason
    - no_concept_questions + duplicate_proposals: write agent_todo flags

    Returns a summary dict:
      {superseded: N, rejected: M, todos_written: P, skipped: K}
    """
    if objectives_mod is None:
        objectives_mod = _import_objectives()

    summary = {"superseded": 0, "rejected": 0, "todos_written": 0, "skipped": 0}

    # -- Pass A: stale directions
    for f in findings.get("stale_directions", []):
        path = vault_root / f["path"]
        if not path.exists():
            summary["skipped"] += 1
            continue
        updates = {
            "status": "superseded",
            "superseded_reason": f["reason"],
            "updated": today,
        }
        if dry_run:
            print(f"[dry-run] supersede {f['path']}: {f['reason']}")
        else:
            apply_frontmatter_update(path, updates)
        summary["superseded"] += 1

    # -- Pass D: redundant proposals vs existing questions
    for f in findings.get("redundant_proposals", []):
        path = vault_root / f["path"]
        if not path.exists():
            summary["skipped"] += 1
            continue
        updates = {
            "status": "rejected",
            "rejection_reason": f"duplicates existing question {f['duplicates_question']}",
            "updated": today,
        }
        if dry_run:
            print(f"[dry-run] reject {f['path']}: {f['reason']}")
        else:
            apply_frontmatter_update(path, updates)
        summary["rejected"] += 1

    # -- Pass E: no-linked-concept research_questions -> write agent_todo flags
    for f in findings.get("no_concept_questions", []):
        # Extract Q-NNNN id from path for the TODO slug.
        node_id = f["node_id"]
        body = (
            f"## For future Claude\n"
            f"This TODO was written by obj-reconcile on {today}. It flags a "
            f"research_question that has no linked concept. The user should link "
            f"the question to one or more active concepts via its related: field.\n\n"
            f"## Flag: research_question with no linked concept\n"
            f"**Question:** [[{f['path']}]]\n"
            f"**Issue:** The `related:` field carries no `[[wiki/concepts/<slug>]]` "
            f"link. This question will not appear in any concept-scoped analysis until "
            f"a concept is linked.\n"
            f"**Suggested action:** Edit `{f['path']}` and add a "
            f"`[[wiki/concepts/<slug>]]` link to its `related:` field to associate it "
            f"with the most appropriate active concept.\n"
        )
        if dry_run:
            print(f"[dry-run] write TODO for no-linked-concept {node_id}")
        else:
            write_agent_todo(
                vault_root,
                f"no-concept-{node_id}",
                body,
                today,
                objectives_mod=objectives_mod,
            )
        summary["todos_written"] += 1

    # -- Pass C: duplicate proposals -> write agent_todo flags (adjudication needed)
    for f in findings.get("duplicate_proposals", []):
        id_a = f["node_a"]["id"]
        id_b = f["node_b"]["id"]
        path_a = f["node_a"]["path"]
        path_b = f["node_b"]["path"]
        body = (
            f"## For future Claude\n"
            f"This TODO was written by obj-reconcile on {today}. Two pending proposals "
            f"have substantially overlapping content (overlap={f['overlap']:.0%}). They "
            f"may be duplicates. The research agent should call the validation judge to "
            f"confirm, or the user can resolve manually.\n\n"
            f"## Flag: possible duplicate proposals (adjudication needed)\n"
            f"**Proposal A:** [[{path_a}]] ({id_a})\n"
            f"**Proposal B:** [[{path_b}]] ({id_b})\n"
            f"**Overlap:** {f['overlap']:.0%} token similarity\n"
            f"**Issue:** {f['reason']}\n"
            f"**Suggested action:** Compare both proposals. If duplicate, reject the weaker "
            f"one by running obj-reconcile with the adjudication step enabled, or edit "
            f"the proposal frontmatter directly (set status: rejected + rejection_reason).\n"
        )
        if dry_run:
            print(f"[dry-run] write TODO for duplicate proposals {id_a}/{id_b}")
        else:
            write_agent_todo(
                vault_root,
                f"dup-prop-{id_a}-{id_b}",
                body,
                today,
                objectives_mod=objectives_mod,
            )
        summary["todos_written"] += 1

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_detect(args: argparse.Namespace) -> int:
    vault_root = _resolve_vault_root(args.vault_root)
    objectives = _import_objectives()
    nodes = objectives.scan_objectives(vault_root)
    findings = run_all_detections(nodes)
    print(json.dumps(findings, indent=2))
    return 0


def _cli_apply(args: argparse.Namespace) -> int:
    vault_root = _resolve_vault_root(args.vault_root)
    objectives = _import_objectives()
    nodes = objectives.scan_objectives(vault_root)
    findings = run_all_detections(nodes)
    today = datetime.date.today().isoformat()
    summary = apply_resolutions(
        vault_root,
        findings,
        today,
        objectives_mod=objectives,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="obj-reconcile helper: detect + apply objective graph inconsistencies."
    )
    ap.add_argument("--vault-root", default=None, help="Override vault root path")
    sub = ap.add_subparsers(dest="verb", required=True)

    detect_p = sub.add_parser("detect", help="Detect all inconsistencies (no writes)")
    detect_p.add_argument("--vault-root", default=None, dest="vault_root")

    apply_p = sub.add_parser("apply", help="Detect and apply resolutions")
    apply_p.add_argument("--vault-root", default=None, dest="vault_root")
    apply_p.add_argument("--dry-run", action="store_true", help="Print without writing")

    args = ap.parse_args(argv)

    if args.verb == "detect":
        return _cli_detect(args)
    if args.verb == "apply":
        return _cli_apply(args)
    print(f"unknown verb: {args.verb}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
