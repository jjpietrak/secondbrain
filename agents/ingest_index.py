#!/usr/bin/env python3
"""Per-vault ingest index — tracks which sources are ingested vs pending.

Lives in the vault (travels with it): canonical store `<vault>/meta/ingest_index.json`,
human-readable table `<vault>/meta/ingest_index.md`.

PRIMARY KEY is a STABLE source id — NOT the filename — so a renamed file is never mistaken
for a new source. Each row also keeps `filename` and `url`. ID scheme (precedence):
explicit frontmatter id > arXiv > DOI > YouTube > canonical URL > content hash. Examples:
`arxiv:2401.12345`, `doi:10.1145/3600006.3613165`, `youtube:dQw4w9WgXcQ`,
`url:https://example.com/post`, `sha256:1a2b3c…`. arXiv version suffixes (v1/v2) are stripped
for the key (same paper) but the file's content hash is still tracked for change detection.

A source is PENDING when its id is absent (new) or its content hash changed since the recorded
ingest (re-ingest). This makes /obsidian-ingest idempotent and rename-proof.

CLI (resolves the active vault via VAULT / default_vault):
  python -m agents.ingest_index pending [--json]      # raw files needing ingest (one path/line)
  python -m agents.ingest_index status                 # counts: ingested / pending / total
  python -m agents.ingest_index scan                   # register raw/ sources as pending (no ingest)
  python -m agents.ingest_index mark <raw-path> [--source-page <vault-relpath>]
  python -m agents.ingest_index id <raw-path>          # derived source id for one file
  python -m agents.ingest_index get [--id <id> | <raw-path>]
  python -m agents.ingest_index report                 # (re)write meta/ingest_index.md table
  python -m agents.ingest_index list                   # full JSON store
  ... add --vault <name> to target a specific vault.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

try:
    from agents import vault_config as vc
except ImportError:  # run as a script: agents/ is on sys.path
    import vault_config as vc

SCHEMA_VERSION = 2
# Source-like extensions ingested into the vault (mirrors /obsidian-ingest's handlers).
SCAN_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".csv", ".epub",
             ".mp3", ".m4a", ".wav", ".ogg", ".webm", ".mp4",
             ".png", ".jpg", ".jpeg", ".gif", ".webp"}
SKIP_DIRS = {"assets"}                       # attachments, not standalone sources
LEDGER_REL = Path("meta") / "ingest_index.json"
MIRROR_REL = Path("meta") / "ingest_index.md"

_ARXIV = re.compile(r"(?:arxiv[:/ ]?)?\b(\d{4}\.\d{4,5})(v\d+)?\b", re.IGNORECASE)
_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
_YOUTUBE = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})")
_FM_BLOCK = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_TEXTLIKE = {".md", ".markdown", ".txt", ".html", ".htm"}


# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vault(name: str | None) -> Path:
    return vc.vault_path(name)


def _load(name: str | None) -> dict:
    p = _vault(name) / LEDGER_REL
    if p.exists():
        try:
            data = json.loads(p.read_text())
            data.setdefault("sources", {})
            return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"version": SCHEMA_VERSION, "vault": vc.active_vault(name), "sources": {}}


def _save(name: str | None, data: dict) -> None:
    data["version"] = SCHEMA_VERSION
    data["vault"] = vc.active_vault(name)
    p = _vault(name) / LEDGER_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# id derivation
# --------------------------------------------------------------------------- #
def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _parse_frontmatter(text: str) -> dict:
    m = _FM_BLOCK.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            fm[k.strip().lower()] = v.strip().strip("\"'")
    return fm


def _canonical_url(url: str) -> str:
    try:
        u = urlparse(url.strip())
        if not u.scheme or not u.netloc:
            return url.strip()
        q = [(k, v) for k, v in parse_qsl(u.query)
             if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
        return urlunparse((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"),
                           "", urlencode(q), ""))
    except ValueError:
        return url.strip()


def derive_id(path: Path | None = None, url: str = "",
              frontmatter: dict | None = None, text: str = "") -> tuple[str, str, str]:
    """Return (id, id_type, url). Precedence: explicit fm id > arXiv > DOI > YouTube > url > hash."""
    fm = frontmatter or {}
    url = (url or fm.get("source_url") or fm.get("url") or "").strip()
    hay = "\n".join([str(path) if path else "", url, " ".join(fm.values()), text[:4000]])

    for k in ("id", "source_id", "arxiv_id", "arxiv", "doi"):
        if fm.get(k):
            v = fm[k].strip()
            if k in ("arxiv", "arxiv_id"):
                return f"arxiv:{re.sub(r'v[0-9]+$', '', v)}", "arxiv", url
            if k == "doi":
                return f"doi:{v.lower()}", "doi", url
            return (v if ":" in v else f"id:{v}"), "explicit", url

    m = _ARXIV.search(hay)
    if m:
        return f"arxiv:{m.group(1)}", "arxiv", (url or f"https://arxiv.org/abs/{m.group(1)}")
    m = _DOI.search(hay)
    if m:
        return f"doi:{m.group(1).lower()}", "doi", (url or f"https://doi.org/{m.group(1)}")
    m = _YOUTUBE.search(hay)
    if m:
        return f"youtube:{m.group(1)}", "youtube", (url or f"https://youtu.be/{m.group(1)}")
    if url:
        return f"url:{_canonical_url(url)}", "url", url
    if path:
        try:
            return file_hash(path), "hash", url
        except OSError:
            pass
    return f"unknown:{path.name if path else 'source'}", "unknown", url


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def inspect(path: Path) -> dict:
    """Derive id + metadata for a raw file."""
    text = path.read_text(errors="ignore") if path.suffix.lower() in _TEXTLIKE else ""
    fm = _parse_frontmatter(text)
    sid, id_type, url = derive_id(path=path, frontmatter=fm, text=text)
    title = fm.get("title") or _first_heading(text) or path.stem
    src_type = fm.get("source_type") or (path.parent.name.rstrip("s")) or path.suffix.lstrip(".")
    try:
        chash = file_hash(path)
    except OSError:
        chash = ""
    return {"id": sid, "id_type": id_type, "url": url, "title": title,
            "source_type": src_type, "content_hash": chash}


# --------------------------------------------------------------------------- #
def _raw_files(name: str | None) -> list[Path]:
    root = _vault(name) / "raw"
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in SCAN_EXTS:
            continue
        parts = p.relative_to(root).parts
        if parts and parts[0] in SKIP_DIRS:
            continue
        out.append(p)
    return sorted(out)


def _rel(vault: Path, p: Path) -> str:
    return str(p.resolve().relative_to(vault.resolve()))


def _upsert(data: dict, vault: Path, p: Path, *, ingested: bool,
            source_page: str | None = None) -> dict:
    """Insert/update a row keyed by source id. Renamed file (same id) → updates filename only."""
    info = inspect(p)
    sid = info["id"]
    row = data["sources"].get(sid)
    if row is None:
        row = {"id": sid, "id_type": info["id_type"], "status": "pending",
               "title": info["title"], "filename": _rel(vault, p), "url": info["url"],
               "source_type": info["source_type"], "content_hash": info["content_hash"],
               "first_seen": _now(), "ingested_at": None, "source_page": ""}
        data["sources"][sid] = row
    else:
        row["filename"] = _rel(vault, p)          # rename-proof: same id, new path
        row["content_hash"] = info["content_hash"]
        if info["url"] and not row.get("url"):
            row["url"] = info["url"]
    if ingested:
        row["status"] = "ingested"
        row["ingested_at"] = _now()
        if source_page:
            row["source_page"] = source_page
    return row


def pending(name: str | None = None) -> list[Path]:
    """Raw files that are new (id unseen) or changed (content hash differs). Deduped by id."""
    led = _load(name)["sources"]
    vault = _vault(name)
    out, seen_ids = [], set()
    for p in _raw_files(name):
        info = inspect(p)
        sid = info["id"]
        if sid in seen_ids:
            continue
        rec = led.get(sid)
        is_pending = rec is None or rec.get("status") != "ingested" \
            or rec.get("content_hash") != info["content_hash"]
        if is_pending:
            seen_ids.add(sid)
            out.append(p)
    return out


def scan(name: str | None = None) -> dict:
    """Register every raw/ source (as pending if new); update filenames for renames."""
    data = _load(name)
    vault = _vault(name)
    added = 0
    for p in _raw_files(name):
        before = len(data["sources"])
        _upsert(data, vault, p, ingested=False)
        added += len(data["sources"]) - before
    _save(name, data)
    render_md(name)
    pend = len(pending(name))
    return {"added": added, "total": len(data["sources"]), "pending": pend}


def mark(raw_path: str, source_page: str | None = None, name: str | None = None) -> str:
    vault = _vault(name)
    p = Path(raw_path)
    if not p.is_absolute():
        p = vault / raw_path
    data = _load(name)
    row = _upsert(data, vault, p, ingested=True, source_page=source_page)
    _save(name, data)
    render_md(name)
    return row["id"]


def render_md(name: str | None = None) -> Path:
    data = _load(name)
    rows = sorted(data["sources"].values(),
                  key=lambda r: (r.get("status") != "ingested", r.get("id", "")))
    ing = sum(1 for r in rows if r.get("status") == "ingested")
    pend = len(rows) - ing
    L = [f"# Ingested sources — {data.get('vault', vc.active_vault(name))}\n",
         f"_Generated: {_now()}. Canonical store: `meta/ingest_index.json` (keyed by stable "
         f"source id, not filename). **{ing} ingested, {pend} pending**, {len(rows)} total._\n",
         "| Source ID | Status | Type | Title | File | URL | Wiki page |",
         "|-----------|--------|------|-------|------|-----|-----------|"]
    for r in rows:
        mk = "✅ ingested" if r.get("status") == "ingested" else "⏳ pending"
        url = f"[link]({r['url']})" if r.get("url") else ""
        title = (r.get("title") or "")[:50].replace("|", "\\|")
        fn = (r.get("filename") or "").replace("|", "\\|")
        sp = (r.get("source_page") or "").replace("|", "\\|")
        L.append(f"| `{r.get('id','')}` | {mk} | {r.get('source_type','')} | {title} | {fn} | {url} | {sp} |")
    out = _vault(name) / MIRROR_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n")
    return out


# --------------------------------------------------------------------------- #
def _main(argv: list[str]) -> int:
    args = list(argv)
    name = None
    if "--vault" in args:
        i = args.index("--vault"); name = args[i + 1]; del args[i:i + 2]
    as_json = "--json" in args
    if as_json:
        args.remove("--json")
    source_page = None
    if "--source-page" in args:
        i = args.index("--source-page"); source_page = args[i + 1]; del args[i:i + 2]
    sid_opt = None
    if "--id" in args:
        i = args.index("--id"); sid_opt = args[i + 1]; del args[i:i + 2]
    cmd = args[0] if args else "status"
    vault = _vault(name)

    if cmd == "pending":
        rels = [_rel(vault, p) for p in pending(name)]
        print(json.dumps(rels, indent=2) if as_json else "\n".join(rels))
    elif cmd == "status":
        data = _load(name); rows = list(data["sources"].values())
        ing = sum(1 for r in rows if r.get("status") == "ingested")
        print(f"vault={vc.active_vault(name)} total={len(rows)} ingested={ing} pending={len(pending(name))}")
    elif cmd == "scan":
        r = scan(name)
        print(f"scanned: +{r['added']} new, {r['total']} total, {r['pending']} pending")
    elif cmd == "mark":
        if len(args) < 2:
            print("usage: mark <raw-path> [--source-page <vault-relpath>]", file=sys.stderr); return 2
        print(f"marked ingested: {mark(args[1], source_page, name)}")
    elif cmd == "id":
        if len(args) < 2:
            print("usage: id <raw-path>", file=sys.stderr); return 2
        p = Path(args[1]); p = p if p.is_absolute() else vault / args[1]
        print(inspect(p)["id"])
    elif cmd == "get":
        data = _load(name)
        if not sid_opt and len(args) >= 2:
            p = Path(args[1]); sid_opt = inspect(p if p.is_absolute() else vault / args[1])["id"]
        print(json.dumps(data["sources"].get(sid_opt or "", {}), indent=2))
    elif cmd == "report":
        print(f"wrote {render_md(name)}")
    elif cmd == "list":
        print(json.dumps(_load(name)["sources"], indent=2, sort_keys=True))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
