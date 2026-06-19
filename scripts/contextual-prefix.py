#!/usr/bin/env python3
"""contextual-prefix.py — chunk wiki pages and generate per-chunk contextual prefixes.

Implements the ingest-side of Anthropic's Sept 2024 Contextual Retrieval pattern
(https://www.anthropic.com/news/contextual-retrieval). For each chunk of a wiki
page, generates a 1-2 sentence prefix situating the chunk in its source. The
prefixed text is what gets BM25-indexed and embedded, materially improving
retrieval accuracy (Anthropic measured 35-49% failure reduction).

Three-tier prefix generation (chosen per-run automatically):
  1. If ANTHROPIC_API_KEY is set      -> direct Anthropic API call (Haiku 4.5)
                                         with prompt caching on the page body
                                         (only when the body clears the ~16 KB
                                         Haiku 4.5 cache floor; see
                                         cache_control_for()).
                                         ~$12 / 1000 docs per Anthropic figures.
                                         REQUIRES --allow-egress (sends bodies off-machine).
  2. Elif `claude` binary on PATH     -> `claude -p` subprocess (uses CC subscription;
                                         no API key needed; slower per call).
                                         REQUIRES --allow-egress (subprocess egresses).
  3. Else (DEFAULT)                   -> synthetic prefix from page frontmatter +
                                         first paragraph (zero-cost floor; loses
                                         most of the contextual benefit but BM25
                                         and vector channels still work).

Data-egress posture (Second Brain default = LOCAL/$0):
  Tiers 1 and 2 send wiki page bodies off-machine. Both are GATED behind
  --allow-egress (default off). Without the flag, pick_prefix_tier() always
  returns "synthetic" regardless of env vars or claude binary presence. This is
  the contextual-prefix half of the $0/free-route discipline; egress is opt-in
  only, mirroring rerank.py's --allow-remote-ollama default-deny.

Second Brain adaptation vs the CO original: the ONLY change is vault-root
resolution (see _resolve_vault_root) so chunks are written under the ACTIVE
VAULT's .vault-meta/chunks/ rather than the code repo. The sandbox-test contract
(scripts copied into a tmp dir resolve their vault root to that tmp via the
__file__.parent.parent fallback) is preserved.

Chunk schema written to <vault>/.vault-meta/chunks/<page-address>/chunk-NNN.json:
{
  "schema_version": 1,
  "page_path": "wiki/concepts/Foo.md",
  "page_address": "c-000042",
  "chunk_index": 3,
  "raw_text": "...",
  "contextualized_text": "<prefix> <raw_text>",
  "prefix_source": "anthropic-api" | "claude-cli" | "synthetic" | "skipped",
  "char_count": 487,
  "body_hash": "sha256:...",     # of raw_text
  "page_body_hash": "sha256:...", # of the WHOLE source page (for invalidation)
  "created_at": "2026-05-17T..."
}

Pages without an `address:` frontmatter field are still chunked (using a
synthetic address derived from the path slug) so this tool works on vaults
without an address allocator enabled.

Usage:
  contextual-prefix.py PATH               # process a single page
  contextual-prefix.py --all              # process every wiki/*.md
  contextual-prefix.py PATH --no-llm      # force synthetic-prefix tier 3
  contextual-prefix.py PATH --rebuild     # ignore existing chunks
  contextual-prefix.py PATH --peek        # print what would happen; write nothing

Exit codes:
  0 — success
  2 — usage error
  3 — page file missing or unreadable
  4 — chunk dir creation failed
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _resolve_vault_root():
    """Resolve the active vault root (the .vault-meta and wiki/ live under it).

    Second Brain adaptation (the one deviation from the CO original). Precedence:
      1. $VAULT_META_ROOT  - explicit override / test hook (wins over everything)
      2. $VAULT_ROOT / $VAULT_PATH - exported by `agents.vault_config env`
      3. `agents.vault_config path` - resolve the active vault on demand, but ONLY
         when this script runs from its canonical repo location (so the hermetic
         sandbox tests fall through to the parent.parent fallback instead of
         pointing at the live vault)
      4. Path(__file__).resolve().parent.parent - the CO original's behavior.
    """
    env = os.environ.get("VAULT_META_ROOT") or os.environ.get("VAULT_ROOT") \
        or os.environ.get("VAULT_PATH")
    if env:
        return Path(env)
    script_dir = Path(__file__).resolve().parent
    try:
        from agents import vault_config as vc  # type: ignore
        if script_dir == (vc.CODE_PATH / "scripts").resolve():
            return vc.vault_path()
    except Exception:
        pass
    return script_dir.parent


VAULT_ROOT = _resolve_vault_root()
WIKI_DIR = VAULT_ROOT / "wiki"
META_DIR = VAULT_ROOT / ".vault-meta"
CHUNKS_DIR = META_DIR / "chunks"

CHUNK_TARGET_TOKENS = 500  # rough; we approximate via chars/4
CHUNK_TARGET_CHARS = CHUNK_TARGET_TOKENS * 4
CHUNK_OVERLAP_CHARS = 200

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_TIMEOUT_SEC = 30
CLAUDE_CLI_TIMEOUT_SEC = 60

# Anthropic prompt caching ignores any cached prefix below the model's minimum
# cacheable size — 4,096 tokens for Haiku 4.5. At ~4 chars/token that is ~16 KB.
# We attach cache_control only when the body clears this floor so the marker
# reflects reality: below the floor the API treats it as a silent no-op. The
# check counts the body only — a deliberately conservative ~370-char underestimate
# that ignores the system_msg + <page> wrapper also inside the cached prefix — so
# near the boundary it errs toward not-marking, never toward a wrongly-attached marker.
HAIKU_CACHE_MIN_CHARS = 16384  # 4096 tokens * 4 chars/token

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_PAGE_MISSING = 3
EXIT_CHUNK_DIR = 4

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
ADDRESS_RE = re.compile(r"^address:\s*(c-\d{6})\s*$", re.MULTILINE)
TITLE_RE = re.compile(r"^title:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)


def log(msg):
    print(msg, file=sys.stderr)


def sha256(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_page(path):
    if not path.is_file():
        raise SystemExit(EXIT_PAGE_MISSING)
    return path.read_text(encoding="utf-8", errors="replace")


def parse_frontmatter(body):
    m = FRONTMATTER_RE.match(body)
    if not m:
        return {}, body
    fm_text = m.group(1)
    rest = body[m.end():]
    addr_m = ADDRESS_RE.search(fm_text)
    title_m = TITLE_RE.search(fm_text)
    return {
        "address": addr_m.group(1) if addr_m else None,
        "title": title_m.group(1) if title_m else None,
        "raw": fm_text,
    }, rest


def derive_synthetic_address(page_path):
    """Stable per-path address-shaped string when no real address is set.
    Format: syn-NNNNNN derived from a hash of the relative path (deterministic).
    Used only for chunk filing.
    """
    rel = page_path.relative_to(VAULT_ROOT)
    h = hashlib.sha1(str(rel).encode("utf-8")).hexdigest()
    return "syn-" + h[:6]


def chunk_body(body, target_chars=CHUNK_TARGET_CHARS, overlap=CHUNK_OVERLAP_CHARS):
    """Split body into overlapping chunks on paragraph boundaries when possible.
    Heuristic: walk the body, accumulate paragraphs until len exceeds target,
    flush, then keep the trailing `overlap` chars as the seed of the next chunk.
    Empty paragraphs collapse to single boundaries.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks = []
    cur = []
    cur_len = 0
    for p in paragraphs:
        cur.append(p)
        cur_len += len(p) + 2
        if cur_len >= target_chars:
            chunk_text = "\n\n".join(cur)
            chunks.append(chunk_text)
            # seed next chunk with the tail
            tail = chunk_text[-overlap:] if overlap > 0 else ""
            cur = [tail] if tail else []
            cur_len = len(tail)
    if cur and "".join(cur).strip():
        chunks.append("\n\n".join(cur))
    if not chunks and body.strip():
        # tiny page — single chunk
        chunks = [body.strip()]
    return chunks


