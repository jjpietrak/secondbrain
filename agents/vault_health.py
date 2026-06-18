#!/usr/bin/env python3
"""
Vault health auditor for the Second Brain.

Checks wiki/ for:
- Orphaned pages (no inbound wikilinks)
- Dead wikilinks (links to non-existent pages)
- Missing required frontmatter (type, created, updated, sources)
- Stale pages (updated > 90 days ago)
- Empty / stub pages (< 50 chars of body)

Writes a health report to meta/health_report.md. Pure Python — no LLM/API calls.
"""
from __future__ import annotations
import os, re
from pathlib import Path
from datetime import datetime, timedelta

try:
    from agents import vault_config as vc
except ImportError:  # run as a script: agents/ is already on sys.path
    import vault_config as vc

VAULT = vc.vault_path()           # active vault ($VAULT or default_vault)
WIKI = VAULT / "wiki"
REPORT = VAULT / "meta" / "health_report.md"

# Structural / template files that are not subject to the page checks.
SKIP_STEMS = {"index", "log", "hot", "overview"}

LINK_RE = re.compile(r"\[\[([^\]|#]+)")


def is_skippable(page: Path) -> bool:
    return page.name.startswith("_") or page.stem in SKIP_STEMS


def extract_wikilinks(content: str) -> list[str]:
    return [m.strip() for m in LINK_RE.findall(content)]


def extract_frontmatter(content: str) -> dict:
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            try:
                import yaml
                return yaml.safe_load(content[3:end]) or {}
            except Exception:
                return {}
    return {}


def audit() -> tuple[dict, int]:
    if not WIKI.exists():
        return ({"orphaned": [], "dead_links": [], "missing_frontmatter": [],
                 "stale": [], "empty": []}, 0)

    pages = [p for p in WIKI.rglob("*.md")]
    page_names = {p.stem for p in pages}
    backlinks: dict[str, list[str]] = {p.stem: [] for p in pages}
    all_links: dict[str, list[str]] = {}
    issues = {"orphaned": [], "dead_links": [], "missing_frontmatter": [],
              "stale": [], "empty": []}
    cutoff = datetime.now() - timedelta(days=90)
    required = {"type", "created", "updated", "sources"}

    for page in pages:
        if is_skippable(page):
            continue
        rel = str(page.relative_to(VAULT))
        content = page.read_text(errors="replace")
        body = content
        fm = extract_frontmatter(content)
        if content.startswith("---"):
            e = content.find("---", 3)
            if e > 0:
                body = content[e + 3:]
        if len(body.strip()) < 50:
            issues["empty"].append(rel)
        missing = required - set(fm.keys())
        if missing:
            issues["missing_frontmatter"].append(f"{rel} — missing: {', '.join(sorted(missing))}")
        upd = fm.get("updated")
        if isinstance(upd, str):
            try:
                if datetime.fromisoformat(upd) < cutoff:
                    issues["stale"].append(rel)
            except ValueError:
                pass
        links = extract_wikilinks(content)
        all_links[page.stem] = links
        for link in links:
            backlinks.setdefault(link, []).append(page.stem)

    for page in pages:
        if is_skippable(page):
            continue
        if not backlinks.get(page.stem):
            issues["orphaned"].append(str(page.relative_to(VAULT)))

    for stem, links in all_links.items():
        for link in links:
            if link not in page_names:
                issues["dead_links"].append(f"{stem} → [[{link}]]")

    return issues, len([p for p in pages if not is_skippable(p)])


def write_report(issues: dict, total_pages: int) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    score = 100
    lines = [f"# Vault health report\n", f"_Generated: {now}_\n",
             f"**Total wiki pages:** {total_pages}\n"]
    for key, items in issues.items():
        score -= len(items) * 2
        lines.append(f"\n## {key.replace('_', ' ').capitalize()} ({len(items)})")
        for item in items[:20]:
            lines.append(f"- {item}")
        if len(items) > 20:
            lines.append(f"- _...and {len(items) - 20} more_")
    score = max(0, score)
    lines.insert(1, f"**Health score:** {score}/100\n")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"[health] score={score}/100 pages={total_pages} report={REPORT}")
    return score


if __name__ == "__main__":
    issues, total = audit()
    write_report(issues, total)
