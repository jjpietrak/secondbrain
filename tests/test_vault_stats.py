#!/usr/bin/env python3
"""Hermetic test for agents/vault_stats.py.

Builds a throwaway fixture vault under a temp dir with a mix of types, statuses, and
tags, then asserts vault_stats aggregates the counts correctly. No LLM, no network,
no real vault. $0.
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents import vault_stats as vs  # noqa: E402


def build_fixture(root: Path) -> None:
    ent = root / "wiki" / "entities"
    con = root / "wiki" / "concepts"
    ent.mkdir(parents=True)
    con.mkdir(parents=True)

    (ent / "alice.md").write_text(
        "---\ntype: entity\nstatus: mature\ntags:\n  - person\n  - optics\n"
        "updated: 2026-06-18\n---\nAlice. #optics body tag.\n"
    )
    (ent / "acme.md").write_text(
        "---\ntype: entity\nstatus: developing\ntags: [company, optics]\n"
        "updated: 2026-06-10\n---\nAcme Corp.\n"
    )
    (con / "disagg.md").write_text(
        "---\ntype: concept\nstatus: seed\ntags:\n  - optics\n"
        "updated: 2026-06-19\n---\nDisaggregation concept.\n"
    )
    (con / "attention.md").write_text(
        "---\ntype: concept\nstatus: mature\nupdated: 2026-05-01\n---\n"
        "Attention. #ml inline tag here.\n"
    )
    # untyped / unset page
    (con / "loose.md").write_text(
        "---\ncreated: 2026-04-01\n---\nNo type, no status.\n"
    )
    # skippable structural files must NOT be counted
    (root / "wiki" / "index.md").write_text("---\ntype: index\n---\nindex\n")
    (con / "_template.md").write_text("---\ntype: concept\n---\ntemplate\n")


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_fixture(root)
        stats = vs.aggregate(root)

        checks = [
            ("total pages excludes index/_template", stats["total_pages"] == 5),
            ("by_type entity=2", stats["by_type"].get("entity") == 2),
            ("by_type concept=2", stats["by_type"].get("concept") == 2),
            ("by_type untyped=1", stats["by_type"].get("untyped") == 1),
            ("by_status mature=2", stats["by_status"].get("mature") == 2),
            ("by_status developing=1", stats["by_status"].get("developing") == 1),
            ("by_status seed=1", stats["by_status"].get("seed") == 1),
            ("by_status unset=1", stats["by_status"].get("unset") == 1),
            # optics: alice fm(1)+inline(1), acme fm(1), disagg fm(1) = 4
            ("tag optics aggregated (=4)", stats["tags"].get("optics", 0) == 4),
            ("block-list tag person parsed", stats["tags"].get("person") == 1),
            ("inline tag ml counted", stats["tags"].get("ml") == 1),
            ("most_recently_edited top is 2026-06-19",
             bool(stats["most_recently_edited"]) and
             stats["most_recently_edited"][0][1] == "2026-06-19"),
        ]
        for label, ok in checks:
            if ok:
                print(f"  PASS  {label}")
            else:
                failures.append(label)
                print(f"  FAIL  {label}  (got: {label})")

        body = vs.render_report(stats)
        if "Vault stats" not in body or "By type" not in body:
            failures.append("render_report produced no report")
            print("  FAIL  render_report produced no report")
        else:
            print("  PASS  render_report ok")

    if failures:
        print(f"\nFAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print("\nAll vault_stats tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
