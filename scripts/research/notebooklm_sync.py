#!/usr/bin/env python3
"""Bi-directional sync between an Obsidian vault folder and a real Google NotebookLM
notebook, via the `nlm` CLI (NotebookLM Tools).

This is the REAL-notebook counterpart to notebooklm.py (which is ephemeral Gemini
File Search). It costs $0 — `nlm` authenticates with your Google account browser
cookies and touches none of the three billing pools.

DESIGN — split authority by subfolder (conflict-free):

  research/notebooklm/<slug>/
    push/      vault-authored .md  -> pushed UP as NotebookLM *sources*  (vault wins)
    notes/     NotebookLM *notes*  -> pulled DOWN as .md                 (notebook wins)
    artifacts/ downloaded studio outputs (reports, audio, ...)           (pull only)
    .nlm-sync.json  manifest: source-ids, note-ids, content hashes, timestamps

NotebookLM has two writable surfaces with different semantics:
  - sources: add/delete (not editable in place); these GROUND the notebook AI.
  - notes:   create/update/delete with stable ids; a true 2-way notepad, but they
             do NOT ground the AI.
We map vault .md -> sources (so your notes feed the AI) and notebook notes -> vault
.md. Each object has exactly one authoritative side, so there is never a merge
conflict and no per-object mtime is required (the CLI does not expose one).

Auth: `nlm` sessions last ~20 min (browser cookies). Run `nlm login` once in a
terminal with a Chromium-family browser. This script gates on `nlm login --check`
and exits 2 (not 1) when auth is missing, so callers can skip gracefully.

Usage:
  uv run -m scripts.research.notebooklm_sync --notebook <id|alias> [--dry-run]
  uv run -m scripts.research.notebooklm_sync --notebook <id> --probe   # dump JSON shapes
  uv run -m scripts.research.notebooklm_sync --notebook <id> --pull-only
  uv run -m scripts.research.notebooklm_sync --notebook <id> --push-only
  uv run -m scripts.research.notebooklm_sync --notebook <id> --prune   # delete remote sources whose local file was removed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .lib.config import VAULT_PATH

NOTEBOOKLM_ROOT = VAULT_PATH / "research" / "notebooklm"
MANIFEST_NAME = ".nlm-sync.json"
# nlm reports MD as a supported source file type; these are what we push.
PUSH_EXTS = {".md", ".markdown", ".txt"}


# --------------------------------------------------------------------------- #
# nlm CLI plumbing
# --------------------------------------------------------------------------- #
def nlm(args: list[str], *, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    """Invoke the nlm CLI. Returns the CompletedProcess (stdout captured by default)."""
    cmd = ["nlm", *args]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
    )


def nlm_json(args: list[str]) -> object:
    """Run an nlm command with --json and parse stdout. Returns parsed JSON or None."""
    proc = nlm([*args, "--json"], check=False)
    if proc.returncode != 0:
        print(f"WARNING: `nlm {' '.join(args)} --json` exited {proc.returncode}: "
              f"{proc.stderr.strip()[:200]}", file=sys.stderr)
        return None
    out = proc.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # Some nlm builds prefix human chatter before the JSON; grab the first {...} or [...].
        m = re.search(r"(\{.*\}|\[.*\])", out, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        print(f"WARNING: could not parse JSON from `nlm {' '.join(args)}`", file=sys.stderr)
        return None


def check_auth() -> bool:
    proc = nlm(["login", "--check"], check=False)
    blob = (proc.stdout + proc.stderr).lower()
    return proc.returncode == 0 and "error" not in blob and "expired" not in blob


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def slugify(text: str) -> str:
    text = (text or "untitled").lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    return (text or "untitled")[:80]


def pick(d: dict, *keys: str, default=None):
    """Return the first present, non-empty key from a dict (tolerates CLI shape drift)."""
    for k in keys:
        if isinstance(d, dict) and d.get(k):
            return d[k]
    return default


def load_manifest(folder: Path) -> dict:
    path = folder / MANIFEST_NAME
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {"notebook_id": None, "notebook_title": None, "last_sync": None,
            "sources": {}, "notes": {}}


def save_manifest(folder: Path, manifest: dict, dry_run: bool) -> None:
    manifest["last_sync"] = now_iso()
    if dry_run:
        print(f"  [dry-run] would write manifest {folder / MANIFEST_NAME}", file=sys.stderr)
        return
    (folder / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- #
# PULL: NotebookLM notes -> vault notes/*.md   (notebook is authoritative)
# --------------------------------------------------------------------------- #
NOTE_TEMPLATE = """---
nlm_note_id: {note_id}
nlm_notebook_id: {notebook_id}
type: notebooklm-note
synced_at: {synced_at}
source: notebooklm
---

# {title}

