#!/usr/bin/env python3
"""Clean a web page (or local HTML file) with defuddle-cli into raw/articles/.

Deterministic helper for the wiki-defuddle skill. Resolves the active vault, derives a safe
dated slug filename, runs `defuddle <url-or-file>`, prepends a minimal provenance frontmatter
header, and writes the result to `<vault>/raw/articles/<slug>-<YYYY-MM-DD>.md` (immutable raw -
never overwrites; appends a numeric suffix on clash). Prints the absolute output path on stdout.

The source URL/path is passed to defuddle as a SINGLE argv element (never interpolated into a
shell string), so a hostile URL cannot inject shell commands.

Exit codes:
  0  wrote the cleaned file (path on stdout)
  3  defuddle-cli not found on PATH (caller should fall back)
  4  defuddle ran but failed / produced no output

Usage:
  python -m scripts.wiki_ingest_defuddle <url|file.html> [--vault NAME] [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

try:
    from agents import vault_config as vc
except ImportError:  # pragma: no cover - allow running with agents/ on sys.path
    import vault_config as vc  # type: ignore


def slugify(source: str) -> str:
    """Last URL path segment (or filename stem) -> safe lowercase hyphen slug.

    Strips query/fragment, path separators, control chars, and leading dots/hyphens so the
    result can never traverse out of raw/articles/."""
    parsed = urlparse(source)
    if parsed.scheme and parsed.netloc:
        seg = parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.netloc
    else:
        seg = Path(source).stem
    seg = seg.lower()
    seg = re.sub(r"[^a-z0-9]+", "-", seg).strip("-.")
    seg = seg or "article"
    return seg[:80]


def _unique_path(base: Path) -> Path:
    """raw is immutable: never overwrite. On a clash append -1, -2, ... before the suffix."""
    if not base.exists():
        return base
    stem, suffix = base.stem, base.suffix
    n = 1
    while True:
        cand = base.with_name(f"{stem}-{n}{suffix}")
        if not cand.exists():
            return cand
        n += 1


def run_defuddle(source: str) -> str:
    """Run defuddle-cli on a URL or local file; return stdout markdown. Raises on failure."""
    exe = shutil.which("defuddle")
    if not exe:
        raise FileNotFoundError("defuddle-cli not found on PATH (npm i -g defuddle-cli)")
    proc = subprocess.run([exe, source], capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(
            f"defuddle failed (rc={proc.returncode}): {proc.stderr.strip()[:300]}"
        )
    return proc.stdout


def clean_to_raw(source: str, *, vault: str | None = None,
                 out_dir: Path | None = None) -> Path:
    """Clean `source` and write it to raw/articles/. Returns the output path."""
    body = run_defuddle(source)
    today = date.today().isoformat()
    slug = slugify(source)
    if out_dir is None:
        out_dir = vc.vault_path(vault) / "raw" / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = _unique_path(out_dir / f"{slug}-{today}.md")
    is_url = bool(urlparse(source).scheme and urlparse(source).netloc)
    header = (
        "---\n"
        f"source_url: {source if is_url else ''}\n"
        f"fetched: {today}\n"
        "source_type: article\n"
        "---\n\n"
    )
    out.write_text(header + body)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="URL or local .html file to clean")
    ap.add_argument("--vault", default=None, help="target vault name (default: active)")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="override output dir (mainly for tests)")
    a = ap.parse_args(argv)
    try:
        out = clean_to_raw(a.source, vault=a.vault, out_dir=a.out_dir)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 3
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 4
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
