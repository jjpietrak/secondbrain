#!/usr/bin/env python3
"""retrieve.py — hybrid retrieval orchestrator for the Second Brain wiki.

Pipeline:
  query  ->  bm25-index.py query (top-K candidates by BM25 over contextualized chunks)
         ->  rerank.py        (cosine on nomic-embed-text vectors via ollama,
                              or no-op if ollama unavailable)
         ->  drill            (return chunk pages with absolute paths so the
                              caller can Read them and synthesize)

Loads sibling scripts as Python modules (no subprocess overhead). Falls back
gracefully when index or rerank stage is missing:
- If .vault-meta/bm25/index.json is absent     -> exit 10 with friendly message;
                                                  caller falls back to legacy
                                                  hot->index->drill read order.
- If .vault-meta/chunks/ is empty              -> exit 10 (same).
- If rerank stage cannot embed (no ollama)     -> no-op rerank, returns BM25 order.

Second Brain adaptation vs the CO original (two deviations, both noted inline):
  1. VAULT_ROOT (the .vault-meta location) resolves to the ACTIVE VAULT via
     _resolve_vault_root, not the code repo.
  2. SCRIPTS_DIR is decoupled from VAULT_ROOT: it is always the directory THIS
     script lives in, because in Second Brain the scripts live in the code repo
     while .vault-meta lives in the (separate) active vault. In the CO original
     and in the hermetic sandbox tests these two coincide, so behavior there is
     unchanged.

Output schema (JSON to stdout):
{
  "query": "...",
  "strategy": "bm25+rerank:cosine:nomic-embed-text" | "bm25+noop-rerank" | "bm25-only",
  "top_k": 5,
  "candidates": [
    {
      "chunk_id": "c-000042:3",
      "page_address": "c-000042",
      "page_path": "wiki/concepts/Foo.md",
      "absolute_path": "/abs/path/to/wiki/concepts/Foo.md",
      "chunk_index": 3,
      "bm25_score": 7.12,
      "rerank_score": 0.81,
      "rerank_source": "cosine:nomic-embed-text",
      "snippet": "... first 200 chars of the chunk ..."
    },
    ...
  ]
}

Usage:
  retrieve.py "your query here"           # standard: BM25 top-20, rerank to top-5
  retrieve.py "query" --top 10            # change result count
  retrieve.py "query" --no-rerank         # skip rerank, BM25-only
  retrieve.py "query" --explain           # include per-stage diagnostics

Exit codes:
  0 — success
  2 — usage error
  10 — feature not provisioned (no chunks or no BM25 index); caller falls back
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def _resolve_vault_root():
    """Resolve the active vault root (the .vault-meta location).

    Second Brain adaptation. Precedence:
      1. $VAULT_META_ROOT  - explicit override / test hook (wins over everything)
      2. $VAULT_ROOT / $VAULT_PATH - exported by `agents.vault_config env`
      3. `agents.vault_config path` - resolve the active vault on demand, but ONLY
         when this script runs from its canonical repo location (so the hermetic
         sandbox tests fall through to the parent.parent fallback)
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


# SCRIPTS_DIR is the directory THIS script lives in (always — sibling helpers
# travel with it). VAULT_ROOT is the active vault (where .vault-meta lives), which
# in Second Brain is a SEPARATE location from the code repo. In the CO original and
# in the sandbox tests they coincide.
SCRIPTS_DIR = Path(__file__).resolve().parent
VAULT_ROOT = _resolve_vault_root()
META_DIR = VAULT_ROOT / ".vault-meta"
CHUNKS_DIR = META_DIR / "chunks"
BM25_INDEX = META_DIR / "bm25" / "index.json"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_PROVISIONED = 10


def log(msg):
    print(msg, file=sys.stderr)