{content}
"""


def pull_notes(notebook_id: str, folder: Path, manifest: dict, dry_run: bool) -> int:
    notes = nlm_json(["note", "list", notebook_id])
    if notes is None:
        print("  no notes returned (or auth/JSON issue)", file=sys.stderr)
        return 0
    if isinstance(notes, dict):
        notes = notes.get("notes") or notes.get("items") or []
    notes_dir = folder / "notes"
    conflicts_dir = folder / ".conflicts"
    changed = 0
    seen_ids: set[str] = set()

    for note in notes:
        note_id = str(pick(note, "id", "note_id", "noteId", default="")).strip()
        if not note_id:
            continue
        seen_ids.add(note_id)
        title = pick(note, "title", "name", default="Untitled note")
        content = pick(note, "content", "text", "body", default="") or ""
        remote_hash = sha(title + "\n" + content)
        record = manifest["notes"].get(note_id, {})
        rel = record.get("file") or f"notes/{slugify(title)}-{note_id[:8]}.md"
        dest = folder / rel

        # Skip if remote unchanged since last pull.
        if record.get("hash") == remote_hash and dest.exists():
            continue

        # Local edit detection: notes/ is pull-authoritative, but never silently
        # destroy a local edit — back it up to .conflicts/ first.
        if dest.exists():
            local_hash = sha_of_note_file(dest)
            if local_hash and local_hash != record.get("hash"):
                if not dry_run:
                    conflicts_dir.mkdir(parents=True, exist_ok=True)
                    backup = conflicts_dir / f"{dest.stem}.local.{now_iso().replace(':', '')}.md"
                    backup.write_text(dest.read_text(errors="ignore"))
                print(f"  CONFLICT: local edit to {rel} backed up to .conflicts/ "
                      f"(notebook version wins)", file=sys.stderr)

        body = NOTE_TEMPLATE.format(
            note_id=note_id, notebook_id=notebook_id, synced_at=now_iso(),
            title=title, content=content,
        )
        if dry_run:
            print(f"  [dry-run] would PULL note -> {rel}", file=sys.stderr)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body)
        manifest["notes"][note_id] = {"file": rel, "title": title,
                                      "hash": remote_hash, "pulled_at": now_iso()}
        changed += 1

    # Notes deleted in NotebookLM: flag (do not auto-delete vault files).
    for gone in set(manifest["notes"]) - seen_ids:
        rel = manifest["notes"][gone].get("file")
        print(f"  NOTE removed in NotebookLM (kept locally): {rel}", file=sys.stderr)

    return changed


def sha_of_note_file(path: Path) -> str | None:
    """Recompute the title+content hash of a pulled note file, ignoring frontmatter."""
    try:
        raw = path.read_text(errors="ignore")
    except OSError:
        return None
    body = re.sub(r"^---\n.*?\n---\n", "", raw, count=1, flags=re.DOTALL)
    m = re.match(r"\s*#\s*(.+?)\n(.*)$", body, flags=re.DOTALL)
    if m:
        title, content = m.group(1).strip(), m.group(2).strip()
        return sha(title + "\n" + content)
    return sha(body.strip())


# --------------------------------------------------------------------------- #
# PUSH: vault push/*.md -> NotebookLM sources   (vault is authoritative)
# --------------------------------------------------------------------------- #
def list_source_ids(notebook_id: str) -> set[str]:
    data = nlm_json(["source", "list", notebook_id])
    if data is None:
        return set()
    if isinstance(data, dict):
        data = data.get("sources") or data.get("items") or []
    return {str(pick(s, "id", "source_id", "sourceId", default="")).strip()
            for s in data if pick(s, "id", "source_id", "sourceId")}


def push_sources(notebook_id: str, folder: Path, manifest: dict,
                 dry_run: bool, prune: bool) -> int:
    push_dir = folder / "push"
    push_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in push_dir.rglob("*") if p.suffix.lower() in PUSH_EXTS)
    changed = 0
    seen_rel: set[str] = set()

    for path in files:
        rel = str(path.relative_to(folder))
        seen_rel.add(rel)
        content_hash = sha(path.read_text(errors="ignore"))
        record = manifest["sources"].get(rel, {})

        if record.get("hash") == content_hash and record.get("source_id"):
            continue  # unchanged

        # Changed/new: delete the stale source first (sources are not editable).
        old_id = record.get("source_id")
        if old_id:
            if dry_run:
                print(f"  [dry-run] would DELETE stale source {old_id} for {rel}", file=sys.stderr)
            else:
                nlm(["source", "delete", old_id, "--confirm"], check=False)

        if dry_run:
            print(f"  [dry-run] would PUSH {rel} -> new source", file=sys.stderr)
            manifest["sources"][rel] = {"source_id": old_id or "DRYRUN",
                                        "hash": content_hash, "pushed_at": now_iso()}
            changed += 1
            continue

        # Capture the new source id by diffing the source list before/after add.
        before = list_source_ids(notebook_id)
        proc = nlm(["source", "add", notebook_id, "--file", str(path), "--wait"], check=False)
        if proc.returncode != 0:
            print(f"  ERROR adding {rel}: {proc.stderr.strip()[:200]}", file=sys.stderr)
            continue
        after = list_source_ids(notebook_id)
        new_ids = after - before
        new_id = next(iter(new_ids)) if new_ids else None
        manifest["sources"][rel] = {"source_id": new_id, "hash": content_hash,
                                    "pushed_at": now_iso()}
        print(f"  PUSHED {rel} -> source {new_id or '(id unknown)'}", file=sys.stderr)
        changed += 1

    # Files removed locally: optionally prune the remote source.
    for gone_rel in set(manifest["sources"]) - seen_rel:
        rec = manifest["sources"][gone_rel]
        sid = rec.get("source_id")
        if prune and sid:
            if dry_run:
                print(f"  [dry-run] would PRUNE remote source {sid} ({gone_rel})", file=sys.stderr)
            else:
                nlm(["source", "delete", sid, "--confirm"], check=False)
                print(f"  PRUNED remote source {sid} ({gone_rel})", file=sys.stderr)
            del manifest["sources"][gone_rel]
        else:
            print(f"  local file removed (remote source kept; use --prune to delete): "
                  f"{gone_rel}", file=sys.stderr)

    return changed


# --------------------------------------------------------------------------- #
# probe: dump raw JSON shapes so field mappings can be confirmed against a live notebook
# --------------------------------------------------------------------------- #
def probe(notebook_id: str) -> int:
    for label, args in [("NOTEBOOK", ["notebook", "get", notebook_id]),
                        ("NOTES", ["note", "list", notebook_id]),
                        ("SOURCES", ["source", "list", notebook_id])]:
        print(f"\n===== {label} (raw --json) =====")
        print(json.dumps(nlm_json(args), indent=2)[:4000])
    return 0


# --------------------------------------------------------------------------- #
def resolve_notebook_title(notebook_id: str) -> str:
    data = nlm_json(["notebook", "get", notebook_id])
    if isinstance(data, dict):
        return pick(data, "title", "name", default=notebook_id)
    return notebook_id


def run(notebook_id: str, *, dry_run: bool, pull_only: bool,
        push_only: bool, prune: bool, do_probe: bool) -> int:
    if not check_auth():
        print("nlm is not authenticated (sessions last ~20 min).\n"
              "  Run:  nlm login   (opens a Chromium-family browser)\n"
              "  Then re-run this sync.", file=sys.stderr)
        return 2

    if do_probe:
        return probe(notebook_id)

    title = resolve_notebook_title(notebook_id)
    folder = NOTEBOOKLM_ROOT / slugify(title)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(folder)
    manifest["notebook_id"] = notebook_id
    manifest["notebook_title"] = title

    print(f"=== NotebookLM sync: {title} ({notebook_id}) ===", file=sys.stderr)
    print(f"Folder: {folder}{'  [DRY RUN]' if dry_run else ''}", file=sys.stderr)

    pulled = pushed = 0
    if not push_only:
        print("-- PULL notes (notebook -> vault) --", file=sys.stderr)
        pulled = pull_notes(notebook_id, folder, manifest, dry_run)
    if not pull_only:
        print("-- PUSH sources (vault -> notebook) --", file=sys.stderr)
        pushed = push_sources(notebook_id, folder, manifest, dry_run, prune)

    save_manifest(folder, manifest, dry_run)
    print(f"\n=== done: {pulled} note(s) pulled, {pushed} source(s) pushed ===",
          file=sys.stderr)
    print(json.dumps({
        "notebook_id": notebook_id, "notebook_title": title,
        "folder": str(folder.relative_to(VAULT_PATH)),
        "notes_pulled": pulled, "sources_pushed": pushed, "dry_run": dry_run,
    }, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--notebook", required=True, help="NotebookLM notebook id or nlm alias")
    ap.add_argument("--dry-run", action="store_true", help="show planned actions, change nothing")
    ap.add_argument("--pull-only", action="store_true", help="only pull notes down")
    ap.add_argument("--push-only", action="store_true", help="only push sources up")
    ap.add_argument("--prune", action="store_true",
                    help="delete remote sources whose local push/ file was removed")
    ap.add_argument("--probe", action="store_true",
                    help="dump raw nlm --json shapes (for verifying field mappings)")
    a = ap.parse_args()
    return run(a.notebook, dry_run=a.dry_run, pull_only=a.pull_only,
               push_only=a.push_only, prune=a.prune, do_probe=a.probe)


if __name__ == "__main__":
    sys.exit(main())
