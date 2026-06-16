#!/usr/bin/env python3
"""Per-vault ingest index — track which raw/ sources have been ingested, by content hash.

Ledger (per vault, travels with it): <vault>/meta/ingest_index.json
  { "sources": { "<raw relpath>": {"hash": ..., "ingested_at": ..., "source_page": ...} } }

A raw source is PENDING when its current content hash is absent from the ledger (new) or
differs from the recorded hash (changed since last ingest → re-ingest). This makes
`/obsidian-ingest` idempotent and lets it batch exactly the not-yet-ingested files.

CLI (resolves the active vault via VAULT / default_vault):
  python -m agents.ingest_index pending [--json]      # raw files needing ingest (one path/line)
  python -m agents.ingest_index status                 # counts: ingested / pending / total
  python -m agents.ingest_index mark <raw-path> [--source-page <vault-relpath>]
  python -m agents.ingest_index hash <raw-path>        # content hash of one file
  python -m agents.ingest_index list                   # everything recorded as ingested
  ... add --vault <name> to target a specific vault.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from agents import vault_config as vc
except ImportError:  # run as a script: agents/ is on sys.path
    import vault_config as vc

# Source-like extensions ingested into the vault (mirrors /obsidian-ingest's handlers).
SCAN_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".csv", ".epub",
             ".mp3", ".m4a", ".wav", ".ogg", ".webm", ".mp4",
             ".png", ".jpg", ".jpeg", ".gif", ".webp"}
# raw/ subdirs that hold attachments, not standalone sources.
SKIP_DIRS = {"assets"}
LEDGER_REL = Path("meta") / "ingest_index.json"


def _vault(name: str | None) -> Path:
    return vc.vault_path(name)


def _ledger_path(name: str | None) -> Path:
    return _vault(name) / LEDGER_REL


def _load(name: str | None) -> dict:
    p = _ledger_path(name)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {"sources": {}}


def _save(name: str | None, data: dict) -> None:
    p = _ledger_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _raw_files(name: str | None) -> list[Path]:
    root = _vault(name) / "raw"
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in SCAN_EXTS:
            continue
        rel_parts = p.relative_to(root).parts
        if rel_parts and rel_parts[0] in SKIP_DIRS:
            continue
        out.append(p)
    return sorted(out)


def pending(name: str | None = None) -> list[Path]:
    """Raw files that are new or changed since their recorded ingest."""
    led = _load(name).get("sources", {})
    vault = _vault(name)
    out = []
    for p in _raw_files(name):
        rel = str(p.relative_to(vault))
        rec = led.get(rel)
        if rec is None or rec.get("hash") != file_hash(p):
            out.append(p)
    return out


def mark(raw_path: str, source_page: str | None = None, name: str | None = None) -> None:
    vault = _vault(name)
    p = Path(raw_path)
    if not p.is_absolute():
        p = vault / raw_path
    rel = str(p.resolve().relative_to(vault.resolve()))
    data = _load(name)
    data.setdefault("sources", {})[rel] = {
        "hash": file_hash(p),
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_page": source_page or "",
    }
    _save(name, data)


def _main(argv: list[str]) -> int:
    args = list(argv)
    name = None
    if "--vault" in args:
        i = args.index("--vault"); name = args[i + 1]; del args[i:i + 2]
    as_json = False
    if "--json" in args:
        as_json = True; args.remove("--json")
    source_page = None
    if "--source-page" in args:
        i = args.index("--source-page"); source_page = args[i + 1]; del args[i:i + 2]
    cmd = args[0] if args else "status"
    vault = _vault(name)

    if cmd == "pending":
        items = pending(name)
        rels = [str(p.relative_to(vault)) for p in items]
        print(json.dumps(rels, indent=2) if as_json else "\n".join(rels))
    elif cmd == "status":
        led = _load(name).get("sources", {})
        total = len(_raw_files(name))
        pend = len(pending(name))
        print(f"vault={vc.active_vault(name)} raw_sources={total} ingested={total - pend} pending={pend}")
    elif cmd == "mark":
        if len(args) < 2:
            print("usage: mark <raw-path> [--source-page <vault-relpath>]", file=sys.stderr)
            return 2
        mark(args[1], source_page, name)
        print(f"marked ingested: {args[1]}")
    elif cmd == "hash":
        if len(args) < 2:
            print("usage: hash <raw-path>", file=sys.stderr)
            return 2
        p = Path(args[1])
        print(file_hash(p if p.is_absolute() else vault / args[1]))
    elif cmd == "list":
        print(json.dumps(_load(name).get("sources", {}), indent=2, sort_keys=True))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
