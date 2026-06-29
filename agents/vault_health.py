#!/usr/bin/env python3
"""
Vault health auditor for the Second Brain.

Checks configurable areas (wiki/, objective/, research/) for:
- Orphaned pages (no inbound wikilinks)
- Dead wikilinks (links to non-existent pages)
- Missing required frontmatter (per-type, derived from area templates)
- Stale pages (updated > 90 days ago)
- Empty / stub pages (< 50 chars of body)
- Frontmatter trapped in a leading ``` code fence (UNWRAP, do not add) [OSB]
- Unfilled Templater / template syntax left in a page [OSB]
- Probable duplicate pages (same normalized stem) [OSB]

Cross-area link resolution: [[wiki/concepts/foo]] and [[foo]] both resolve to
the page stem "foo" (last-path-segment normalization), so an objective node
linking [[wiki/concepts/foo]] will NOT be flagged as a dead link when that
wiki page exists.

Writes a dated report to meta/health_report/health-<date>.md (a FOLDER, so dated
reports accumulate). Pure Python - no LLM/API calls ($0).

Usage:
  python -m agents.vault_health                    # active vault, all areas, write dated report
  python -m agents.vault_health --area wiki        # wiki-only (reproduces legacy behavior)
  python -m agents.vault_health --area objective   # objective/ only
  python -m agents.vault_health --area research    # research/ only
  python -m agents.vault_health --area all         # all areas (default)
  python -m agents.vault_health --json             # emit machine-readable JSON to stdout
  python -m agents.vault_health --print-only       # render report to stdout, no file write
  python -m agents.vault_health --vault <name>     # target a specific vault
  python -m agents.vault_health --path <path>      # explicit vault path
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

# Default areas to audit when none are specified.
DEFAULT_AREAS = ("wiki", "objective", "research")

# Wiki subtypes map to the same required set.
WIKI_REQUIRED = {"type", "created", "updated", "sources"}

# Per-type required frontmatter keyed by the `type` frontmatter value.
# Derived by reading the live templates (task specification).
# Keys marked optional (e.g. answer_ref, vault) are excluded.
# The fallback for unknown types is {"type", "created", "updated"}.
TYPE_REQUIRED: dict[str, set[str]] = {
    # --- wiki types (all subtypes share the same required set) ---
    "concept": WIKI_REQUIRED,
    "entity": WIKI_REQUIRED,
    "source": WIKI_REQUIRED,
    "synthesis": WIKI_REQUIRED,
    # --- objective types ---
    "purpose": {"type", "created", "updated", "status"},
    "topic": {"type", "id", "created", "updated", "status"},
    "research_question": {"type", "id", "created", "updated", "solved", "topic", "priority"},
    "direction": {"type", "id", "created", "updated", "status", "serves_question", "priority"},
    "research_question_proposal": {"type", "id", "created", "updated", "status"},
    "decision": {"type", "id", "created", "updated", "status", "scope"},
    "agent_todo": {"type", "id", "created", "updated", "status"},
    # --- research types ---
    # Q-NNNN files have type: research_report (snake_case); deep/_template has
    # type: research-report (hyphen).  Accept both spellings pointing to same set.
    "research_report": {"type", "id", "created", "updated", "status"},
    "research-report": {"type", "created", "updated", "status"},
    "query-result": {"type", "created", "updated"},
}

FALLBACK_REQUIRED = {"type", "created", "updated"}


def _required_for_type(fm_type: str | None) -> set[str]:
    """Return the required frontmatter key set for a given page type."""
    if not fm_type:
        return FALLBACK_REQUIRED
    return TYPE_REQUIRED.get(fm_type, FALLBACK_REQUIRED)


def is_skippable(page: Path) -> bool:
    return page.name.startswith("_") or page.stem in SKIP_STEMS


def _link_target(raw: str) -> str:
    """Normalize a wikilink target to the page stem it resolves to.

    Wikilinks in this vault are frequently path-qualified ([[sources/Foo]],
    [[wiki/concepts/Bar]], [[objective/topic/T-0001]]). Pages are keyed by bare
    stem, so resolve a link to its last path segment (and drop any leading/trailing
    whitespace). LINK_RE already strips a trailing |alias and #heading.
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


