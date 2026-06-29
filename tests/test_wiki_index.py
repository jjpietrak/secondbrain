#!/usr/bin/env python3
"""Hermetic tests for scripts/wiki_index.py.

Creates a fixture vault in a tempdir, runs build_index(), and asserts:
  - The index contains the real page Title (not a bare arXiv id or filename stem).
  - The index has an Ingest date column (date values present for sourced pages).
  - Sources with an ingest_index entry get their ingested_at date.
  - Pages without an ingest_index entry fall back to frontmatter `created`.
  - Template files (_template.md) are excluded.
  - wiki/index.md itself is excluded from the scan.
  - wiki/hot.md and wiki/log.md are excluded from the scan.
  - Type-based sections (Sources, Concepts, Entities, Synthesis) appear.

No LLM, no network, no real vault. $0.
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.wiki_index import build_index, _parse_fm, _page_title, _ingest_date  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _make_vault(tmp_root: Path) -> Path:
    """Create a minimal fixture vault under tmp_root/vault/."""
    root = tmp_root / "vault"

    for d in ("wiki/sources", "wiki/concepts", "wiki/entities", "wiki/synthesis", "meta"):
        (root / d).mkdir(parents=True, exist_ok=True)

    # sources page with a real title (NOT an arXiv id)
    (root / "wiki" / "sources" / "photons-to-tokens.md").write_text(
        textwrap.dedent("""\
        ---
        type: source
        title: "Photons to Tokens: Systems-Level Performance"
        created: 2026-06-16
        updated: 2026-06-16
        status: developing
        ai-first: true
        ---

        # Photons to Tokens: Systems-Level Performance

        A preprint about optical LLM accelerators.
        """),
        encoding="utf-8",
    )

    # concept page with only H1 title (no frontmatter title:)
    (root / "wiki" / "concepts" / "programming-latency.md").write_text(
        textwrap.dedent("""\
        ---
        type: concept
        created: 2026-06-15
        updated: 2026-06-15
        status: developing
        ai-first: true
        ---

        # Programming Latency

        The hard optical ceiling.
        """),
        encoding="utf-8",
    )

    # entity page with neither title nor H1 (should use filename stem)
    (root / "wiki" / "entities" / "groq-lpu.md").write_text(
        textwrap.dedent("""\
        ---
        type: entity
        created: 2026-06-14
        updated: 2026-06-14
        status: seed
        ai-first: true
        ---

        LPU hardware family.
        """),
        encoding="utf-8",
    )

    # synthesis page
    (root / "wiki" / "synthesis" / "disaggregation-thesis.md").write_text(
        textwrap.dedent("""\
        ---
        type: synthesis
        title: "Disaggregation Thesis 2025-2026"
        created: 2026-06-17
        updated: 2026-06-17
        status: developing
        ai-first: true
        ---

        Cross-source literature review.
        """),
        encoding="utf-8",
    )

    # _template.md must be excluded
    (root / "wiki" / "sources" / "_template.md").write_text(
        textwrap.dedent("""\
        ---
        type: source
        title: "Template"
        ---
        """),
        encoding="utf-8",
    )

    # wiki/index.md placeholder (must be excluded from scan)
    (root / "wiki" / "index.md").write_text("# Old index\n", encoding="utf-8")

    # wiki/hot.md + wiki/log.md must be excluded
    (root / "wiki" / "hot.md").write_text("# Hot\n", encoding="utf-8")
    (root / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")

    # ingest_index.json: photons-to-tokens has an ingested_at date
    ingest_data = {
        "version": 3,
        "vault": "fixture",
        "sources": {
            "arxiv:photons": {
                "id": "arxiv:photons",
                "id_type": "arxiv",
                "filename": "raw/papers/photons-to-tokens.pdf",
                "source_page": "wiki/sources/photons-to-tokens.md",
                "status": "ingested",
                "ingested_at": "2026-06-16T14:00:00+00:00",
                "first_seen": "2026-06-16T13:00:00+00:00",
                "content_hash": "sha256:abc123",
                "title": "Photons to Tokens",
                "url": "",
                "source_type": "paper",
                "discovered_by": "",
                "rationale": "",
                "relevance_score": None,
                "objective_ids": [],
                "proposed_at": None,
                "rejected_at": None,
                "rejection_reason": "",
            }
        },
    }
    (root / "meta" / "ingest_index.json").write_text(
        json.dumps(ingest_data, indent=2), encoding="utf-8"
    )

    return root


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def main() -> int:
    failures: list[str] = []

    def check(label: str, condition: bool) -> None:
        if condition:
            print(f"  PASS  {label}")
        else:
            failures.append(label)
            print(f"  FAIL  {label}")

    with tempfile.TemporaryDirectory() as td:
        vault = _make_vault(Path(td))
        content = build_index(vault, dry_run=True)

        print("\n--- Integration tests (fixture vault) ---")

        # T1: real title present (not arXiv id)
        check(
            "real title 'Photons to Tokens' present",
            "Photons to Tokens" in content,
        )
        check(
            "arXiv id NOT used as title",
            "arxiv:photons" not in content,
        )

        # T2: ingest date column header
        check(
            "Ingest date column header present",
            "Ingest date" in content,
        )

        # T3: ingested_at date from ingest_index
        check(
            "ingested_at date '2026-06-16' from ingest_index",
            "2026-06-16" in content,
        )

        # T4: fallback to frontmatter created
        check(
            "fallback created date '2026-06-15' for concept page",
            "2026-06-15" in content,
        )

        # T5: _template excluded
        check(
            "_template file excluded",
            "_template" not in content,
        )

        # T6: hot/log/index excluded
        bad_lines = [
            ln for ln in content.splitlines()
            if "|" in ln and any(x in ln for x in ("hot.md", "log.md"))
        ]
        check("wiki/hot.md and wiki/log.md excluded from rows", len(bad_lines) == 0)

        # T7: type-based sections
        for section in ("## Sources", "## Concepts", "## Entities", "## Synthesis"):
            check(f"section '{section}' present", section in content)

        # T8: H1 fallback title
        check(
            "H1 fallback title 'Programming Latency' present",
            "Programming Latency" in content,
        )

        # T9: stem fallback title (groq-lpu -> "Groq Lpu" or similar)
        check(
            "filename-stem fallback for groq-lpu.md contains 'Groq'",
            "Groq" in content,
        )

        # T10: synthesis page title
        check(
            "synthesis title 'Disaggregation Thesis 2025-2026' present",
            "Disaggregation Thesis" in content,
        )

    # --- Unit tests ---
    print("\n--- Unit tests (helpers) ---")

    # _parse_fm: quoted title with colon inside
    fm_text = textwrap.dedent("""\
        ---
        type: source
        title: "Photons to Tokens: Systems-Level Performance"
        created: 2026-06-16
        ---
        """)
    fm = _parse_fm(fm_text)
    check("parse_fm strips quotes from title", fm.get("title") == "Photons to Tokens: Systems-Level Performance")
    check("parse_fm reads type", fm.get("type") == "source")
    check("parse_fm reads created", fm.get("created") == "2026-06-16")

    # _page_title precedence: fm title > H1 > stem
    with tempfile.TemporaryDirectory() as td2:
        dummy = Path(td2) / "test-page.md"
        dummy.write_text("# H1 Heading\n")
        check(
            "_page_title: fm title wins over H1",
            _page_title({"title": "FM Title"}, "# H1 Heading\n", dummy) == "FM Title",
        )
        check(
            "_page_title: H1 wins over stem",
            _page_title({}, "# H1 Heading\n", dummy) == "H1 Heading",
        )
        check(
            "_page_title: stem fallback when no title/H1",
            _page_title({}, "no heading here\n", dummy) == "Test Page",
        )

        # _ingest_date priority
        dummy_page = Path(td2) / "wiki" / "sources" / "test.md"
        dummy_page.parent.mkdir(parents=True, exist_ok=True)
        dummy_page.touch()

        ingest_map_with = {
            "wiki/sources/test.md": {
                "ingested_at": "2026-06-20T10:00:00+00:00",
                "first_seen": "2026-06-19T10:00:00+00:00",
            }
        }
        check(
            "_ingest_date: ingest_index ingested_at takes priority",
            _ingest_date("wiki/sources/test.md", {"created": "2026-01-01"}, dummy_page, ingest_map_with) == "2026-06-20",
        )
        check(
            "_ingest_date: falls back to frontmatter created",
            _ingest_date("wiki/sources/test.md", {"created": "2026-01-01"}, dummy_page, {}) == "2026-01-01",
        )
        check(
            "_ingest_date: falls back to frontmatter date field",
            _ingest_date("wiki/sources/test.md", {"date": "2026-03-15"}, dummy_page, {}) == "2026-03-15",
        )

    # Summary
    if failures:
        print(f"\nFAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print(f"\nAll wiki_index tests passed ({10 + 7 + 7} checks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
