#!/usr/bin/env python3
"""wiki-init: scaffold a fresh vault, or reconcile an existing live vault to the v0.2 schema.

Two modes:

  scaffold   - create the v0.2 folder skeleton + _template.md per repetitive item + the
               index/log/hot caches in an EMPTY (or new) vault directory.
  reconcile  - bring an EXISTING live vault into line with the v0.2 schema. CRITICAL: the
               reconcile NEVER mutates the live vault. It snapshots the vault to a /tmp copy,
               applies the target schema to the COPY only, diffs copy-vs-live, writes a
               human-readable change report to meta/health_report/wiki-init-dryrun-<ts>.md,
               prints a summary, and STOPS. Applying to the live vault is a separate, explicit,
               user-approved step (--apply, additive + idempotent).

Reconcile actions (honoring the RESOLVED Phase-1 decisions):
  - create missing folders: raw/{opinions,code,notebooklm}, research/query,
    meta/health_report/ + meta/nightly_report/ (as FOLDERS). objective/ is P2 -> SKIP.
    meta single-artifact stores stay FILES (ingest_index.json/.md, cost_report.md,
    health_report.md) - we do NOT fold them into folders.
  - (re)write _template.md per repetitive wiki item to the MERGED SUPERSET frontmatter
    (all live home-grown fields + bi-temporal timeline: + frozen type tags).
  - collapse wiki/hot/hot.md -> wiki/hot.md (file); drop the wiki/hot/ folder.
  - relocate the stray root stepfun-mfa.md into wiki/concepts/ (abstract method).
  - DROP daily/ and output/ (RESOLVED #5; output/ returns in Phase 5).

Frontmatter migration is additive and .get()-safe: existing _template fields are preserved;
only missing fields (notably timeline:) are added. No field is dropped.

Resolve $VAULT_ROOT via `agents.vault_config path`; never hard-code a vault path.

CLI:
  python scripts/wiki_init.py reconcile [--vault-root PATH] [--out-dir PATH] [--keep-copy]
  python scripts/wiki_init.py reconcile --apply --vault-root PATH   # APPLY to live (explicit)
  python scripts/wiki_init.py scaffold --vault-root PATH            # fresh vault skeleton
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Target schema description
# ---------------------------------------------------------------------------

# Folders to ensure exist (relative to vault root). objective/ is P2 -> excluded here.
TARGET_FOLDERS = [
    "raw/papers",
    "raw/articles",
    "raw/transcripts",
    "raw/notes",
    "raw/opinions",
    "raw/assets",
    "raw/code",
    "raw/notebooklm",
    "research/deep",
    "research/query",
    "wiki/entities",
    "wiki/concepts",
    "wiki/sources",
    "wiki/synthesis",
    "meta/health_report",   # accumulating series -> FOLDER
    "meta/nightly_report",  # accumulating series -> FOLDER
]

# Folders to DROP entirely (RESOLVED #5 + schema cleanup).
# templates/ = old Obsidian Templater dir (holds "Daily Note.md").
# Per-folder _template.md (colocated) is the v0.2 convention instead.
DROP_FOLDERS = ["daily", "output", "templates"]

# Vault root file to DELETE. Agent rules live in the CODE repo (CLAUDE.md / docs/ / .claude/).
# The vault PURPOSE lives in config/vaults/<v>/vault.yaml (agents.vault_config purpose).
VAULT_CLAUDE_FILE = "_CLAUDE.md"

# wiki/hot/ folder is collapsed to wiki/hot.md (file).
HOT_FOLDER = "wiki/hot"
HOT_FILE = "wiki/hot.md"

# The stray root page to relocate, and its destination subfolder.
STRAY_PAGE = "stepfun-mfa.md"
STRAY_DEST = "wiki/concepts"

DATE_PLACEHOLDER = "{{date}}"
TITLE_PLACEHOLDER = "{{title}}"


# ---------------------------------------------------------------------------
# Merged-superset templates (live home-grown fields + frozen tags + timeline:)
# ---------------------------------------------------------------------------

def _entity_template() -> str:
    return """---