def import_sibling(name, filename):
    """Import a hyphenated sibling .py file as a Python module.

    Wrapped in try/except so a syntax error or missing dependency in a sibling
    helper produces a friendly diagnostic instead of a bare Python traceback at
    the user's first retrieve call.
    """
    target = SCRIPTS_DIR / filename
    if not target.is_file():
        log(f"ERR: sibling helper {filename} not found at {target}")
        log("  Run `bash scripts/setup-retrieve.sh --check` to verify the install.")
        sys.exit(EXIT_NOT_PROVISIONED)
    try:
        spec = importlib.util.spec_from_file_location(name, target)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (ImportError, SyntaxError, AttributeError) as e:
        log(f"ERR: failed to import sibling helper {filename}: {type(e).__name__}: {e}")
        log("  This likely means the helper script is corrupted or has a syntax error.")
        log("  Run `python3 scripts/<helper>.py --help` directly to see the underlying error.")
        log("  If it persists: re-clone the repo or check `git status` for local damage.")
        sys.exit(EXIT_NOT_PROVISIONED)


def chunk_snippet(chunk_data, max_chars=200):
    text = chunk_data.get("raw_text", "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def main():
    parser = argparse.ArgumentParser(description="Hybrid retrieval over the vault.")
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--top", type=int, default=5, help="Final result count (post-rerank)")
    parser.add_argument("--bm25-top", type=int, default=20,
                        help="Candidate count from BM25 (pre-rerank)")
    parser.add_argument("--no-rerank", action="store_true",
                        help="Skip the rerank stage; return BM25-only")
    parser.add_argument("--explain", action="store_true",
                        help="Include per-stage diagnostics in output")
    parser.add_argument("--allow-remote-ollama", action="store_true",
                        help="Forwarded to rerank.py")
    args = parser.parse_args()

    if not BM25_INDEX.is_file():
        log(f"ERR: no BM25 index at {BM25_INDEX}. Run `bash scripts/setup-retrieve.sh` "
            "to provision, or fall back to legacy hot->index->drill.")
        return EXIT_NOT_PROVISIONED
    if not CHUNKS_DIR.is_dir() or not any(CHUNKS_DIR.iterdir()):
        log(f"ERR: no chunks at {CHUNKS_DIR}. Run "
            "`python3 scripts/contextual-prefix.py --all` first.")
        return EXIT_NOT_PROVISIONED

    bm25 = import_sibling("bm25_index", "bm25-index.py")
    reranker = import_sibling("rerank", "rerank.py")

    bm25_hits = bm25.query(args.query, top_k=args.bm25_top)
    log(f"bm25: {len(bm25_hits)} hits")

    candidates = []
    for h in bm25_hits:
        chunk_path = VAULT_ROOT / h["path"]
        try:
            chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        candidates.append({
            "chunk_id": h["chunk_id"],
            "page_address": chunk.get("page_address"),
            "page_path": chunk.get("page_path"),
            "absolute_path": str((VAULT_ROOT / chunk.get("page_path", "")).resolve()),
            "chunk_index": chunk.get("chunk_index"),
            "bm25_score": h["score"],
            "path": h["path"],
            "snippet": chunk_snippet(chunk),
        })

    if args.no_rerank:
        final = candidates[:args.top]
        strategy = "bm25-only"
        for c in final:
            c["rerank_score"] = c["bm25_score"]
            c["rerank_source"] = "skipped"
    else:
        final = reranker.rerank(
            args.query, candidates, top_k=args.top,
            allow_remote=args.allow_remote_ollama,
        )
        # Derive strategy from first candidate's rerank_source
        first_src = (final[0].get("rerank_source") if final else "unknown")
        strategy = f"bm25+rerank:{first_src}"

    # Dedupe by page (we may have multiple chunks of the same page; collapse to best)
    by_page = {}
    for c in final:
        addr = c.get("page_address")
        if addr not in by_page or c.get("rerank_score", 0) > by_page[addr].get("rerank_score", 0):
            by_page[addr] = c
    deduped = list(by_page.values())
    deduped.sort(key=lambda c: c.get("rerank_score", 0), reverse=True)

    out = {
        "query": args.query,
        "strategy": strategy,
        "top_k": args.top,
        "candidates": deduped[:args.top],
    }
    if args.explain:
        out["explain"] = {
            "bm25_candidate_count": len(bm25_hits),
            "bm25_candidates": len(bm25_hits),
            "post_rerank_count": len(final),
            "deduped_count": len(deduped),
            "bm25_top_param": args.bm25_top,
        }

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