def _area_for(page: Path, vault: Path) -> str:
    """Return the top-level area name for a page (e.g. 'wiki', 'objective', 'research')."""
    try:
        parts = page.relative_to(vault).parts
        return parts[0] if parts else "unknown"
    except ValueError:
        return "unknown"


# Areas that use degree-based orphan detection (no inbound AND no outbound = isolated).
# The wiki area uses the legacy inbound-only definition (back-compat).
DEGREE_ORPHAN_AREAS: frozenset[str] = frozenset({"objective", "research"})

# Valid values for the written_by frontmatter key.
VALID_WRITTEN_BY: frozenset[str] = frozenset({"USER", "research", "wiki", "web", "backend"})

# Issue categories.
ISSUE_KEYS = (
    "code_fence_wrapped",
    "missing_frontmatter",
    "missing_written_by",
    "unfilled_template",
    "duplicate",
    "orphaned",
    "dead_links",
    "stale",
    "empty",
)


def audit(
    vault: Path | None = None,
    areas: tuple[str, ...] | list[str] = DEFAULT_AREAS,
) -> tuple[dict, int]:
    """Audit the given vault areas and return (issues_dict, total_non_skippable_pages).

    issues_dict keys are those in ISSUE_KEYS; each value is a list of strings.
    Each issue string is prefixed with '[<area>] ' so callers can filter per area.

    The page index (page_names + backlinks) is built ACROSS ALL scanned areas so
    cross-area wikilinks resolve correctly. A link [[wiki/concepts/foo]] from an
    objective page will not be flagged dead as long as the wiki page 'foo' exists.
    """
    vault = vault or vc.vault_path()
    empty_issues: dict = {k: [] for k in ISSUE_KEYS}

    # Collect all pages across all requested areas.
    all_pages: list[Path] = []
    for area in areas:
        area_dir = vault / area
        if area_dir.exists():
            all_pages.extend(area_dir.rglob("*.md"))

    if not all_pages:
        return (empty_issues, 0)

    # Build global page index keyed by stem (last path segment without extension).
    # This is what _link_target() resolves links to.
    page_names: set[str] = {p.stem for p in all_pages}
    backlinks: dict[str, list[str]] = {p.stem: [] for p in all_pages}
    all_links: dict[str, list[str]] = {}
    issues: dict[str, list[str]] = {k: [] for k in ISSUE_KEYS}
    cutoff = datetime.now() - timedelta(days=90)

    dup_stems: dict[str, list[str]] = {}

    for page in all_pages:
        if is_skippable(page):
            continue
        area = _area_for(page, vault)
        rel = str(page.relative_to(vault))
        prefix = f"[{area}] "
        content = page.read_text(errors="replace")

        # duplicate detection runs over ALL non-skippable pages (global, cross-area)
        dup_stems.setdefault(_norm_stem(page.stem), []).append(rel)

        # OSB check: frontmatter trapped in a leading code fence (unwrap, do not add).
        # Detected first and SHORT-CIRCUITS the missing-frontmatter check so we never
        # advise prepending a second frontmatter block onto a fence-wrapped page.
        fenced = bool(CODE_FENCE_WRAP_RE.match(content))
        if fenced:
            issues["code_fence_wrapped"].append(f"{prefix}{rel}")

        # OSB check: unfilled Templater syntax left in a real page.
        if TEMPLATE_RE.search(content):
            issues["unfilled_template"].append(f"{prefix}{rel}")

        fm = extract_frontmatter(content)
        body = content
        if content.startswith("---"):
            e = content.find("---", 3)
            if e > 0:
                body = content[e + 3:]
        if len(body.strip()) < 50:
            issues["empty"].append(f"{prefix}{rel}")

        if not fenced:
            fm_type = fm.get("type")
            required = _required_for_type(fm_type)
            missing = required - set(fm.keys())
            if missing:
                issues["missing_frontmatter"].append(
                    f"{prefix}{rel} - missing: {', '.join(sorted(missing))}"
                )

        # written_by provenance check (all areas, all non-skippable pages).
        written_by = fm.get("written_by")
        if written_by is None:
            issues["missing_written_by"].append(f"{prefix}{rel} - missing written_by")
        elif written_by not in VALID_WRITTEN_BY:
            issues["missing_written_by"].append(
                f"{prefix}{rel} - invalid written_by: {written_by!r}"
            )

        upd = fm.get("updated")
        if isinstance(upd, str):
            try:
                if datetime.fromisoformat(upd) < cutoff:
                    issues["stale"].append(f"{prefix}{rel}")
            except ValueError:
                pass
        links = extract_wikilinks(content)
        all_links[page.stem] = links
        for link in links:
            backlinks.setdefault(link, []).append(page.stem)

    for page in all_pages:
        if is_skippable(page):
            continue
        area = _area_for(page, vault)
        has_inbound = bool(backlinks.get(page.stem))
        has_outbound = bool(all_links.get(page.stem))
        if area in DEGREE_ORPHAN_AREAS:
            # Degree-based: flag only if truly isolated (no inbound AND no outbound).
            # DAG leaves (directions, decisions, proposals) with outbound links are fine.
            if not has_inbound and not has_outbound:
                issues["orphaned"].append(f"[{area}] {page.relative_to(vault)}")
        else:
            # Legacy inbound-only definition (wiki area + any unknown area).
            if not has_inbound:
                issues["orphaned"].append(f"[{area}] {page.relative_to(vault)}")

    for stem, links in all_links.items():
        for link in links:
            if link not in page_names:
                # Find the page's area from the stem. Since stems may collide across
                # areas (unlikely but possible), use the first match.
                page_area = "unknown"
                for p in all_pages:
                    if p.stem == stem and not is_skippable(p):
                        page_area = _area_for(p, vault)
                        break
                issues["dead_links"].append(f"[{page_area}] {stem} -> [[{link}]]")

    for norm, files in dup_stems.items():
        if norm and len(files) > 1:
            # Derive area(s) from the rel paths (first segment is the area).
            group_areas = {f.split("/")[0] for f in files}
            if len(group_areas) == 1:
                area_tag = f"[{next(iter(group_areas))}]"
            else:
                area_tag = "[cross-area]"
            issues["duplicate"].append(
                f"{area_tag} {norm!r}: {', '.join(sorted(files))}"
            )

    total = len([p for p in all_pages if not is_skippable(p)])
    return issues, total


