#!/usr/bin/env python3
"""wiki_focus_zone: rolling tracker of recently-touched wiki nodes.

The store (meta/focus_zone.json) lives in the CODE REPO, not the vault.
The vault root is only used to relativize absolute file paths from hook events.

Modes:
  (default / hook)  - read a Claude Code PostToolUse JSON event from stdin, record the
                      touched wiki page into <code_repo>/meta/focus_zone.json.
  render            - print the ## Focus zone (last 10 nodes) markdown section for hot.md.
  list              - dump meta/focus_zone.json for inspection.
  record <path>     - directly record a vault-relative or absolute path (for manual/test use).

Called by the PostToolUse hook in .claude/settings.json. Always exits 0 (never blocks).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

WINDOW_SIZE = 10
STORE_REL = "meta/focus_zone.json"  # relative to CODE REPO root

# Infrastructure files that are NOT knowledge nodes.
SKIP_NAMES = {
    "hot.md", "log.md", "index.md", "_template.md",
    "gap/index.md", "gaps.md",
}

# Top-level vault areas to track (vault-relative path prefixes).
TRACKED_PREFIXES = ("wiki/", "objective/")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _resolve_code_repo() -> Path:
    """Return the code repo root (where this script lives under scripts/)."""
    env = os.environ.get("CODE_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def _resolve_vault_root() -> Path | None:
    """Return the active vault root (needed only to relativize hook file paths)."""
    env = os.environ.get("VAULT_ROOT")
    if env:
        return Path(env)
    try:
        repo = _resolve_code_repo()
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from agents import vault_config  # type: ignore
        return Path(vault_config.vault_path())
    except Exception:
        return None


def _store_path(code_repo: Path) -> Path:
    return code_repo / STORE_REL


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------

def _load(store: Path) -> list[dict]:
    if not store.exists():
        return []
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        return data.get("window", [])
    except Exception:
        return []


def _save(store: Path, window: list[dict]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        json.dumps({"window": window}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _record(store: Path, rel_path: str) -> None:
    """Add a vault-relative path to the rolling window."""
    name = Path(rel_path).name
    if name in SKIP_NAMES:
        return
    if not any(rel_path.startswith(p) for p in TRACKED_PREFIXES):
        return

    window = _load(store)
    window = [e for e in window if e.get("path") != rel_path]
    window.insert(0, {
        "path": rel_path,
        "touched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    })
    window = window[:WINDOW_SIZE]
    _save(store, window)


# ---------------------------------------------------------------------------
# Public verbs
# ---------------------------------------------------------------------------

def cmd_render(store: Path) -> None:
    window = _load(store)
    print("## Focus zone (last 10 nodes)")
    if not window:
        print("-")
        return
    for entry in window:
        path = entry.get("path", "")
        date = entry.get("touched_at", "")[:10]
        slug = path[:-3] if path.endswith(".md") else path
        print(f"- [[{slug}]] -- {date}")


def cmd_record_direct(store: Path, vault_root: Path | None, path_arg: str) -> None:
    abs_path = Path(path_arg)
    if abs_path.is_absolute():
        if vault_root is None:
            return
        try:
            rel_path = str(abs_path.relative_to(vault_root))
        except ValueError:
            return
    else:
        rel_path = path_arg
    _record(store, rel_path)


def cmd_list(store: Path) -> None:
    window = _load(store)
    print(json.dumps({"window": window}, indent=2))


def cmd_hook(store: Path, vault_root: Path | None) -> None:
    """Read PostToolUse event from stdin and record if it's a wiki page."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except Exception:
        return

    tool_input = event.get("tool_input") or event.get("toolInput") or {}
    file_path: str | None = None
    for key in ("file_path", "filePath", "path", "notebook_path"):
        val = tool_input.get(key)
        if val:
            file_path = val
            break

    if not file_path:
        return

    abs_path = Path(file_path)
    if abs_path.is_absolute():
        if vault_root is None:
            return
        try:
            rel_path = str(abs_path.relative_to(vault_root))
        except ValueError:
            return
    else:
        rel_path = file_path

    _record(store, rel_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    code_repo = _resolve_code_repo()
    store = _store_path(code_repo)
    vault_root = _resolve_vault_root()

    args = sys.argv[1:]

    if not args:
        cmd_hook(store, vault_root)
    elif args[0] == "render":
        cmd_render(store)
    elif args[0] == "list":
        cmd_list(store)
    elif args[0] == "record" and len(args) >= 2:
        cmd_record_direct(store, vault_root, args[1])
    else:
        cmd_hook(store, vault_root)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
