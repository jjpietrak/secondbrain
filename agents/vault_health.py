#!/usr/bin/env python3
"""
Vault health auditor for the Second Brain.

Checks wiki/ for:
- Orphaned pages (no inbound wikilinks)
- Dead wikilinks (links to non-existent pages)
- Missing required frontmatter (type, created, updated, sources)
- Stale pages (updated > 90 days ago)
- Empty / stub pages (< 50 chars of body)
- Frontmatter trapped in a leading ``` code fence (UNWRAP, do not add) [OSB]
- Unfilled Templater / template syntax left in a page [OSB]
- Probable duplicate pages (same normalized stem) [OSB]

Writes a dated report to meta/health_report/health-<date>.md (a FOLDER, so dated
reports accumulate). Pure Python - no LLM/API calls ($0).

Usage:
  python -m agents.vault_health              # active vault, write dated report
  python -m agents.vault_health --json       # emit machine-readable JSON to stdout
  python -m agents.vault_health --print-only # render report to stdout, no file write
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta

try:
    from agents import vault_config as vc
except ImportError:  # run as a script: agents/ is already on sys.path
    import vault_config as vc

# Structural / template files that are not subject to the page checks.
SKIP_STEMS = {"index", "log", "hot", "overview"}

LINK_RE = re.compile(r"\[\[([^\]|#]+)")
# Frontmatter accidentally saved inside a leading ``` code fence: the first
# non-blank content opens a fence and the real `---` frontmatter sits INSIDE it.
# The fix is to UNWRAP the fence, NOT to add a second frontmatter block (that would
# double-corrupt the page). Detected separately from genuinely-missing frontmatter.
CODE_FENCE_WRAP_RE = re.compile(r"\A\s*```[^\n]*\n\s*---\s*\n")
# Unfilled Templater syntax (<% ... %>) left behind in a real page.
TEMPLATE_RE = re.compile(r"<%.*?%>", re.DOTALL)


def is_skippable(page: Path) -> bool:
    return page.name.startswith("_") or page.stem in SKIP_STEMS


def _link_target(raw: str) -> str:
    """Normalize a wikilink target to the page stem it resolves to.

    Wikilinks in this vault are frequently path-qualified ([[sources/Foo]],
    [[concepts/Bar]]). Pages are keyed by bare stem, so resolve a link to its
    last path segment (and drop any leading/trailing whitespace). LINK_RE already
    strips a trailing |alias and #heading.
    """
    return raw.strip().rsplit("/", 1)[-1].strip()


def extract_wikilinks(content: str) -> list[str]:
    return [_link_target(m) for m in LINK_RE.findall(content)]


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


def _norm_stem(stem: str) -> str:
    """Normalize a page stem for duplicate detection: strip dates, lowercase,
    collapse non-alphanumerics to single spaces (matches OSB heuristic)."""
    norm = re.sub(r"\d{4}-\d{2}-\d{2}", "", stem).lower()
    norm = re.sub(r"[^a-z0-9 ]", " ", norm)
    return re.sub(r"\s+", " ", norm).strip()


def audit(vault: Path | None = None) -> tuple[dict, int]:
    vault = vault or vc.vault_path()
    wiki = vault / "wiki"
    empty_issues: dict = {
        "code_fence_wrapped": [], "missing_frontmatter": [], "unfilled_template": [],
        "duplicate": [], "orphaned": [], "dead_links": [], "stale": [], "empty": [],
    }
    if not wiki.exists():
        return (empty_issues, 0)

    pages = [p for p in wiki.rglob("*.md")]
    page_names = {p.stem for p in pages}
    backlinks: dict[str, list[str]] = {p.stem: [] for p in pages}
    all_links: dict[str, list[str]] = {}
    issues = {k: [] for k in empty_issues}
    cutoff = datetime.now() - timedelta(days=90)
    required = {"type", "created", "updated", "sources"}

    dup_stems: dict[str, list[str]] = {}

    for page in pages:
        if is_skippable(page):
            continue
        rel = str(page.relative_to(vault))
        content = page.read_text(errors="replace")

        # duplicate detection runs over ALL non-skippable pages
        dup_stems.setdefault(_norm_stem(page.stem), []).append(rel)

        # OSB check: frontmatter trapped in a leading code fence (unwrap, do not add).
        # Detected first and SHORT-CIRCUITS the missing-frontmatter check so we never
        # advise prepending a second frontmatter block onto a fence-wrapped page.
        fenced = bool(CODE_FENCE_WRAP_RE.match(content))
        if fenced:
            issues["code_fence_wrapped"].append(rel)

        # OSB check: unfilled Templater syntax left in a real page.
        if TEMPLATE_RE.search(content):
            issues["unfilled_template"].append(rel)

        fm = extract_frontmatter(content)
        body = content
        if content.startswith("---"):
            e = content.find("---", 3)
            if e > 0:
                body = content[e + 3:]
        if len(body.strip()) < 50:
            issues["empty"].append(rel)

        if not fenced:
            missing = required - set(fm.keys())
            if missing:
                issues["missing_frontmatter"].append(
                    f"{rel} - missing: {', '.join(sorted(missing))}")

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
            issues["orphaned"].append(str(page.relative_to(vault)))

    for stem, links in all_links.items():
        for link in links:
            if link not in page_names:
                issues["dead_links"].append(f"{stem} -> [[{link}]]")

    for norm, files in dup_stems.items():
        if norm and len(files) > 1:
            issues["duplicate"].append(f"{norm!r}: {', '.join(sorted(files))}")

    return issues, len([p for p in pages if not is_skippable(p)])


def render_report(issues: dict, total_pages: int) -> tuple[str, int]:
    now = datetime.now().isoformat(timespec="seconds")
    score = 100
    lines = ["# Vault health report\n", f"_Generated: {now}_\n",
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
    return "\n".join(lines) + "\n", score


def write_report(issues: dict, total_pages: int, vault: Path | None = None) -> int:
    vault = vault or vc.vault_path()
    body, score = render_report(issues, total_pages)
    report_dir = vault / "meta" / "health_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"health-{datetime.now():%Y-%m-%d}.md"
    report.write_text(body)
    print(f"[health] score={score}/100 pages={total_pages} report={report}")
    return score


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Second Brain vault health auditor")
    ap.add_argument("--vault", help="vault name (default: active vault)")
    ap.add_argument("--path", help="explicit vault path (overrides --vault)")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    ap.add_argument("--print-only", action="store_true",
                    help="render report to stdout, do not write a file")
    args = ap.parse_args(argv)

    vault = Path(args.path) if args.path else vc.vault_path(args.vault)
    issues, total = audit(vault)

    if args.json:
        out = {
            "vault": str(vault),
            "scanned": datetime.now().isoformat(timespec="seconds"),
            "total_pages": total,
            "counts": {k: len(v) for k, v in issues.items()},
            "issues": issues,
        }
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.print_only:
        body, _ = render_report(issues, total)
        sys.stdout.write(body)
        return 0

    write_report(issues, total, vault)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