def _issues_for_area(issues: dict, area: str | None) -> dict:
    """Filter issues dict to only those tagged with a given area prefix.

    Cross-area duplicates ([cross-area] tag) are included when the file list
    contains a file from the requested area.
    """
    if area is None:
        return issues
    prefix = f"[{area}]"
    cross = "[cross-area]"
    result: dict[str, list[str]] = {}
    for k, v in issues.items():
        filtered = []
        for i in v:
            if i.startswith(prefix):
                filtered.append(i)
            elif i.startswith(cross) and f"/{area}/" in i:
                # Cross-area duplicate that involves this area
                filtered.append(i)
        result[k] = filtered
    return result


def _area_breakdown(issues: dict, areas: tuple[str, ...] | list[str]) -> dict[str, dict[str, int]]:
    """Return per-area issue counts: {area: {issue_key: count, ..., total: N}}."""
    breakdown: dict[str, dict[str, int]] = {}
    for area in areas:
        counts = {k: len([i for i in v if i.startswith(f"[{area}]")])
                  for k, v in issues.items()}
        counts["total"] = sum(counts.values())
        breakdown[area] = counts
    return breakdown


def render_report(
    issues: dict,
    total_pages: int,
    areas: tuple[str, ...] | list[str] | None = None,
) -> tuple[str, int]:
    """Render the health report.

    If areas is provided, the report includes per-area sections and an overall summary.
    The score formula (100 - 2*issues) is an open ADR; the report leads with per-area
    counts so the score is clearly advisory.
    """
    now = datetime.now().isoformat(timespec="seconds")
    total_issues = sum(len(v) for v in issues.values())
    score = max(0, 100 - 2 * total_issues)

    effective_areas = list(areas) if areas else []
    multi_area = len(effective_areas) > 1

    lines = [
        "# Vault health report\n",
        f"_Generated: {now}_\n",
        f"**Total pages scanned:** {total_pages}\n",
        f"**Health score:** {score}/100 _(advisory -- score formula pending ADR; see per-area counts below)_\n",
    ]

    if multi_area:
        # Per-area summary table
        breakdown = _area_breakdown(issues, effective_areas)
        lines.append("\n## Per-area issue counts\n")
        header = "| Area | " + " | ".join(k.replace("_", " ") for k in ISSUE_KEYS) + " | Total |"
        sep = "|------|" + "---|" * (len(ISSUE_KEYS) + 1)
        lines.append(header)
        lines.append(sep)
        for area in effective_areas:
            counts = breakdown[area]
            row = f"| {area} | " + " | ".join(str(counts.get(k, 0)) for k in ISSUE_KEYS) + f" | {counts['total']} |"
            lines.append(row)
        lines.append("")

    # Per-area detail sections
    if multi_area:
        for area in effective_areas:
            area_issues = _issues_for_area(issues, area)
            area_total = sum(len(v) for v in area_issues.values())
            lines.append(f"\n## Area: {area} ({area_total} issues)\n")
            for key, items in area_issues.items():
                lines.append(f"\n### {key.replace('_', ' ').capitalize()} ({len(items)})")
                for item in items[:20]:
                    lines.append(f"- {item}")
                if len(items) > 20:
                    lines.append(f"- _...and {len(items) - 20} more_")
    else:
        # Single-area or no-area: flat layout (legacy style for --area wiki)
        for key, items in issues.items():
            lines.append(f"\n## {key.replace('_', ' ').capitalize()} ({len(items)})")
            for item in items[:20]:
                lines.append(f"- {item}")
            if len(items) > 20:
                lines.append(f"- _...and {len(items) - 20} more_")

    # Overall summary
    lines.append(f"\n## Overall summary\n")
    for key, items in issues.items():
        lines.append(f"- **{key.replace('_', ' ')}**: {len(items)}")
    lines.append(f"\n_Score formula: 100 - 2*total_issues, floors at 0. Formula is advisory_")
    lines.append(f"_pending an ADR on dead-link weighting and per-category caps._\n")

    return "\n".join(lines) + "\n", score


def write_report(
    issues: dict,
    total_pages: int,
    vault: Path | None = None,
    areas: tuple[str, ...] | list[str] | None = None,
) -> int:
    vault = vault or vc.vault_path()
    body, score = render_report(issues, total_pages, areas=areas)
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
    ap.add_argument(
        "--area",
        choices=["wiki", "objective", "research", "all"],
        default="all",
        help="area(s) to audit: wiki|objective|research|all (default: all)",
    )
    args = ap.parse_args(argv)

    vault = Path(args.path) if args.path else vc.vault_path(args.vault)

    if args.area == "all":
        areas = DEFAULT_AREAS
    else:
        areas = (args.area,)

    issues, total = audit(vault, areas=areas)

    if args.json:
        breakdown = _area_breakdown(issues, areas)
        out = {
            "vault": str(vault),
            "scanned": datetime.now().isoformat(timespec="seconds"),
            "areas": list(areas),
            "total_pages": total,
            "counts": {k: len(v) for k, v in issues.items()},
            "per_area": breakdown,
            "issues": issues,
        }
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.print_only:
        body, _ = render_report(issues, total, areas=areas)
        sys.stdout.write(body)
        return 0

    write_report(issues, total, vault, areas=areas)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
