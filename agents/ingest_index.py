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
ingest (re-ingest). `scan` also reconciles deletions: a source whose id is no longer present in
raw/ is marked `deleted` (the row is kept for history; rename-safe since renames keep the id).
A deleted source whose file later reappears flips back to `pending`. This makes /obsidian-ingest
idempotent, rename-proof, and deletion-aware.

CLI (resolves the active vault via VAULT / default_vault):
  python -m agents.ingest_index pending [--json]      # raw files needing ingest (one path/line)
  python -m agents.ingest_index status                 # counts: ingested / pending / deleted / total
  python -m agents.ingest_index scan                   # reconcile: register new + mark deleted
  python -m agents.ingest_index deleted                # list sources marked deleted
  python -m agents.ingest_index mark <raw-path> [--source-page <vault-relpath>]
  python -m agents.ingest_index id <raw-path>          # derived source id for one file
  python -m agents.ingest_index get [--id <id> | <raw-path>]
  python -m agents.ingest_index report                 # (re)write meta/ingest_index.md table
  python -m agents.ingest_index list                   # full JSON store
  ... add --vault <name> to target a specific vault.

v3 approval queue (web/research-discovered candidates; NO file on disk yet):
  python -m agents.ingest_index enqueue <id|url> [--title T] [--rationale R] \
        [--score F] [--discovered-by AGENT] [--objective-ids OID ...]
                                                       # add a waiting_approval candidate;
                                                       # NO-OP if already rejected (sticky);
                                                       # metadata-only if already present.
  python -m agents.ingest_index queue   (alias: waiting) # list waiting_approval rows
  python -m agents.ingest_index approve <id>             # waiting_approval -> pending
  python -m agents.ingest_index reject  <id> --reason T  # -> rejected (sticky)

Statuses: pending | ingested | deleted | waiting_approval | rejected. The scan() deletion
sweep SKIPS waiting_approval/rejected rows (they have no on-disk file by design).
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

SCHEMA_VERSION = 3
# Source-like extensions ingested into the vault (mirrors /obsidian-ingest's handlers).
SCAN_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".csv", ".epub",
             ".mp3", ".m4a", ".wav", ".ogg", ".webm", ".mp4",
             ".png", ".jpg", ".jpeg", ".gif", ".webp"}
SKIP_DIRS = {"assets"}                       # attachments, not standalone sources
LEDGER_REL = Path("meta") / "ingest_index.json"
MIRROR_REL = Path("meta") / "ingest_index.md"

# Status set (v3). waiting_approval + rejected rows have NO file on disk.
STATUSES = {"pending", "ingested", "deleted", "waiting_approval", "rejected"}
# Statuses the deletion-sweep MUST skip (they legitimately have no file in raw/).
NO_FILE_STATUSES = {"deleted", "waiting_approval", "rejected"}
# v3 fields added additively to every row (.get()-safe, never overwrite existing values).
_V3_DEFAULTS = {
    "relevance_score": None,
    "rationale": "",
    "discovered_by": "",
    "objective_ids": [],
    "proposed_at": None,
    "rejected_at": None,
    "rejection_reason": "",
}

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


def _migrate(data: dict) -> dict:
    """Additive v2 -> v3 migration. Adds the new per-row fields with `setdefault` so
    existing values are never rewritten and no row is lost. Status is left untouched
    (existing pending|ingested|deleted rows stay valid; the new statuses are only ever
    set by the v3 verbs). Idempotent: re-running on a v3 store is a no-op."""
    for row in data.get("sources", {}).values():
        if not isinstance(row, dict):
            continue
        for k, v in _V3_DEFAULTS.items():
            # copy mutable defaults so rows never share a list instance
            row.setdefault(k, list(v) if isinstance(v, list) else v)
    return data


def _load(name: str | None) -> dict:
    p = _vault(name) / LEDGER_REL
    if p.exists():
        try:
            data = json.loads(p.read_text())
            data.setdefault("sources", {})
            return _migrate(data)
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
        for k, v in _V3_DEFAULTS.items():
            row[k] = list(v) if isinstance(v, list) else v
        data["sources"][sid] = row
    else:
        row["filename"] = _rel(vault, p)          # rename-proof: same id, new path
        row["content_hash"] = info["content_hash"]
        if info["url"] and not row.get("url"):
            row["url"] = info["url"]
        if row.get("status") == "deleted":         # file came back → re-queue for ingest
            row["status"] = "pending"
            row.pop("deleted_at", None)
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
    """Reconcile the index against raw/ on disk:
    - register new sources (pending) and update filenames for renames;
    - mark sources whose id is no longer present on disk as `deleted` (kept for history).
    Rename-safe: a renamed file keeps its id, so it stays in the present set."""
    data = _load(name)
    vault = _vault(name)
    added = 0
    present_ids: set[str] = set()
    for p in _raw_files(name):
        before = len(data["sources"])
        row = _upsert(data, vault, p, ingested=False)
        present_ids.add(row["id"])
        added += len(data["sources"]) - before
    deleted = 0
    for sid, row in data["sources"].items():
        # v3 guard: waiting_approval/rejected rows intentionally have NO file on disk
        # (web/research candidates not yet fetched, or user-rejected). Sweeping them to
        # `deleted` would corrupt the approval queue, so skip them alongside `deleted`.
        if sid not in present_ids and row.get("status") not in NO_FILE_STATUSES:
            row["status"] = "deleted"
            row["deleted_at"] = _now()
            deleted += 1
    _save(name, data)
    render_md(name)
    return {"added": added, "deleted": deleted,
            "total": len(data["sources"]), "pending": len(pending(name))}


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


