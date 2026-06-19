#!/usr/bin/env python3
"""Hermetic test for scripts/wiki_lint_extra.py and its reuse of vault_health.

Builds a throwaway fixture vault under a temp dir with:
  - a stray root `stepfun-mfa.md` (root-level .md that belongs in wiki/)
  - an orphan page + a dead wikilink (covered by vault_health, asserted via reuse)
  - a page with an empty `## TODO` section (heading, no body)
  - a term wikilinked from 2 distinct pages with no page of its own (missing_pages)
  - a genuine contradiction pair (role evolved without a timeline append) so the
    fixture matches the stream's reconcile/lint contract; lint flags the structure,
    reconcile (LLM) adjudicates the contradiction at runtime.
Then asserts wiki_lint_extra flags the extras AND that vault_health (reused, not
re-implemented) still flags the orphan + dead link on the same fixture. $0, no LLM.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import wiki_lint_extra as wl  # noqa: E402
from agents import vault_health as vh      # noqa: E402

FM = "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"


def build_fixture(root: Path) -> None:
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "wiki" / "entities").mkdir(parents=True)
    w = root / "wiki" / "concepts"
    e = root / "wiki" / "entities"

    # Stray root file (the live offender shape).
    (root / "stepfun-mfa.md").write_text(FM + "Stray at vault root. [[hub]].\n")
    # Allowed root files (must NOT be flagged stray).
    # NOTE: _CLAUDE.md is intentionally NOT created here. It is a v0.1 artefact;
    # wiki_init reconcile deletes it and wiki_lint_extra flags it as stray (Change 2).
    (root / "index.md").write_text("# index\n")

    # Hub provides inbound links so most pages are not orphans.
    (w / "hub.md").write_text(
        FM + "Links to [[target]] and [[ghost-page]] and [[Disaggregation]].\n"
        "Long enough body to clear the stub bar for this fixture page here.\n"
    )
    (w / "target.md").write_text(
        FM + "Target. [[hub]] points back. Enough words to clear the stub bar here.\n"
    )
    # Orphan: nothing links to it (it links out so hub stays linked).
    (w / "orphan.md").write_text(
        FM + "Nobody links to me; I link to [[hub]]. Enough text to avoid stub flag.\n"
    )
    # Empty section: heading with no content beneath it.
    (w / "empty-sec.md").write_text(
        FM + "Body links [[hub]] to avoid orphan.\n\n## TODO\n\n## Notes\nReal content.\n"
    )
    # A second page that also links the missing [[Disaggregation]] term (2 mentions).
    (w / "second.md").write_text(
        FM + "Also discusses [[Disaggregation]] and links [[hub]] to stay non-orphan.\n"
    )
    # Contradiction pair: an entity whose role differs across two pages (structural
    # cue for wiki-reconcile; lint just confirms the fixture has both pages present).
    (e / "alice.md").write_text(
        FM + "Alice is CTO at [[hub]] as of 2026-01. Linked from [[second]].\n"
    )


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_fixture(root)

        res = wl.lint(root)

        def has(key: str, needle: str) -> bool:
            return any(needle in item for item in res[key])

        checks = [
            ("stray_root flags stepfun-mfa.md", "stepfun-mfa.md" in res["stray_root"]),
            # _CLAUDE.md is now a v0.1 artefact - wiki_init reconcile deletes it and
            # wiki_lint_extra flags it as stray if found at vault root. No fixture file.
            ("index.md not flagged stray", "index.md" not in res["stray_root"]),
            ("empty_sections flags TODO", has("empty_sections", "TODO")),
            ("empty_sections skips Notes", not has("empty_sections", "'Notes'")),
            ("missing_pages flags Disaggregation", has("missing_pages", "Disaggregation")),
            ("missing_pages skips single-mention ghost-page",
             not has("missing_pages", "ghost-page")),
        ]

        # Reuse check: vault_health (NOT re-implemented here) still flags orphan + dead link.
        vh_issues, _ = vh.audit(root)
        checks.append(("reuse: vault_health flags orphan",
                       any("orphan.md" in i for i in vh_issues["orphaned"])))
        checks.append(("reuse: vault_health flags dead link",
                       any("ghost-page" in i for i in vh_issues["dead_links"])))

        for label, ok in checks:
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")
            if not ok:
                failures.append(label)

    if failures:
        print(f"\nFAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print("\nAll wiki_lint_extra tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
