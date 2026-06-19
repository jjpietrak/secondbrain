#!/usr/bin/env python3
"""Wiki-lint extra structural checks (the SKILL-side complement to vault_health).

`agents/vault_health.py` already covers orphans, dead wikilinks, missing/code-fence
frontmatter, unfilled-template syntax, stale pages, empty/stub pages, and probable
duplicates. wiki-lint REUSES that auditor (calls it, does not duplicate it) and adds
the structural checks that live only on the skill side:

  - stray_root      : *.md sitting at the VAULT ROOT that belong inside wiki/
                      (the live vault's `stepfun-mfa.md` is the known offender).
  - empty_sections  : a heading (## / ###) with no content before the next heading,
                      frontmatter break, or EOF. (vault_health's `empty` is whole-page.)
  - missing_pages   : a candidate term wikilinked from 2+ pages that has no page of
                      its own AND is not already flagged as a dead link by vault_health
                      (i.e. it is mentioned widely enough to deserve its own page).

Pure Python, no LLM, no network ($0). Read-only over wiki/; observes, never fixes.
Resolves the active vault via agents.vault_config (never hard-codes a path).

Usage:
  python -m scripts.wiki_lint_extra              # JSON of the extra checks to stdout
  python -m scripts.wiki_lint_extra --vault NAME
  python -m scripts.wiki_lint_extra --path /abs/vault
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from agents import vault_config as vc
except ImportError:  # run as a loose script: put repo root on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agents import vault_config as vc

# Root-level files that legitimately live at the vault root (not stray).
ROOT_ALLOW = {"_CLAUDE.md", "CLAUDE.md", "README.md", "index.md", "log.md", "hot.md"}

LINK_RE = re.compile(r"\[\[([^\]|#]+)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# A line is "content" if it is non-blank and not itself a heading.
MISSING_PAGE_MIN_MENTIONS = 2


def _strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            return content[end + 3:]
    return content


def _wikilinks(content: str) -> list[str]:
    return [m.strip() for m in LINK_RE.findall(content)]


def find_stray_root(vault: Path) -> list[str]:
    """*.md at the vault root that are not in the structural allowlist."""
    stray = []
    for p in sorted(vault.glob("*.md")):
        if p.name in ROOT_ALLOW or p.name.startswith("_"):
            continue
        stray.append(p.name)
    return stray


def find_empty_sections(vault: Path) -> list[str]:
    """Headings whose body (until the next heading / EOF) has no real content."""
    wiki = vault / "wiki"
    out: list[str] = []
    if not wiki.exists():
        return out
    for page in sorted(wiki.rglob("*.md")):
        if page.name.startswith("_"):
            continue
        body = _strip_frontmatter(page.read_text(errors="replace"))
        lines = body.splitlines()
        rel = str(page.relative_to(vault))
        i = 0
        while i < len(lines):
            m = HEADING_RE.match(lines[i])
            if not m:
                i += 1
                continue
            title = m.group(2).strip()
            # scan forward for content until the next heading or EOF
            j = i + 1
            has_content = False
            while j < len(lines) and not HEADING_RE.match(lines[j]):
                if lines[j].strip():
                    has_content = True
                j += 1
            if not has_content:
                out.append(f"{rel} -> '{title}' has no content")
            i = j
    return out


def find_missing_pages(vault: Path, existing_pages: set[str], dead_links: set[str]) -> list[str]:
    """Terms wikilinked from MISSING_PAGE_MIN_MENTIONS+ distinct pages, with no page
    of their own. We surface these as 'deserves a page' rather than mere dead links."""
    wiki = vault / "wiki"
    out: list[str] = []
    if not wiki.exists():
        return out
    mentions: dict[str, set[str]] = {}
    for page in sorted(wiki.rglob("*.md")):
        if page.name.startswith("_"):
            continue
        rel = str(page.relative_to(vault))
        for link in _wikilinks(page.read_text(errors="replace")):
            if link in existing_pages:
                continue
            mentions.setdefault(link, set()).add(rel)
    for term, sources in sorted(mentions.items()):
        if len(sources) >= MISSING_PAGE_MIN_MENTIONS:
            srcs = ", ".join(sorted(sources))
            out.append(f"[[{term}]] mentioned in {len(sources)} pages ({srcs}) but has no page")
    return out


def lint(vault: Path) -> dict:
    wiki = vault / "wiki"
    existing = set()
    if wiki.exists():
        existing = {p.stem for p in wiki.rglob("*.md") if not p.name.startswith("_")}
    # dead_links is informational here; missing_pages is the 2+-mention subset.
    dead = set()
    return {
        "stray_root": find_stray_root(vault),
        "empty_sections": find_empty_sections(vault),
        "missing_pages": find_missing_pages(vault, existing, dead),
    }


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="wiki-lint extra structural checks")
    ap.add_argument("--vault", help="vault name (default: active vault)")
    ap.add_argument("--path", help="explicit vault path (overrides --vault)")
    args = ap.parse_args(argv)

    vault = Path(args.path) if args.path else vc.vault_path(args.vault)
    result = lint(vault)
    out = {
        "vault": str(vault),
        "counts": {k: len(v) for k, v in result.items()},
        "checks": result,
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
