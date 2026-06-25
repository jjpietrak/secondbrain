#!/usr/bin/env python3
"""Deterministic wiki/index.md builder.

Scans wiki/** (skipping _template* files), reads each page's real Title
(frontmatter `title:` > first `# H1` > filename stem), its `type`, and
the ingest date (sourced from the vault's ingest_index.json by matching
`source_page`; fallback to frontmatter `created`/`date`; last resort is
file mtime). Emits a regenerated wiki/index.md with columns:
  Title | Type | Ingest date | Path (vault-relative)

Sections in the output:
  - Sources     (type: source)
  - Synthesis   (type: synthesis)
  - Concepts    (type: concept)   - subsorted A-Z
  - Entities    (type: entity)    - subsorted A-Z
  - Other       (anything unrecognised or missing type)

Uses the wiki-lock.sh Layer-2 lock on wiki/index.md before writing.

CLI:
  python scripts/wiki_index.py [--vault-root PATH] [--dry-run]
    --vault-root PATH   override vault root (default: resolved via agents.vault_config)
    --dry-run           print the generated index to stdout; do NOT write to disk

The script is called by wiki-ingest and wiki-init (index rebuild step).
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
# Resolve vault root
# ---------------------------------------------------------------------------

def _resolve_vault_root(override: str | None) -> Path:
    if override:
        return Path(override).resolve()
    # Prefer $VAULT_ROOT / $VAULT_PATH env vars (set by eval "$(vault_config env)")
    for ev in ("VAULT_ROOT", "VAULT_PATH"):
        if os.environ.get(ev):
            return Path(os.environ[ev])
    # Fall back to agents.vault_config
    code_root = Path(os.environ.get("CODE_PATH") or Path(__file__).resolve().parent.parent)
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
        sys.exit(f"wiki_index: cannot resolve vault root: {exc}")


# ---------------------------------------------------------------------------
# Frontmatter + title parsing
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)


def _parse_fm(text: str) -> dict[str, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            raw = v.strip()
            # strip surrounding quotes
            if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
                raw = raw[1:-1]
            fm[k.strip().lower()] = raw
    return fm


def _page_title(fm: dict[str, str], text: str, path: Path) -> str:
    if fm.get("title"):
        return fm["title"]
    m = _H1_RE.search(text)
    if m:
        return m.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


# ---------------------------------------------------------------------------
# Ingest date resolution
# ---------------------------------------------------------------------------

def _load_ingest_index(vault_root: Path) -> dict[str, dict]:
    """Return a mapping of vault-relative source_page -> row from ingest_index.json."""
    ledger = vault_root / "meta" / "ingest_index.json"
    if not ledger.exists():
        return {}
    try:
        data = json.loads(ledger.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for row in data.get("sources", {}).values():
        sp = (row.get("source_page") or "").strip()
        if sp:
            out[sp.lstrip("/")] = row
    return out


def _format_date(iso: str | None) -> str:
    """Return YYYY-MM-DD from an ISO datetime string, or empty string."""
    if not iso:
        return ""
    # ISO 8601: 2026-06-16T13:59:52+00:00 or 2026-06-16
    try:
        return iso[:10]
    except Exception:
        return ""


def _mtime_date(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except OSError:
        return ""


def _ingest_date(
    vault_rel: str,
    fm: dict[str, str],
    path: Path,
    ingest_map: dict[str, dict],
) -> str:
    """Priority: ingest_index.ingested_at > fm created/date > file mtime."""
    row = ingest_map.get(vault_rel)
    if row:
        d = _format_date(row.get("ingested_at") or row.get("first_seen"))
        if d:
            return d
    for key in ("created", "date"):
        v = fm.get(key, "").strip()
        if v:
            return v[:10]
    return _mtime_date(path)


# ---------------------------------------------------------------------------
# Scan wiki pages
# ---------------------------------------------------------------------------

def _scan_wiki(vault_root: Path, ingest_map: dict[str, dict]) -> list[dict]:
    """Return list of page-dicts sorted by title within each type group."""
    wiki_dir = vault_root / "wiki"
    if not wiki_dir.exists():
        return []

    pages: list[dict] = []
    for md in sorted(wiki_dir.rglob("*.md")):
        # skip _template files and hidden files
        if md.name.startswith("_") or md.name.startswith("."):
            continue
        # skip wiki/index.md itself (we are building it)
        if md.name == "index.md" and md.parent == wiki_dir:
            continue
        # skip wiki/hot.md and wiki/log.md (special shared caches)
        if md.parent == wiki_dir and md.name in ("hot.md", "log.md"):
            continue

        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fm = _parse_fm(text)
        vault_rel = str(md.relative_to(vault_root)).replace("\\", "/")
        title = _page_title(fm, text, md)
        page_type = fm.get("type", "").strip() or "untyped"
        date = _ingest_date(vault_rel, fm, md, ingest_map)

        pages.append({
            "title": title,
            "type": page_type,
            "date": date,
            "path": vault_rel,
            "stem": md.stem,
        })

    return pages


# ---------------------------------------------------------------------------
# Index rendering
# ---------------------------------------------------------------------------

_TYPE_ORDER = ["source", "synthesis", "concept", "entity"]


def _section_name(page_type: str) -> str:
    return {
        "source": "Sources",
        "synthesis": "Synthesis",
        "concept": "Concepts",
        "entity": "Entities",
    }.get(page_type, "Other")


def _render_index(pages: list[dict]) -> str:
    lines: list[str] = []
    lines.append("---")
    lines.append("type: index")
    today = datetime.date.today().isoformat()
    lines.append(f"updated: {today}")
    lines.append("sources: []")
    lines.append("ai-first: true")
    lines.append("---")
    lines.append("")
    lines.append("# Vault index")
    lines.append("")
    lines.append(
        "Master catalog of all wiki pages. Rebuilt automatically by `scripts/wiki_index.py`"
        " on every ingest."
    )
    lines.append("")

    # Group pages by type
    buckets: dict[str, list[dict]] = {t: [] for t in _TYPE_ORDER}
    buckets["other"] = []
    for p in pages:
        pt = p["type"]
        if pt in buckets:
            buckets[pt].append(p)
        else:
            buckets["other"].append(p)

    # Sort within each bucket by title A-Z (sources: keep insertion order which reflects
    # ingest chronology, but sort by date descending then title)
    buckets["source"].sort(key=lambda p: (p["date"] or "0000-00-00"), reverse=True)
    for t in ("synthesis", "concept", "entity", "other"):
        buckets[t].sort(key=lambda p: p["title"].lower())

    type_keys: list[str] = _TYPE_ORDER + ["other"]

    for tk in type_keys:
        group = buckets[tk]
        if not group:
            continue
        section = _section_name(tk) if tk != "other" else "Other"
        lines.append(f"## {section}")
        lines.append("")
        # Table header
        lines.append("| Title | Type | Ingest date | Path |")
        lines.append("|-------|------|-------------|------|")
        for p in group:
            # wikilink-style path reference using page stem (standard Obsidian convention)
            folder_stem = "/".join(p["path"].split("/")[1:-1])  # e.g. "concepts"
            stem = p["stem"]
            wl = f"[[{folder_stem}/{stem}]]" if folder_stem else f"[[{stem}]]"
            title_safe = p["title"].replace("|", "\\|")
            date_val = p["date"] or "-"
            path_val = p["path"]
            lines.append(f"| {title_safe} | {p['type']} | {date_val} | {wl} |")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Locked write
# ---------------------------------------------------------------------------

def _locked_write(vault_root: Path, content: str, dry_run: bool) -> None:
    index_path = vault_root / "wiki" / "index.md"
    index_rel = "wiki/index.md"

    if dry_run:
        print(content)
        return

    code_root = Path(os.environ.get("CODE_PATH") or Path(__file__).resolve().parent.parent)
    lock_script = code_root / "scripts" / "wiki-lock.sh"

    def _acquire() -> bool:
        if not lock_script.exists():
            return True  # no lock available; proceed anyway
        r = subprocess.run(
            ["bash", str(lock_script), "acquire", index_rel],
            cwd=str(vault_root),
            capture_output=True,
        )
        return r.returncode == 0

    def _release() -> None:
        if not lock_script.exists():
            return
        subprocess.run(
            ["bash", str(lock_script), "release", index_rel],
            cwd=str(vault_root),
            capture_output=True,
        )

    acquired = _acquire()
    if not acquired:
        import time
        time.sleep(2)
        acquired = _acquire()
    if not acquired:
        print(
            "wiki_index: wiki/index.md still held after retry -> skipping write",
            file=sys.stderr,
        )
        return

    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(content, encoding="utf-8")
    finally:
        _release()

    print(f"wiki_index: wrote {index_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def build_index(vault_root: Path, dry_run: bool = False) -> str:
    """Build and optionally write wiki/index.md. Returns the rendered content."""
    ingest_map = _load_ingest_index(vault_root)
    pages = _scan_wiki(vault_root, ingest_map)
    content = _render_index(pages)
    _locked_write(vault_root, content, dry_run)
    return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild wiki/index.md deterministically."
    )
    parser.add_argument(
        "--vault-root",
        metavar="PATH",
        default=None,
        help="Override vault root path (default: resolved via agents.vault_config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated index to stdout; do NOT write to disk.",
    )
    args = parser.parse_args(argv)

    vault_root = _resolve_vault_root(args.vault_root)
    build_index(vault_root, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
