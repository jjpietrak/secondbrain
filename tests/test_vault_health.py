#!/usr/bin/env python3
"""Hermetic test for agents/vault_health.py (OSB-extended checks).

Builds a throwaway fixture vault under a temp dir with:
  - a code-fence-wrapped page (frontmatter trapped in a leading ``` fence)
  - an unfilled `_template`-style page with <% %> Templater syntax in a real page
  - an orphan page (no inbound wikilinks)
  - a page with a dead wikilink
  - two duplicate-stem pages
Then asserts vault_health flags each. No LLM, no network, no real vault. $0.
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

# import agents/ as a package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents import vault_health as vh  # noqa: E402


def build_fixture(root: Path) -> None:
    wiki = root / "wiki" / "concepts"
    wiki.mkdir(parents=True)

    # A well-formed hub page that links to several others (provides backlinks).
    (wiki / "hub.md").write_text(
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
        "Links to [[target]] and [[ghost-page]] and [[dupe-thing]].\n"
        "Body is long enough to not count as a stub page here for sure yes.\n"
    )

    # A normal target page (linked from hub -> NOT orphan).
    (wiki / "target.md").write_text(
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
        "I am the target. [[hub]] points back. Plenty of words to clear the stub bar.\n"
    )

    # Orphan: no inbound links, but links out to hub so hub is not orphaned either.
    (wiki / "orphan.md").write_text(
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
        "Nobody links to me. I link to [[hub]]. Enough text to avoid the stub flag here.\n"
    )

    # Code-fence-wrapped: frontmatter trapped in a leading ``` fence (UNWRAP, do not add).
    (wiki / "fenced.md").write_text(
        "```markdown\n---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\n"
        "sources: []\n---\nReal body trapped in a fence. [[hub]] link to avoid orphan.\n```\n"
    )

    # Unfilled template syntax left in a real page.
    (wiki / "unfilled.md").write_text(
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
        "Created on <% tp.date.now() %> by the template. [[hub]] to avoid orphan flag.\n"
    )

    # Duplicate stems (normalized collide): "dupe thing" twice.
    (wiki / "dupe-thing.md").write_text(
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
        "First dupe. [[hub]] link so it is not also an orphan in this fixture run here.\n"
    )
    (wiki / "Dupe_Thing.md").write_text(
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
        "Second dupe. [[hub]] link so it is not also an orphan in this fixture run here.\n"
    )


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_fixture(root)
        issues, total = vh.audit(root)

        def has(key: str, needle: str) -> bool:
            return any(needle in item for item in issues[key])

        checks = [
            ("code_fence_wrapped flagged", has("code_fence_wrapped", "fenced.md")),
            ("unfilled_template flagged", has("unfilled_template", "unfilled.md")),
            ("orphan flagged", has("orphaned", "orphan.md")),
            ("dead_link flagged", has("dead_links", "ghost-page")),
            ("duplicate flagged", has("duplicate", "dupe")),
            # fence-wrapped page must NOT also be reported as missing frontmatter
            ("fenced not double-reported as missing_fm",
             not has("missing_frontmatter", "fenced.md")),
            ("total pages counted", total >= 7),
        ]
        for label, ok in checks:
            if ok:
                print(f"  PASS  {label}")
            else:
                failures.append(label)
                print(f"  FAIL  {label}")

        # Smoke the JSON path renders without error.
        body, score = vh.render_report(issues, total)
        if "Vault health report" not in body:
            failures.append("render_report produced no report")
            print("  FAIL  render_report produced no report")
        else:
            print(f"  PASS  render_report ok (score={score})")

    if failures:
        print(f"\nFAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print("\nAll vault_health tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