type: entity
title: "{{title}}"
date: {{date}}
created: {{date}}
updated: {{date}}
entity_type: person        # person | organization | tool | company | project | place
role: ""
company: ""                # e.g. "[[Acme Corp]]" (frozen schema field)
last_interaction: {{date}}
status: seed               # seed | developing | mature | evergreen
aliases: []
tags:
  - entity
ai-first: true
related: []
sources: []
timeline:                  # bi-temporal facts - never delete, only append
  - fact: ""
    from: {{date}}
    until: present
    learned: {{date}}
    source: ""
---

## For future Claude
This is an entity page about {{title}}, created {{date}}. It records who/what this is, current
role/status, connections, and a bi-temporal timeline of facts. Top-level role/company/status
reflect the CURRENT state; the timeline preserves history (never overwrite, only append).

# {{title}}

## Overview
<!-- Who or what this is. One paragraph. -->

## Key facts
-

## Connections
<!-- wikilinks to related entities and concepts -->
-

## Source appearances
<!-- [[sources/X]] pages where this entity appears -->
-
"""


def _concept_template() -> str:
    return """---
type: concept
title: "{{title}}"
date: {{date}}
created: {{date}}
updated: {{date}}
complexity: intermediate   # basic | intermediate | advanced
domain: ""
status: seed               # seed | developing | mature | evergreen
related_projects: []       # frozen schema field
aliases: []
tags:
  - concept
ai-first: true
related: []
sources: []
---

## For future Claude
This is a concept page about {{title}}, created {{date}}. It defines the idea/framework/method,
how it works, why it matters, and links to related concepts and sources.

# {{title}}

## Definition
<!-- What this concept is. Declarative, present tense. One clear paragraph. -->

## How it works
<!-- Mechanism or explanation -->

## Why it matters
<!-- Significance in this domain -->

## Examples
-

## Connections
-

## Sources
-
"""


def _source_template() -> str:
    return """---
type: source
title: "{{title}}"
date: {{date}}
created: {{date}}
updated: {{date}}
source_type: article       # article | paper | transcript | video | data | note
author: ""
date_published: ""
url: ""                     # original source URL (kept verbatim)
source_url: ""              # frozen schema alias for url
content_hash: ""            # frozen schema: drift detection
confidence: medium          # high | medium | low
status: seed                # seed | developing | mature | evergreen
tags:
  - source
ai-first: true
related: []
sources: []                 # the raw/ file(s) this summarises, e.g. ["[[raw/articles/foo]]"]
timeline: []                # optional bi-temporal facts where a source's claims evolve
---

## For future Claude
This is a source summary about {{title}}, created {{date}}. One structured summary per ingested
raw source: key claims, entities mentioned, concepts introduced. The `sources:` field links the
immutable raw/ file(s) this summarises.

# {{title}}

## Summary
<!-- 2-3 sentence summary of the source -->

## Key claims
-

## Entities mentioned
- [[]] -

## Concepts introduced
- [[]] -

## Notes
"""


def _synthesis_template() -> str:
    return """---
type: synthesis
title: "{{title}}"
date: {{date}}
created: {{date}}
updated: {{date}}
synthesis_type: comparison   # comparison | analysis | literature-review
subjects: []                 # e.g. ["[[Subject A]]", "[[Subject B]]"]
dimensions: []
verdict: ""                  # one-line conclusion
status: seed                 # seed | developing | mature | evergreen
aliases: []
tags:
  - synthesis
ai-first: true
related: []
sources: []
---

## For future Claude
This is a synthesis page titled {{title}}, created {{date}}. It compares/analyses multiple
subjects across stated dimensions and records a one-line verdict, with sources cited as
[[wikilinks]].

# {{title}}

## Overview
<!-- Why these things are being compared/analysed and what question this answers. -->

## Comparison
| Dimension | Subject A | Subject B |
|-----------|-----------|-----------|
|           |           |           |

## Verdict
<!-- One clear conclusion - which is better for what use case. -->

