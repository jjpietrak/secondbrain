#!/usr/bin/env python3
"""
Vault stats aggregator for the Second Brain.

Walks the active vault's wiki/, parses frontmatter from every page, and aggregates:
- counts by `type` (entity / concept / source / synthesis / ...)
- counts by `status` (seed / developing / mature / evergreen / ...)
- tag frequency (from `tags:` list/scalar and `#inline` tags)
- most-recently-edited pages (by `updated`, fallback `created`)

Writes a dated stats report to meta/health_report/stats-<date>.md (a FOLDER so dated
reports accumulate). Backend-owned per RBAC. Pure Python - no LLM/API calls ($0).
No PyYAML dependency (lightweight frontmatter parser).

Usage:
  python -m agents.vault_stats              # active vault, write dated report
  python -m agents.vault_stats --json       # emit machine-readable JSON to stdout
  python -m agents.vault_stats --print-only # render report to stdout, no file write
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from agents import vault_config as vc
except ImportError:  # run as a script: agents/ is already on sys.path
    import vault_config as vc

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
INLINE_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")

# Structural / template files excluded from the aggregate (mirror vault_health).
SKIP_STEMS = {"index", "log", "hot", "overview"}


def is_skippable(page: Path) -> bool:
    return page.name.startswith("_") or page.stem in SKIP_STEMS


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Lightweight YAML-subset frontmatter parser (no PyYAML).

    Handles `key: scalar` and block lists:
        tags:
          - a
          - b
    and inline flow lists: `tags: [a, b]`. Nested objects are ignored.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict[str, Any] = {}
    cur_key: str | None = None
    for raw in m.group(1).splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # continuation of a block list (indented "- item")
        stripped = line.strip()
        if line[:1] in (" ", "\t") and stripped.startswith("-") and cur_key:
            item = stripped[1:].strip().strip('"').strip("'")
            if item:
                # The opener line set fm[cur_key] = None (empty scalar). Promote it to a
                # list on the first block item.
                if not isinstance(fm.get(cur_key), list):
                    fm[cur_key] = []
                fm[cur_key].append(item)
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",")]
            fm[key] = [v for v in items if v]
            cur_key = key
        elif val == "":
            fm[key] = None
            cur_key = key  # may be a block list opener
        else:
            fm[key] = val.strip('"').strip("'")
            cur_key = key
    return fm


def _tags_of(fm: dict[str, Any], body: str) -> list[str]:
    tags: list[str] = []
    raw = fm.get("tags")
    if isinstance(raw, list):
        tags.extend(str(t) for t in raw if t)
    elif isinstance(raw, str) and raw:
        tags.extend(t.strip() for t in re.split(r"[,\s]+", raw) if t.strip())
    for m in INLINE_TAG_RE.findall(body):
        tags.append(m)
    return [t.lstrip("#") for t in tags]


def _edited_at(fm: dict[str, Any]) -> datetime | None:
    for key in ("updated", "created", "date"):
        v = fm.get(key)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v[:19]) if "T" in v else \
                    datetime.fromisoformat(v[:10])
            except ValueError:
                continue
    return None


def aggregate(vault: Path | None = None) -> dict[str, Any]:
    vault = vault or vc.vault_path()
    wiki = vault / "wiki"
    by_type: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    tag_freq: Counter[str] = Counter()
    edited: list[tuple[str, str]] = []  # (rel, iso-date)
    total = 0

    if wiki.exists():
        for page in wiki.rglob("*.md"):
            if is_skippable(page):
                continue
            rel = str(page.relative_to(vault))
            text = page.read_text(errors="replace")
            fm = parse_frontmatter(text)
            body = FRONTMATTER_RE.sub("", text, count=1)
            total += 1
            by_type[str(fm.get("type") or "untyped")] += 1
            by_status[str(fm.get("status") or "unset")] += 1
            for t in _tags_of(fm, body):
                tag_freq[t] += 1
            ed = _edited_at(fm)
            if ed:
                edited.append((rel, ed.date().isoformat()))

    edited.sort(key=lambda x: x[1], reverse=True)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "vault": str(vault),
        "total_pages": total,
        "by_type": dict(by_type.most_common()),
        "by_status": dict(by_status.most_common()),
        "tags": dict(tag_freq.most_common(25)),
        "most_recently_edited": edited[:15],
    }


def render_report(stats: dict[str, Any]) -> str:
    def fmt(d: dict[str, int]) -> list[str]:
        return [f"- {k}: {v}" for k, v in d.items()] or ["- (none)"]

    lines = ["# Vault stats\n", f"_Generated: {stats['generated_at']}_\n",
             f"**Total wiki pages:** {stats['total_pages']}\n",
             "\n## By type"]
    lines += fmt(stats["by_type"])
    lines.append("\n## By status")
    lines += fmt(stats["by_status"])
    lines.append("\n## Top tags")
    lines += fmt(stats["tags"])
    lines.append("\n## Most recently edited")
    if stats["most_recently_edited"]:
        for rel, d in stats["most_recently_edited"]:
            lines.append(f"- {d}  {rel}")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def write_report(stats: dict[str, Any], vault: Path | None = None) -> Path:
    vault = vault or vc.vault_path()
    report_dir = vault / "meta" / "health_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"stats-{datetime.now():%Y-%m-%d}.md"
    report.write_text(render_report(stats))
    print(f"[stats] pages={stats['total_pages']} types={len(stats['by_type'])} "
          f"report={report}")
    return report


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Second Brain vault stats aggregator")
    ap.add_argument("--vault", help="vault name (default: active vault)")
    ap.add_argument("--path", help="explicit vault path (overrides --vault)")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    ap.add_argument("--print-only", action="store_true",
                    help="render report to stdout, do not write a file")
    args = ap.parse_args(argv)

    vault = Path(args.path) if args.path else vc.vault_path(args.vault)
    stats = aggregate(vault)

    if args.json:
        json.dump(stats, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.print_only:
        sys.stdout.write(render_report(stats))
        return 0

    write_report(stats, vault)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