def synthetic_prefix(fm, body, chunk_text):
    """Tier-3 prefix: page title + first sentence of the page body.
    Free, hermetic, deterministic. Provides modest BM25 lift via title-word
    re-injection into the chunk corpus.
    """
    title = (fm.get("title") or "").strip() or "(untitled)"
    # First sentence of the body (not the chunk — gives the chunk a page-level frame)
    first_sentence = re.split(r"(?<=[.!?])\s+", body.strip(), maxsplit=1)
    first = first_sentence[0][:300] if first_sentence else ""
    return f"This passage is from the wiki page \"{title}\". The page opens: {first}"


def cache_control_for(page_body):
    """Ephemeral cache_control dict when the page body clears the Haiku cache
    floor, else None. Pure function so the floor decision is unit-testable
    without the network (the API call itself stays egress-gated).
    """
    if len(page_body) >= HAIKU_CACHE_MIN_CHARS:
        return {"type": "ephemeral"}
    return None


def anthropic_api_prefix(api_key, page_title, page_body, chunk_text):
    """Tier-1 prefix: direct Anthropic API call, Haiku, prompt-cached page body.

    The page body is the stable prefix shared by every chunk of a page, so it
    goes in `system` behind a cache breakpoint and the variable chunk goes in
    `messages`. Cache reads only land because chunks are processed sequentially
    (chunk 0 warms the prefix) — see the loop note in process_page().
    """
    system_msg = (
        "You are a retrieval-augmentation assistant. Given a wiki page and one "
        "chunk extracted from it, write a single short sentence (under 35 words) "
        "that situates the chunk within the page's scope and topic. Output only "
        "the sentence — no prefix, no quotation marks, no commentary."
    )
    page_block = {
        "type": "text",
        "text": f"<page title=\"{page_title}\">\n{page_body}\n</page>",
    }
    cc = cache_control_for(page_body)
    if cc:
        page_block["cache_control"] = cc
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 100,
        "system": [
            {"type": "text", "text": system_msg},
            page_block,
        ],
        "messages": [
            {
                "role": "user",
                "content": (
                    "Write the single contextualizing sentence for this chunk:\n\n"
                    f"<chunk>\n{chunk_text}\n</chunk>"
                ),
            }
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=ANTHROPIC_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # Cache telemetry: integer token counts only, never page content, so
            # the data-egress posture holds. Confirms whether the body cache is
            # actually firing given the Haiku floor (wrote>0 on chunk 0, read>0
            # on later chunks of the same page).
            usage = data.get("usage", {})
            log(f"  cache: wrote={usage.get('cache_creation_input_tokens', 0)} "
                f"read={usage.get('cache_read_input_tokens', 0)} tok")
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block["text"].strip().splitlines()[0]
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        log(f"  anthropic-api call failed: {e}")
        return None
    return None


def claude_cli_prefix(page_title, page_body, chunk_text):
    """Tier-2 prefix: `claude -p` subprocess (uses CC subscription, no API key)."""
    prompt = (
        f"Wiki page \"{page_title}\":\n\n"
        f"---\n{page_body[:4000]}\n---\n\n"
        f"Chunk:\n<chunk>\n{chunk_text}\n</chunk>\n\n"
        "Write one short sentence (under 35 words) situating this chunk within "
        "the page's scope. Output only the sentence."
    )
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=CLAUDE_CLI_TIMEOUT_SEC,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
        log(f"  claude-cli rc={result.returncode}: {result.stderr.strip()[:200]}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"  claude-cli call failed: {e}")
    return None


def pick_prefix_tier(force_synthetic, allow_egress=False):
    """Choose the contextual-prefix generation tier.

    Without allow_egress=True, ALWAYS returns "synthetic" regardless of
    env vars or claude binary availability. This is the data-egress guard:
    tiers 1 (Anthropic API) and 2 (claude CLI subprocess) both send wiki page
    bodies off-machine, so they require explicit user consent via the
    --allow-egress flag at the CLI layer. Mirrors rerank.py's
    --allow-remote-ollama default-deny and the Second Brain $0/local default.
    """
    if force_synthetic or not allow_egress:
        return "synthetic"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic-api"
    if shutil.which("claude"):
        return "claude-cli"
    return "synthetic"


def generate_prefix(tier, fm, body, chunk_text):
    """Asymmetric fallback by design:
      - tier="anthropic-api" -> on failure, try claude-cli (subprocess,
        free) before synthetic. The API is the user's stated preference,
        and claude-cli is the closer-in-quality fallback.
      - tier="claude-cli"    -> on failure, go straight to synthetic. The
        user has either no API key or has not opted into one; climbing
        back to the API would silently spend money they did not authorize.
      - tier="synthetic"     -> always synthetic.
    """
    title = fm.get("title") or "(untitled)"
    if tier == "anthropic-api":
        result = anthropic_api_prefix(
            os.environ["ANTHROPIC_API_KEY"], title, body, chunk_text
        )
        if result:
            return result, "anthropic-api"
        if shutil.which("claude"):
            result = claude_cli_prefix(title, body, chunk_text)
            if result:
                return result, "claude-cli"
        return synthetic_prefix(fm, body, chunk_text), "synthetic"
    if tier == "claude-cli":
        result = claude_cli_prefix(title, body, chunk_text)
        if result:
            return result, "claude-cli"
        return synthetic_prefix(fm, body, chunk_text), "synthetic"
    return synthetic_prefix(fm, body, chunk_text), "synthetic"


def process_page(page_path, force_synthetic=False, rebuild=False, peek=False,
                 allow_egress=False, progress_label=""):
    body = read_page(page_path)
    fm, content = parse_frontmatter(body)
    address = fm.get("address") or derive_synthetic_address(page_path)
    page_body_hash = sha256(body)

    chunk_dir = CHUNKS_DIR / address
    if not peek:
        try:
            chunk_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log(f"ERR: cannot create chunk dir {chunk_dir}: {e}")
            raise SystemExit(EXIT_CHUNK_DIR)

    chunks = chunk_body(content)
    tier = pick_prefix_tier(force_synthetic, allow_egress=allow_egress)

    progress = (progress_label + " ") if progress_label else ""
    if not chunks:
        # Explicit WARN so the user notices empty-body pages (often
        # frontmatter-only stubs) rather than silently producing no index entries.
        log(f"{progress}WARN: {page_path.relative_to(VAULT_ROOT)} has no chunkable body content "
            f"(empty after frontmatter strip). Skipping; no chunks written.")
        return {"address": address, "written": [], "skipped": 0, "tier": tier}

    log(f"{progress}-> {page_path.relative_to(VAULT_ROOT)}  address={address}  chunks={len(chunks)}  tier={tier}")

    written = []
    skipped = 0
    # Keep this loop sequential. The tier-1 Anthropic path caches the page body;
    # a cache entry is only readable after the first response begins (Anthropic
    # prompt-caching concurrency rule), so chunk 0 warms the prefix and chunks
    # 1..N read it. Parallelizing here would silently zero every cache read.
    for idx, raw in enumerate(chunks):
        chunk_path = chunk_dir / f"chunk-{idx:03d}.json"
        body_hash = sha256(raw)

        if chunk_path.exists() and not rebuild:
            try:
                existing = json.loads(chunk_path.read_text(encoding="utf-8"))
                if existing.get("body_hash") == body_hash and \
                   existing.get("page_body_hash") == page_body_hash:
                    skipped += 1
                    continue
            except (json.JSONDecodeError, OSError):
                pass  # corrupted; overwrite

        if peek:
            log(f"   would write {chunk_path.name} ({len(raw)} chars)")
            continue

        prefix, prefix_source = generate_prefix(tier, fm, content, raw)
        contextualized = f"{prefix}\n\n{raw}" if prefix else raw

        record = {
            "schema_version": 1,
            "page_path": str(page_path.relative_to(VAULT_ROOT)),
            "page_address": address,
            "chunk_index": idx,
            "raw_text": raw,
            "contextualized_text": contextualized,
            "prefix": prefix or "",
            "prefix_source": prefix_source,
            "char_count": len(raw),
            "body_hash": body_hash,
            "page_body_hash": page_body_hash,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        tmp = chunk_path.with_suffix(f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, chunk_path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        written.append(chunk_path.name)

    log(f"   wrote={len(written)}  skipped(unchanged)={skipped}")
    return {"address": address, "written": written, "skipped": skipped, "tier": tier}


def collect_pages(target):
    if target == "--all" or target is None:
        return sorted(p for p in WIKI_DIR.rglob("*.md")
                      if not any(part.startswith(".") for part in p.parts))
    p = Path(target)
    if not p.is_absolute():
        p = VAULT_ROOT / p
    return [p]


def main():
    parser = argparse.ArgumentParser(description="Chunk + contextualize wiki pages.")
    parser.add_argument("path", nargs="?",
                        help="Page path relative to vault root. Omit (or pass --all) "
                             "to process every wiki page.")
    parser.add_argument("--all", action="store_true",
                        help="Process every wiki page (equivalent to omitting path).")
    parser.add_argument("--no-llm", action="store_true",
                        help="Force tier-3 synthetic prefix (skip LLM calls).")
    parser.add_argument("--allow-egress", action="store_true",
                        help="Allow tier-1 (Anthropic API) or tier-2 (claude CLI "
                             "subprocess) prefix generation. Without this flag, page "
                             "bodies stay on-machine and only the tier-3 synthetic "
                             "prefix is used. Mirror of rerank.py's "
                             "--allow-remote-ollama guard.")
    parser.add_argument("--rebuild", action="store_true",
                        help="Re-process chunks even if body_hash matches.")
    parser.add_argument("--peek", action="store_true",
                        help="Print plan, write nothing.")
    args = parser.parse_args()

    if args.all and not args.path:
        args.path = "--all"
    elif not args.path:
        # No path and no --all: default to all (matches the help text)
        args.path = "--all"

    pages = collect_pages(args.path)
    # Explicit single-path invocations must point at a readable file inside the
    # vault. --all only ever yields in-vault files, so this guard is explicit-only.
    if args.path != "--all":
        target = pages[0].resolve()
        if not target.is_relative_to(VAULT_ROOT):
            log(f"ERR: {args.path} resolves outside the vault ({VAULT_ROOT}).")
            return EXIT_USAGE
        if not target.is_file():
            log(f"ERR: {args.path} is not a readable file.")
            return EXIT_PAGE_MISSING
    # Filter to actual files up front so the progress counter is meaningful.
    files = [p for p in pages if p.is_file()]
    skipped_non_files = len(pages) - len(files)
    if skipped_non_files:
        log(f"({skipped_non_files} non-file paths skipped)")
    total = len(files)
    total_written = 0
    total_skipped = 0
    for i, page in enumerate(files, 1):
        result = process_page(
            page,
            force_synthetic=args.no_llm,
            rebuild=args.rebuild,
            peek=args.peek,
            allow_egress=args.allow_egress,
            progress_label=f"[{i}/{total}]",
        )
        total_written += len(result["written"])
        total_skipped += result["skipped"]

    log(f"\nDone. pages={total}  chunks_written={total_written}  chunks_unchanged={total_skipped}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