## Sources
-
"""


def _health_report_template() -> str:
    return """---
type: health-report
date: {{date}}
tags:
  - health-report
ai-first: true
sources: []
---

## For future Claude
This is a vault health report generated by agents/vault_health.py. It records the health
score, page count, and per-category issue lists for the active vault on the report date.
Read this to understand current vault state before a lint or reconcile pass.

# Vault health report

**Health score:** /100

_Generated: {{date}}_

**Total wiki pages:**

## Code fence wrapped (0)

## Missing frontmatter (0)

## Unfilled template (0)

## Duplicate (0)

## Orphaned (0)

## Dead links (0)

## Stale (0)

## Empty (0)
"""


def _nightly_report_template() -> str:
    return """---
type: nightly-report
date: {{date}}
tags:
  - nightly-report
ai-first: true
sources: []
---

## For future Claude
This digest lists items that need human review: sources awaiting approval before being
added to the wiki, lint issues found, and any errors. Review and approve/reject pending
sources by updating meta/ingest_index.json (approve/reject verbs).

# Nightly digest {{date}}

## Pending sources (awaiting approval)
| id | file | status |
|----|------|--------|

## Research topics run
-

## Lint issues
-

## Errors / warnings
-
"""


def _deep_research_template() -> str:
    return """---
type: research-report
date: {{date}}
created: {{date}}
updated: {{date}}
topic: "{{title}}"
engine: claude         # claude | perplexity | free
status: seed           # seed | developing | mature | evergreen
tags:
  - research-report
ai-first: true
sources: []
---

## For future Claude
This is a deep research report filed by the research agent (wiki-research-deep skill). It
records the research question, methodology, findings, and cited wiki pages. The `sources:`
field lists raw/ files or external URLs used; `topic:` is the research question stem.

# Research: {{title}}

## Question
<!-- The precise research question that drove this investigation. -->

## Methodology
<!-- Which sources were consulted; search strategy; any caveats. -->

## Findings
<!-- Main findings, each cited with [[sources/X]] or [[concepts/Y]] wikilinks. -->
-

## Synthesis
<!-- One-paragraph synthesis of the findings as they relate to the vault PURPOSE. -->

## Open questions
-
"""


def _query_template() -> str:
    return """---
type: query-result
date: {{date}}
created: {{date}}
updated: {{date}}
question: "{{title}}"
tags:
  - query-result
ai-first: true
sources: []
---

## For future Claude
This is a saved wiki-query Q&A result. The `question:` field is the original query. The
answer is a synthesised response citing vault pages as [[wikilinks]]. Filed by the wiki
agent after a wiki-query call (research/query/ is the P2 query-history store).

# Q: {{title}}

## Answer
<!-- Synthesised answer citing [[wikilinks]]. -->

## Cited pages
-

## Gaps / follow-up
-
"""


def _hot_file(today: str) -> str:
    return f"""---
type: hot
date: {today}
created: {today}
updated: {today}
tags:
  - hot
ai-first: true
written_by: wiki
sources: []
---

## For future Claude
This is the wiki hot-cache: the most recent ingest-session context, current focus, blind spots,
proposed research directions, and open work threads. Read this first to orient. Keep under
~500 words; older context graduates to index/log.

# Hot cache

## Last updated
{today}

## Key recent facts
-

## Recent changes
-

## Active threads (next sessions)
-

## Blind spots / proposed directions
-