# --------------------------------------------------------------------------- #
# v3 approval-queue verbs (web/research-discovered candidates, no file on disk)
# --------------------------------------------------------------------------- #
def _normalize_enqueue_id(raw: str) -> tuple[str, str, str]:
    """Map a user-supplied id-or-url to a stable (id, id_type, url), mirroring derive_id.
    A bare arxiv/doi/youtube id or canonical-url is accepted directly; otherwise treated
    as a URL when it parses as one, else used verbatim as an explicit id."""
    raw = (raw or "").strip()
    if ":" in raw and raw.split(":", 1)[0].lower() in {
            "arxiv", "doi", "youtube", "url", "id", "sha256", "unknown"}:
        return raw, raw.split(":", 1)[0].lower(), ("" if not raw.startswith("url:") else raw[4:])
    if "://" in raw:
        return derive_id(url=raw)
    return f"id:{raw}", "explicit", ""


def enqueue(ident: str, *, title: str = "", rationale: str = "", score: float | None = None,
            discovered_by: str = "", objective_ids: list[str] | None = None,
            name: str | None = None) -> dict:
    """Add a `waiting_approval` candidate (no file on disk). Idempotent / status-safe:
    - if the id is already `rejected` -> NO-OP (sticky; never re-proposed);
    - if the id already exists in any other status -> update metadata only, NEVER regress
      the status (so an already-pending/ingested source is not knocked back to waiting);
    - otherwise create a fresh `waiting_approval` row.
    Returns the resulting row."""
    sid, id_type, url = _normalize_enqueue_id(ident)
    data = _load(name)
    row = data["sources"].get(sid)
    if row is not None and row.get("status") == "rejected":
        return row  # sticky no-op
    if row is None:
        row = {"id": sid, "id_type": id_type, "status": "waiting_approval",
               "title": title, "filename": "", "url": url,
               "source_type": id_type, "content_hash": "",
               "first_seen": _now(), "ingested_at": None, "source_page": ""}
        for k, v in _V3_DEFAULTS.items():
            row[k] = list(v) if isinstance(v, list) else v
        row["proposed_at"] = _now()
        data["sources"][sid] = row
    # metadata-only update (never touches status for an existing non-rejected row)
    if title:
        row["title"] = title
    if rationale:
        row["rationale"] = rationale
    if score is not None:
        row["relevance_score"] = score
    if discovered_by:
        row["discovered_by"] = discovered_by
    if objective_ids:
        merged = list(row.get("objective_ids") or [])
        for oid in objective_ids:
            if oid not in merged:
                merged.append(oid)
        row["objective_ids"] = merged
    if url and not row.get("url"):
        row["url"] = url
    _save(name, data)
    render_md(name)
    return row


def queue(name: str | None = None) -> list[dict]:
    """Rows awaiting approval (the newsletter / approval surface)."""
    data = _load(name)
    return sorted((r for r in data["sources"].values()
                   if r.get("status") == "waiting_approval"),
                  key=lambda r: (r.get("proposed_at") or "", r.get("id", "")))


def approve(ident: str, name: str | None = None) -> dict | None:
    """Flip a `waiting_approval` row -> `pending` (the wiki agent then fetches the source
    into raw/ and wiki-ingest consumes it). Returns the row, or None if id unknown."""
    sid, _, _ = _normalize_enqueue_id(ident)
    data = _load(name)
    row = data["sources"].get(sid) or data["sources"].get(ident)
    if row is None:
        return None
    if row.get("status") == "waiting_approval":
        row["status"] = "pending"
    _save(name, data)
    render_md(name)
    return row


def reject(ident: str, reason: str = "", name: str | None = None) -> dict | None:
    """Flip a row -> `rejected` (sticky) and record rejected_at + rejection_reason.
    A subsequent `enqueue` of the same id is a no-op. Returns the row, or None if unknown."""
    sid, _, _ = _normalize_enqueue_id(ident)
    data = _load(name)
    row = data["sources"].get(sid) or data["sources"].get(ident)
    if row is None:
        return None
    row["status"] = "rejected"
    row["rejected_at"] = _now()
    row["rejection_reason"] = reason or ""
    _save(name, data)
    render_md(name)
    return row


_STATUS_RANK = {"pending": 0, "waiting_approval": 1, "ingested": 2,
                "rejected": 3, "deleted": 4}
_STATUS_MARK = {"pending": "⏳ pending", "ingested": "✅ ingested", "deleted": "🗑️ deleted",
                "waiting_approval": "❓ waiting_approval", "rejected": "🚫 rejected"}


