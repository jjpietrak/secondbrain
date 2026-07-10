#!/usr/bin/env python3
"""
Apply dead-link slug normalization across the Inference-Disagg vault.
Replaces arxiv-id alias wikilinks with correct source page slugs.
Only touches [[...]] wikilink syntax; leaves bare text, URLs, frontmatter, raw/ paths, and
PDF annotation links (those contain #) untouched.
"""

import re
import sys
from pathlib import Path

VAULT_ROOT = Path("/mnt/c/Obsidian/Inference-Disagg")
TARGET_DIRS = ["wiki", "objective", "research"]

# Each tuple: (old_wikilink_target, new_wikilink_target)
# These are the INNER content of [[ ... ]] — no brackets here.
REPLACEMENTS = [
    # arxiv-id aliases
    ("baidu-afd-challenges-2602.09721", "2602.09721v1"),
    ("wiki/sources/baidu-afd-challenges-2602.09721", "2602.09721v1"),
    ("megascale-infer", "2504.02263v4"),
    ("wiki/sources/megascale-infer", "2504.02263v4"),
    ("frontier-simulator-2508.03148", "2508.03148v1"),
    ("wiki/sources/frontier-simulator-2508.03148", "2508.03148v1"),
    ("llmservingsim-2-2602.23036", "2602.23036v2"),
    ("wiki/sources/llmservingsim-2-2602.23036", "2602.23036v2"),
    ("sarathi-2308.16369", "2308.16369v1"),
    ("wiki/sources/sarathi-2308.16369", "2308.16369v1"),
    ("zte-multivendor-pd-2509.17542", "2509.17542"),
    ("wiki/sources/zte-multivendor-pd-2509.17542", "2509.17542"),
    ("gtc-2026-inference-kingdom-expands", "gtc-2026-the-inference-kingdom-expands"),
    ("astra-sim-3", "astra-sim"),
    ("step-3-paper", "step-3"),
    ("splitwise-2311.18677", "2311.18677v2"),
    ("wiki/sources/splitwise-2311.18677", "2311.18677v2"),
    ("cronus-2509.17357", "2509.17357v1"),
    ("wiki/sources/cronus-2509.17357", "2509.17357v1"),
    ("asgar-et-al-2507.19635", "2507.19635v1"),
    ("asgar-et-al-2507-19635", "2507.19635v1"),
    ("mist-2504.09775", "2504.09775"),
    ("spad-2510.08544", "2510.08544"),
    ("Cerebras", "cerebras"),
    # .pdf suffix dead links
    ("2507.19427v1.pdf", "2507.19427v1"),
    ("2508.03148v1.pdf", "2508.03148v1"),
    ("2506.05508v1.pdf", "2506.05508v1"),
    ("2405.05465v2.pdf", "2405.05465v2"),
    ("2602.12029v1.pdf", "2602.12029v1"),
    ("2311.18677v2.pdf", "2311.18677v2"),
]

# Safety: never touch wikilinks that contain '#' (PDF annotation links like [[file.pdf#page=N]])
# We match [[...]] where the content does NOT contain '#'.
# Pattern captures the full wikilink including brackets.
WIKILINK_RE = re.compile(r'\[\[([^\[\]#]+)\]\]')


def apply_replacements(content: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    """
    Apply all wikilink replacements to content string.
    Returns (new_content, total_substitutions_made).
    Only replaces exact inner-content matches (no # in the link).
    Supports [[target]] and [[target|alias]] forms.
    """
    # Build a lookup for fast matching
    repl_map = {old: new for old, new in replacements}
    count = 0

    def replace_match(m: re.Match) -> str:
        nonlocal count
        inner = m.group(1)
        # Handle [[target|alias]] — only rewrite the target part
        if "|" in inner:
            target, alias = inner.split("|", 1)
            if target in repl_map:
                count += 1
                return f"[[{repl_map[target]}|{alias}]]"
            return m.group(0)
        else:
            if inner in repl_map:
                count += 1
                return f"[[{repl_map[inner]}]]"
            return m.group(0)

    new_content = WIKILINK_RE.sub(replace_match, content)
    return new_content, count


def process_vault():
    total_files_changed = 0
    total_replacements = 0
    changed_files = []

    for dir_name in TARGET_DIRS:
        dir_path = VAULT_ROOT / dir_name
        if not dir_path.exists():
            print(f"  [SKIP] {dir_path} does not exist", file=sys.stderr)
            continue

        md_files = list(dir_path.rglob("*.md"))
        dir_changed = 0
        dir_replacements = 0

        for md_file in sorted(md_files):
            try:
                original = md_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"  [ERROR] reading {md_file}: {e}", file=sys.stderr)
                continue

            new_content, count = apply_replacements(original, REPLACEMENTS)

            if count > 0:
                md_file.write_text(new_content, encoding="utf-8")
                dir_changed += 1
                dir_replacements += count
                changed_files.append((md_file, count))

        print(f"  {dir_name}/: {dir_changed} files changed, {dir_replacements} replacements")
        total_files_changed += dir_changed
        total_replacements += dir_replacements

    print()
    print(f"TOTAL: {total_files_changed} files changed, {total_replacements} replacements")
    if changed_files:
        print()
        print("Changed files:")
        for f, c in changed_files:
            rel = f.relative_to(VAULT_ROOT)
            print(f"  {rel}  ({c} replacement{'s' if c != 1 else ''})")


if __name__ == "__main__":
    print(f"Vault: {VAULT_ROOT}")
    print(f"Dirs: {TARGET_DIRS}")
    print()
    process_vault()