## Focus zone (last 10 nodes)
-
"""


TEMPLATES = {
    # wiki/ per-type knowledge templates (existing, kept)
    "wiki/entities/_template.md": _entity_template,
    "wiki/concepts/_template.md": _concept_template,
    "wiki/sources/_template.md": _source_template,
    "wiki/synthesis/_template.md": _synthesis_template,
    # producer-folder templates: colocated _template.md per folder that generates
    # repeated items/reports.  cost_report is a single regenerated FILE -> no template.
    "meta/health_report/_template.md": _health_report_template,
    "meta/nightly_report/_template.md": _nightly_report_template,
    "research/deep/_template.md": _deep_research_template,
    "research/query/_template.md": _query_template,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return _dt.date.today().isoformat()


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%dT%H%M%S")


def resolve_vault_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("VAULT_ROOT")
    if env:
        return Path(env)
    # Fall back to agents.vault_config (repo import).
    try:
        repo = Path(os.environ.get("CODE_PATH") or Path(__file__).resolve().parent.parent)
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from agents import vault_config  # type: ignore

        return Path(vault_config.vault_path())
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "could not resolve vault root: pass --vault-root or set $VAULT_ROOT "
            f"(vault_config import failed: {exc})"
        )


def _render(text: str, *, title_concrete: str | None = None) -> str:
    """Render template placeholders. Templates keep {{title}} (Obsidian Templater) but a
    concrete relocated page gets a real title."""
    out = text.replace(DATE_PLACEHOLDER, _today())
    if title_concrete is not None:
        out = out.replace(TITLE_PLACEHOLDER, title_concrete)
    return out


# ---------------------------------------------------------------------------
# Core reconcile (operates on a target directory; used for both the /tmp copy and --apply)
# ---------------------------------------------------------------------------

class Plan:
    """Records what reconcile changed, for the summary + report."""

    def __init__(self) -> None:
        self.folders_created: list[str] = []
        self.folders_dropped: list[str] = []
        self.templates_written: list[str] = []   # (path, "added"|"updated")
        self.template_actions: dict[str, str] = {}
        self.files_moved: list[tuple[str, str]] = []
        self.files_dropped: list[str] = []
        self.hot_collapsed: bool = False
        self.vault_claude_deleted: bool = False

    def is_noop(self) -> bool:
        return not (
            self.folders_created
            or self.folders_dropped
            or self.templates_written
            or self.files_moved
            or self.files_dropped
            or self.hot_collapsed
            or self.vault_claude_deleted
        )


def reconcile_tree(root: Path, plan: Plan) -> None:
    """Apply the target schema to `root` (a directory). Mutates `root`. Idempotent."""
    today = _today()

    # 1. Create missing folders (with a .gitkeep so empty dirs persist under git).
    for rel in TARGET_FOLDERS:
        d = root / rel
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            (d / ".gitkeep").write_text("", encoding="utf-8")
            plan.folders_created.append(rel)

    # 2. (Re)write _template.md per repetitive wiki item to the merged superset.
    for rel, factory in TEMPLATES.items():
        path = root / rel
        new_text = _render(factory())
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_text, encoding="utf-8")
            plan.templates_written.append(rel)
            plan.template_actions[rel] = "added"
        else:
            old = path.read_text(encoding="utf-8")
            if old != new_text:
                path.write_text(new_text, encoding="utf-8")
                plan.templates_written.append(rel)
                plan.template_actions[rel] = "updated"

    # 3. Collapse wiki/hot/hot.md -> wiki/hot.md (file); drop the wiki/hot/ folder.
    hot_dir = root / HOT_FOLDER
    hot_file = root / HOT_FILE
    if hot_dir.is_dir():
        src = hot_dir / "hot.md"
        if not hot_file.exists():
            if src.exists():
                hot_file.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                hot_file.write_text(_hot_file(today), encoding="utf-8")
        shutil.rmtree(hot_dir)
        plan.hot_collapsed = True
    elif not hot_file.exists():
        hot_file.write_text(_hot_file(today), encoding="utf-8")
        plan.files_moved.append(("(new)", HOT_FILE))

    # 4. Relocate the stray root page into wiki/concepts/.
    stray = root / STRAY_PAGE
    if stray.is_file():
        dest_dir = root / STRAY_DEST
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / STRAY_PAGE
        if not dest.exists():
            shutil.move(str(stray), str(dest))
            plan.files_moved.append((STRAY_PAGE, f"{STRAY_DEST}/{STRAY_PAGE}"))
        else:
            # destination already exists (idempotent re-run); just drop the stray root copy
            stray.unlink()
            plan.files_dropped.append(STRAY_PAGE)

    # 5. Drop DROP_FOLDERS entirely (daily/, output/, templates/).
    for rel in DROP_FOLDERS:
        d = root / rel
        if d.exists():
            shutil.rmtree(d)
            plan.folders_dropped.append(rel)

    # 6. Delete vault _CLAUDE.md. Agent rules belong in the CODE repo (CLAUDE.md / docs/ /
    #    .claude/); vault PURPOSE is in config/vaults/<v>/vault.yaml (agents.vault_config
    #    purpose). The vault _CLAUDE.md is a v0.1 artefact and must not be recreated.
    vault_claude = root / VAULT_CLAUDE_FILE
    if vault_claude.is_file():
        vault_claude.unlink()
        plan.vault_claude_deleted = True


# ---------------------------------------------------------------------------
# Snapshot + diff + report (dry-run-on-copy)
# ---------------------------------------------------------------------------

def snapshot(vault_root: Path, dest: Path) -> None:
    """Copy the live vault to dest, excluding .git and .obsidian (volatile / large)."""
    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in {".git", ".obsidian"}}

    shutil.copytree(vault_root, dest, ignore=_ignore, symlinks=False)


def diff_trees(live: Path, copy: Path) -> str:
    """Return a unified recursive diff (copy is the proposed target)."""
    try:
        proc = subprocess.run(
            ["diff", "-ruN", "--exclude=.git", "--exclude=.obsidian", str(live), str(copy)],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout or "(no textual diff)\n"
    except FileNotFoundError:
        return "(diff binary unavailable; see structured summary above)\n"


def render_report(plan: Plan, ts: str, vault_root: Path, raw_diff: str) -> str:
    today = _today()
    lines: list[str] = []
    lines.append("---")
    lines.append("type: health-report")
    lines.append(f"date: {today}")
    lines.append("tags:")
    lines.append("  - health-report")
    lines.append("  - wiki-init-dryrun")
    lines.append("ai-first: true")
    lines.append("sources: []")
    lines.append("---")
    lines.append("")
    lines.append("## For future Claude")
    lines.append(
        f"This is a wiki-init DRY-RUN diff report generated {today} (ts {ts}). It describes the "
        "schema-reconcile changes proposed for the live vault. NOTHING was applied to the live "
        "vault. Review the summary, then run `wiki_init.py reconcile --apply` to apply."
    )
    lines.append("")
    lines.append(f"# wiki-init dry-run report ({ts})")
    lines.append("")
    lines.append(f"- Vault: `{vault_root}`")
    lines.append(f"- Mode: DRY-RUN on a /tmp copy. NO changes were made to the live vault.")
    lines.append(f"- Schema version: {SCHEMA_VERSION}")
    lines.append("")
    lines.append("## Summary of proposed changes")
    lines.append("")
    if plan.is_noop():
        lines.append("- No changes - the vault already matches the v0.2 schema (no-op).")
    else:
        lines.append("### Folders to create")
        lines.extend([f"- `{f}/`" for f in plan.folders_created] or ["- (none)"])
        lines.append("")
        lines.append("### Folders to drop")
        lines.extend([f"- `{f}/`" for f in plan.folders_dropped] or ["- (none)"])
        lines.append("")
        lines.append("### Templates to add/change")
        if plan.templates_written:
            lines.extend(
                [f"- `{p}` ({plan.template_actions.get(p, 'changed')})" for p in plan.templates_written]
            )
        else:
            lines.append("- (none)")
        lines.append("")
        lines.append("### Files to move")
        lines.extend([f"- `{s}` -> `{d}`" for s, d in plan.files_moved] or ["- (none)"])
        lines.append("")
        lines.append("### Files to drop")
        lines.extend([f"- `{f}`" for f in plan.files_dropped] or ["- (none)"])
        lines.append("")
        lines.append("### wiki/hot")
        lines.append(
            "- Collapsed `wiki/hot/hot.md` -> `wiki/hot.md` (file)."
            if plan.hot_collapsed
            else "- Already a file (no change)."
        )
        lines.append("")
        lines.append("### Frontmatter migrations (templates -> merged superset)")
        lines.append(
            "- Each `wiki/<type>/_template.md` is (re)written to the MERGED SUPERSET: all live "
            "home-grown fields are kept and the frozen bi-temporal `timeline:` + frozen type "
            "tags (entities: `company`, `last_interaction`; sources: `source_url`, "
            "`content_hash`; concepts: `related_projects`) are ADDED. No field is dropped. "
            "Existing pages are NOT rewritten by reconcile - only the templates change; page "
            "frontmatter is migrated additively at ingest time."
        )
        lines.append("")
        lines.append("### Producer-folder templates")
        lines.append(
            "- A `_template.md` is added INSIDE each producer folder (the folder that generates "
            "a series of repeated items/reports): `meta/health_report/`, `meta/nightly_report/`, "
            "`research/deep/`, `research/query/`. Each template mirrors the structure written "
            "by the producing code/skill. `meta/cost_report.md` is a single regenerated FILE "
            "(no folder template needed)."
        )
        lines.append("")
        lines.append("### vault _CLAUDE.md removal")
        lines.append(
            "- Vault `_CLAUDE.md` is DELETED. Agent rules live in the CODE repo "
            "(CLAUDE.md / docs/ / .claude/). Vault PURPOSE is authoritative in "
            "`config/vaults/<v>/vault.yaml` (resolve via `agents.vault_config purpose`). "
            "The vault `_CLAUDE.md` is a v0.1 artefact and MUST NOT be recreated in the vault."
        )
        lines.append(
            f"  vault_claude_deleted: {plan.vault_claude_deleted}"
        )
    lines.append("")
    lines.append("## STOP - review gate")
    lines.append(
        "This is a dry run. No write touched the live vault. To apply (additive + idempotent): "
        "`python scripts/wiki_init.py reconcile --apply --vault-root <path>`."
    )
    lines.append("")
    lines.append("## Raw recursive diff (live vs proposed copy)")
    lines.append("")
    lines.append("```diff")
    lines.append(raw_diff.rstrip("\n"))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode entry points
# ---------------------------------------------------------------------------

def run_scaffold(vault_root: Path) -> Plan:
    """Create the v0.2 skeleton in (an empty/new) vault_root."""
    vault_root.mkdir(parents=True, exist_ok=True)
    plan = Plan()
    reconcile_tree(vault_root, plan)
    # Scaffold also seeds index.md and log.md if absent.
    today = _today()
    idx = vault_root / "wiki/index.md"
    if not idx.exists():
        idx.write_text(
            f"---\ntype: index\ndate: {today}\ncreated: {today}\nupdated: {today}\n"
            "tags:\n  - index\nai-first: true\nsources: []\n---\n\n"
            "## For future Claude\nMaster catalog of all wiki pages, maintained by agents.\n\n"
            "# Vault index\n\n## Sources\n\n## Synthesis\n\n## Concepts\n\n## Entities\n",
            encoding="utf-8",
        )
        plan.files_moved.append(("(new)", "wiki/index.md"))
    log = vault_root / "wiki/log.md"
    if not log.exists():
        log.write_text(
            f"---\ntype: operation-log\ndate: {today}\ncreated: {today}\nupdated: {today}\n"
            "tags:\n  - operation-log\nai-first: true\nsources: []\n---\n\n"
            "# Operation log\n\nAppend one row per agent operation.\n\n"
            "| Date | Operation | Source | Pages created/updated | Notes |\n"
            "| ---- | --------- | ------ | --------------------- | ----- |\n",
            encoding="utf-8",
        )
        plan.files_moved.append(("(new)", "wiki/log.md"))
    return plan


def run_reconcile(
    vault_root: Path,
    *,
    apply: bool = False,
    out_dir: Path | None = None,
    keep_copy: bool = False,
) -> tuple[Plan, Path | None]:
    """Reconcile mode.

    apply=False (default): dry-run-on-copy. Snapshot to /tmp, reconcile the COPY, diff, write a
        report to <vault>/meta/health_report/wiki-init-dryrun-<ts>.md, return (plan, report).
        ZERO changes to the live vault.
    apply=True: reconcile the LIVE vault in place (additive + idempotent). Returns (plan, None).
    """
    if not vault_root.is_dir():
        raise SystemExit(f"vault root does not exist: {vault_root}")

    ts = _ts()

    if apply:
        plan = Plan()
        reconcile_tree(vault_root, plan)
        return plan, None

    # --- dry-run on a copy ---
    tmp_base = out_dir or Path(os.environ.get("TMPDIR", "/tmp"))
    tmp_base.mkdir(parents=True, exist_ok=True)
    copy_dir = tmp_base / f"wiki-init-dryrun-{ts}"
    if copy_dir.exists():
        shutil.rmtree(copy_dir)
    snapshot(vault_root, copy_dir)

    plan = Plan()
    reconcile_tree(copy_dir, plan)

    raw_diff = diff_trees(vault_root, copy_dir)
    report = render_report(plan, ts, vault_root, raw_diff)

    # Write the report into the backend-owned health_report folder (created on the LIVE vault;
    # this is the ONE permitted live write - a backend-owned audit artifact, not vault knowledge).
    report_dir = vault_root / "meta" / "health_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"wiki-init-dryrun-{ts}.md"
    report_path.write_text(report, encoding="utf-8")

    if not keep_copy:
        shutil.rmtree(copy_dir, ignore_errors=True)

    return plan, report_path


def print_summary(plan: Plan, report_path: Path | None) -> None:
    print("== wiki-init reconcile dry-run summary ==")
    print(f"folders to create    : {len(plan.folders_created)} -> {plan.folders_created}")
    print(f"folders to drop      : {len(plan.folders_dropped)} -> {plan.folders_dropped}")
    print(f"templates changed    : {len(plan.templates_written)} -> "
          f"{[(p, plan.template_actions.get(p)) for p in plan.templates_written]}")
    print(f"files moved          : {plan.files_moved}")
    print(f"files dropped        : {plan.files_dropped}")
    print(f"hot collapsed        : {plan.hot_collapsed}")
    print(f"vault _CLAUDE.md del : {plan.vault_claude_deleted}")
    if report_path:
        print(f"report written       : {report_path}")
    print("STOP: nothing was applied to the live vault.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="wiki-init: scaffold or reconcile a vault to v0.2 schema")
    sub = ap.add_subparsers(dest="mode", required=True)

    rc = sub.add_parser("reconcile", help="reconcile an existing vault (dry-run-on-copy by default)")
    rc.add_argument("--vault-root", default=None)
    rc.add_argument("--out-dir", default=None, help="base dir for the /tmp snapshot copy")
    rc.add_argument("--keep-copy", action="store_true", help="do not delete the snapshot copy")
    rc.add_argument("--apply", action="store_true",
                    help="APPLY to the LIVE vault (additive+idempotent). Use only after review.")

    sc = sub.add_parser("scaffold", help="scaffold a fresh/empty vault")
    sc.add_argument("--vault-root", default=None)

    args = ap.parse_args(argv)

    if args.mode == "scaffold":
        root = resolve_vault_root(args.vault_root)
        plan = run_scaffold(root)
        print(f"scaffolded {root}; folders created: {plan.folders_created}; "
              f"templates: {plan.templates_written}")
        return 0

    if args.mode == "reconcile":
        root = resolve_vault_root(args.vault_root)
        plan, report = run_reconcile(
            root,
            apply=args.apply,
            out_dir=Path(args.out_dir) if args.out_dir else None,
            keep_copy=args.keep_copy,
        )
        if args.apply:
            print(f"APPLIED reconcile to live vault {root}; folders created: "
                  f"{plan.folders_created}; templates: {plan.templates_written}; "
                  f"moved: {plan.files_moved}; dropped folders: {plan.folders_dropped}")
        else:
            print_summary(plan, report)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