def render_md(name: str | None = None) -> Path:
    data = _load(name)
    rows = sorted(data["sources"].values(),
                  key=lambda r: (_STATUS_RANK.get(r.get("status"), 0), r.get("id", "")))
    ing = sum(1 for r in rows if r.get("status") == "ingested")
    pend = sum(1 for r in rows if r.get("status") == "pending")
    dele = sum(1 for r in rows if r.get("status") == "deleted")
    wait = sum(1 for r in rows if r.get("status") == "waiting_approval")
    rej = sum(1 for r in rows if r.get("status") == "rejected")
    L = [f"# Ingested sources — {data.get('vault', vc.active_vault(name))}\n",
         f"_Generated: {_now()}. Canonical store: `meta/ingest_index.json` (keyed by stable "
         f"source id, not filename). **{ing} ingested, {pend} pending, {wait} waiting_approval, "
         f"{rej} rejected, {dele} deleted**, {len(rows)} total._\n",
         "| Source ID | Status | Type | Title | File | URL | Wiki page |",
         "|-----------|--------|------|-------|------|-----|-----------|"]
    for r in rows:
        mk = _STATUS_MARK.get(r.get("status"), r.get("status", ""))
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
    # v3 enqueue/reject options
    opt_title = opt_rationale = opt_discovered = opt_reason = None
    opt_score = None
    opt_objectives: list[str] = []
    for flag in ("--title", "--rationale", "--discovered-by", "--score", "--reason"):
        if flag in args:
            i = args.index(flag); val = args[i + 1]; del args[i:i + 2]
            if flag == "--title": opt_title = val
            elif flag == "--rationale": opt_rationale = val
            elif flag == "--discovered-by": opt_discovered = val
            elif flag == "--reason": opt_reason = val
            elif flag == "--score":
                try: opt_score = float(val)
                except ValueError: opt_score = None
    while "--objective-ids" in args:
        i = args.index("--objective-ids"); opt_objectives.append(args[i + 1]); del args[i:i + 2]
    cmd = args[0] if args else "status"
    vault = _vault(name)

    if cmd == "pending":
        rels = [_rel(vault, p) for p in pending(name)]
        print(json.dumps(rels, indent=2) if as_json else "\n".join(rels))
    elif cmd == "status":
        data = _load(name); rows = list(data["sources"].values())
        ing = sum(1 for r in rows if r.get("status") == "ingested")
        dele = sum(1 for r in rows if r.get("status") == "deleted")
        wait = sum(1 for r in rows if r.get("status") == "waiting_approval")
        rej = sum(1 for r in rows if r.get("status") == "rejected")
        print(f"vault={vc.active_vault(name)} total={len(rows)} ingested={ing} "
              f"waiting_approval={wait} rejected={rej} deleted={dele} "
              f"| pending(needs ingest)={len(pending(name))}")
    elif cmd == "scan":
        r = scan(name)
        print(f"scanned: +{r['added']} new, {r['deleted']} newly-deleted, "
              f"{r['total']} total, {r['pending']} pending")
    elif cmd == "deleted":
        data = _load(name)
        for r in sorted(data["sources"].values(), key=lambda x: x.get("id", "")):
            if r.get("status") == "deleted":
                print(f"  {r['id']}\t{r.get('filename','')}\t(deleted {r.get('deleted_at','')})")
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
    elif cmd == "enqueue":
        if len(args) < 2:
            print("usage: enqueue <id|url> [--title --rationale --score "
                  "--discovered-by --objective-ids ...]", file=sys.stderr); return 2
        row = enqueue(args[1], title=opt_title or "", rationale=opt_rationale or "",
                      score=opt_score, discovered_by=opt_discovered or "",
                      objective_ids=opt_objectives, name=name)
        if as_json:
            print(json.dumps(row, indent=2, sort_keys=True))
        else:
            print(f"{row.get('status')}: {row.get('id')}")
    elif cmd in ("queue", "waiting"):
        rows = queue(name)
        if as_json:
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            for r in rows:
                sc = r.get("relevance_score")
                print(f"  {r['id']}\t{(r.get('title') or '')[:50]}\t"
                      f"score={sc if sc is not None else '-'}\tby={r.get('discovered_by','')}")
    elif cmd == "approve":
        if len(args) < 2:
            print("usage: approve <id>", file=sys.stderr); return 2
        row = approve(args[1], name)
        if row is None:
            print(f"unknown id: {args[1]}", file=sys.stderr); return 1
        print(f"{row.get('status')}: {row.get('id')}")
    elif cmd == "reject":
        if len(args) < 2:
            print("usage: reject <id> --reason <text>", file=sys.stderr); return 2
        row = reject(args[1], opt_reason or "", name)
        if row is None:
            print(f"unknown id: {args[1]}", file=sys.stderr); return 1
        print(f"{row.get('status')}: {row.get('id')} ({row.get('rejection_reason','')})")
    elif cmd == "report":
        print(f"wrote {render_md(name)}")
    elif cmd == "list":
        print(json.dumps(_load(name)["sources"], indent=2, sort_keys=True))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
