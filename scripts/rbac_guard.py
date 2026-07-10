#!/usr/bin/env python3
"""PreToolUse RBAC guard for Second Brain sub-agents.

Reads a Claude Code PreToolUse hook event on stdin (JSON) and decides whether an
Edit/Write/MultiEdit/NotebookEdit to a vault path is permitted for the acting agent. The
guard is keyed by an explicit role hint and is a BACKSTOP for the per-agent write allowlist
described in each agent definition (.claude/agents/<id>.md) and docs/vault-schema.md.

Role resolution (first hit wins):
  1. $SB_AGENT_ROLE environment variable (set when an agent/subagent is launched)
  2. event.get("agent") / event.get("subagent_type") / event.get("agent_id") if present
  3. unknown -> the guard does NOT block (avoids breaking the top-level interactive agent and
     non-vault repo work; only KNOWN restricted roles are enforced).

Decision protocol (Claude Code hooks):
  - print a JSON object on stdout:
      {"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "deny"|"allow", "permissionDecisionReason": "..."}}
  - exit 0 with that payload to communicate the decision.
  - on "allow" / no-opinion we emit nothing and exit 0 (defer to normal permission flow).

Only WRITE-class tools (Write, Edit, MultiEdit, NotebookEdit) are evaluated. Read/Grep/Glob/
Bash are never blocked here (Bash egress is gated elsewhere).

The vault path is matched RELATIVE to the active vault root. We resolve the vault root from
$VAULT_ROOT if set, else from `agents.vault_config path` if importable, else fall back to a
substring match on the known vault mount. Paths outside the vault (repo code edits) are not
governed by this guard and are allowed (those have their own ownership rules).

R4 user-proxy exception (research role only):
  The `objective/research_question/` folder is user-only. The research agent may write there
  ONLY when the $SB_SANCTIONED_SKILL environment variable equals "question-promote" or
  "question-solve". These two skills are user-invoked, user-approved state transitions. The
  variable MUST be set by the skill before any write and unset after. All other paths in the
  user-only objective area (purpose/, decision/) remain denied always, regardless of
  SB_SANCTIONED_SKILL. (The former user-only topic/ folder was retired; the subject axis now
  lives in wiki/concepts/ and is referenced from objective nodes via their related: block.)

  Contract for Wave-3 skill authors:
    - question-promote/SKILL.md: export SB_SANCTIONED_SKILL=question-promote before writing
      to objective/research_question/; unset after.
    - question-solve/SKILL.md: export SB_SANCTIONED_SKILL=question-solve before writing to
      objective/research_question/; unset after.
  The variable must be set in the same process environment that the PreToolUse hook reads
  (i.e. the subagent's shell environment, not a child subprocess).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import PurePosixPath

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Per-role write allowlist, expressed as path PREFIXES relative to the vault root.
# A write is allowed iff the vault-relative path starts with one of these prefixes.
# (Prefix match on path segments; "meta/ingest_index" also matches "meta/ingest_index.json".)
ALLOWLIST: dict[str, list[str]] = {
    "wiki": [
        "wiki/",
        "raw/papers/",
        "raw/articles/",
        "raw/transcripts/",
        "raw/notes/",
        "raw/opinions/",
        "raw/assets/",
        "raw/code/",
        "raw/notebooklm/",
        "meta/ingest_index",  # ingest_index.json / .md / ingest_index/ folder
    ],
    # research role: research-owned objective/ nodes + research/ outputs + nightly report.
    # objective/research_question/ is NOT listed here; it is handled by the R4 sanction check.
    "research": [
        "objective/research_question_proposal/",
        "objective/direction/",
        "objective/agent_todo/",
        "objective/hot.md",
        "objective/index.md",
        "research/",
        "meta/nightly_report/",
    ],
    # web role: writes ONLY the nightly digest via the Write tool.
    # The ingest index enqueue is a Bash CLI call (agents.ingest_index enqueue), NOT a Write
    # tool call, so it is not governed by this guard. Do NOT add meta/ingest_index here.
    "web": [
        "meta/nightly_report/",
    ],
}

# R4: paths that the research role may write ONLY when SB_SANCTIONED_SKILL is set to an
# approved value. The value must be one of RESEARCH_SANCTIONED_SKILLS.
# These paths are NOT in ALLOWLIST["research"] above; they are gated separately.
RESEARCH_SANCTIONED_PATHS: list[str] = [
    "objective/research_question/",
]
RESEARCH_SANCTIONED_SKILLS: frozenset[str] = frozenset(
    {"question-promote", "question-solve"}
)

# Roles we actively enforce. Other roles are unknown -> not blocked by this guard.
ENFORCED_ROLES = set(ALLOWLIST)


def _vault_root() -> str | None:
    root = os.environ.get("VAULT_ROOT")
    if root:
        return root.rstrip("/")
    try:
        # Best effort; importable only when run from the repo with the venv.
        from agents import vault_config  # type: ignore

        return str(vault_config.vault_path()).rstrip("/")
    except Exception:
        return None


def _resolve_role(event: dict) -> str | None:
    role = os.environ.get("SB_AGENT_ROLE")
    if role:
        return role.strip().lower()
    for key in ("agent", "subagent_type", "agent_id", "role"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    return None


def _tool_path(event: dict) -> str | None:
    tin = event.get("tool_input") or {}
    for key in ("file_path", "filePath", "path", "notebook_path"):
        val = tin.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _vault_relative(path: str, vault_root: str | None) -> str | None:
    """Return the vault-relative POSIX path, or None if the path is outside the vault."""
    norm = path.replace("\\", "/")
    if vault_root and norm.startswith(vault_root + "/"):
        return norm[len(vault_root) + 1 :]
    # Fallback: match the known Obsidian mount so the guard works without VAULT_ROOT.
    marker = "/Obsidian/"
    if marker in norm:
        tail = norm.split(marker, 1)[1]
        # tail = "<VaultName>/<rel...>"; drop the vault-name segment.
        parts = tail.split("/", 1)
        return parts[1] if len(parts) == 2 else ""
    return None


def _allowed(rel: str, role: str) -> bool:
    rel = rel.lstrip("/")
    prefixes = ALLOWLIST.get(role, [])
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in prefixes)


def _r4_sanctioned(rel: str) -> bool:
    """Return True iff the write is to a R4 research-sanctioned path AND SB_SANCTIONED_SKILL
    is set to an approved value. Only called for role=research."""
    rel_clean = rel.lstrip("/")
    is_sanctioned_path = any(
        rel_clean == p.rstrip("/") or rel_clean.startswith(p)
        for p in RESEARCH_SANCTIONED_PATHS
    )
    if not is_sanctioned_path:
        return False
    skill = os.environ.get("SB_SANCTIONED_SKILL", "").strip()
    return skill in RESEARCH_SANCTIONED_SKILLS


def decide(event: dict) -> dict | None:
    tool = event.get("tool_name") or event.get("tool")
    if tool not in WRITE_TOOLS:
        return None  # not a write tool; no opinion

    role = _resolve_role(event)
    if role not in ENFORCED_ROLES:
        return None  # unknown/unenforced role; defer

    path = _tool_path(event)
    if not path:
        return None  # nothing to evaluate

    rel = _vault_relative(path, _vault_root())
    if rel is None:
        return None  # outside the vault; not governed here

    if _allowed(rel, role):
        return None  # explicitly permitted; defer to normal flow

    # R4: research role may write to sanctioned paths when the sanction signal is present.
    if role == "research" and _r4_sanctioned(rel):
        return None  # user-proxy exception; defer to normal flow

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"RBAC: agent '{role}' may not {tool} vault path '{rel}'. "
                f"Allowed prefixes: {', '.join(ALLOWLIST.get(role, []))}. "
                f"See .claude/agents/{role}.md and docs/vault-schema.md."
            ),
        }
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # never break the tool flow on a parse error
    out = decide(event)
    if out is not None:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
