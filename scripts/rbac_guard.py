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
}

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
