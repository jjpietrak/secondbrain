#!/usr/bin/env python3
"""Hermetic tests for agents/vault_health.py.

Covers:
  1. All existing wiki-only checks (code_fence, unfilled, orphan, dead_link, duplicate,
     path-qualified link resolution, render_report).
  2. --area wiki reproduces the same results as the original wiki-only audit().
  3. All-area scan surfaces issues in objective/ and research/ areas.
  4. Cross-area wikilink [[wiki/concepts/foo]] from an objective node resolves correctly
     (no false dead-link, linked page not orphaned).
  5. Per-type required frontmatter is enforced per area (objective/research types).

No LLM, no network, no real vault. $0.
Run:  .venv/bin/python -m pytest tests/test_vault_health.py -q
"""
from __future__ import annotations
import sys
import json
import tempfile
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents import vault_health as vh  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_wiki_fixture(root: Path) -> None:
    """Build the original wiki-only fixture (mirrors the pre-refactor tests)."""
    wiki = root / "wiki" / "concepts"
    wiki.mkdir(parents=True)

    # A well-formed hub page that links to several others (provides backlinks).
    _write(wiki / "hub.md",
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
        "written_by: wiki\n---\n"
        "Links to [[target]] and [[ghost-page]] and [[dupe-thing]].\n"
        "Body is long enough to not count as a stub page here for sure yes.\n"
    )

    # A normal target page (linked from hub -> NOT orphan).
    _write(wiki / "target.md",
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
        "written_by: wiki\n---\n"
        "I am the target. [[hub]] points back. Plenty of words to clear the stub bar.\n"
    )

    # Orphan: no inbound links, but links out to hub so hub is not orphaned either.
    # Wiki uses inbound-only orphan rule, so this IS still flagged orphaned even though
    # it has outbound links. This verifies the back-compat wiki behaviour.
    _write(wiki / "orphan.md",
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
        "written_by: wiki\n---\n"
        "Nobody links to me. I link to [[hub]]. Enough text to avoid the stub flag here.\n"
    )

    # Code-fence-wrapped: frontmatter trapped in a leading ``` fence (UNWRAP, do not add).
    _write(wiki / "fenced.md",
        "```markdown\n---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\n"
        "sources: []\n---\nReal body trapped in a fence. [[hub]] link to avoid orphan.\n```\n"
    )

    # Unfilled template syntax left in a real page.
    _write(wiki / "unfilled.md",
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
        "written_by: wiki\n---\n"
        "Created on <% tp.date.now() %> by the template. [[hub]] to avoid orphan flag.\n"
    )

    # Path-qualified wikilinks: a source page linked ONLY via [[sources/cited-src]].
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    _write(sources / "cited-src.md",
        "---\ntype: source\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
        "written_by: USER\n---\n"
        "A cited source page. Reached only by a path-qualified wikilink. Long enough body.\n"
    )
    _write(wiki / "hub2.md",
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
        "written_by: wiki\n---\n"
        "Cites [[sources/cited-src]] and [[concepts/target]] (path-qualified). [[hub]] too.\n"
    )

    # Duplicate stems (normalized collide): "dupe thing" twice.
    _write(wiki / "dupe-thing.md",
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
        "written_by: wiki\n---\n"
        "First dupe. [[hub]] link so it is not also an orphan in this fixture run here.\n"
    )
    _write(wiki / "Dupe_Thing.md",
        "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
        "written_by: wiki\n---\n"
        "Second dupe. [[hub]] link so it is not also an orphan in this fixture run here.\n"
    )


def build_objective_fixture(root: Path) -> None:
    """Build objective/ area fixtures with various per-type pages."""
    # Well-formed research_question
    _write(root / "objective/research_question/Q-0001-good.md",
        "---\ntype: research_question\nid: Q-0001\ncreated: 2026-06-01\n"
        "updated: 2026-06-18\nsolved: \"no\"\n"
        "related:\n  - \"[[wiki/concepts/disaggregated-inference]]\"\npriority: high\n"
        "written_by: USER\n---\n"
        "Can optical accelerators replace HBM? Links to [[wiki/concepts/hub]] "
        "and also more text here to make this page long enough body for the check.\n"
    )

    # research_question with missing required keys (missing solved, priority)
    _write(root / "objective/research_question/Q-0002-bad.md",
        "---\ntype: research_question\nid: Q-0002\ncreated: 2026-06-01\n"
        "updated: 2026-06-18\nwritten_by: USER\n---\n"
        "Missing several required keys. Enough body text here to avoid the stub check.\n"
    )

    # Well-formed decision
    _write(root / "objective/decision/D-0001-good.md",
        "---\ntype: decision\nid: D-0001\ncreated: 2026-06-01\nupdated: 2026-06-18\n"
        "status: active\nscope: all\nwritten_by: USER\n---\n"
        "Do not fetch web content. Enough text to clear stub check here.\n"
    )

    # direction with missing required key (missing serves_question)
    _write(root / "objective/direction/DIR-0001-bad.md",
        "---\ntype: direction\nid: DIR-0001\ncreated: 2026-06-01\nupdated: 2026-06-18\n"
        "status: open\npriority: high\nwritten_by: USER\n---\n"
        "A direction node missing serves_question. Enough text to pass the stub check.\n"
    )

    # Objective leaf: has outbound link but no inbound -- must NOT be flagged orphaned
    # (degree-based rule: only truly isolated nodes are orphaned in objective area).
    _write(root / "objective/direction/DIR-leaf-outbound.md",
        "---\ntype: direction\nid: DIR-leaf\ncreated: 2026-06-01\nupdated: 2026-06-18\n"
        "status: open\nserves_question: Q-0001\npriority: low\nwritten_by: backend\n---\n"
        "This direction links [[Q-0001-good]] but nothing links back to it. "
        "It is a DAG leaf, not an orphan. Enough text to pass the stub check here.\n"
    )

    # Objective isolate: no inbound AND no outbound -- MUST be flagged orphaned.
    _write(root / "objective/direction/DIR-isolated.md",
        "---\ntype: direction\nid: DIR-iso\ncreated: 2026-06-01\nupdated: 2026-06-18\n"
        "status: open\nserves_question: Q-0001\npriority: low\nwritten_by: backend\n---\n"
        "This direction has no links in or out. Truly isolated. "
        "Enough text to clear the stub check here for sure.\n"
    )


def build_research_fixture(root: Path) -> None:
    """Build research/ area fixtures with per-type pages."""
    # Well-formed research_report (snake_case type, as in real Q-NNNN files)
    _write(root / "research/Q-0001.md",
        "---\ntype: research_report\nid: Q-0001\ncreated: 2026-06-22\n"
        "updated: 2026-06-22\ngenerated_by: research\nstatus: partial-answer\n"
        "written_by: research\n---\n"
        "Deep synthesis for Q-0001. Links to [[wiki/concepts/hub]] for cross-area.\n"
        "Lots of body text here to make sure it clears the empty/stub check here.\n"
    )

    # research_report missing required keys (missing id, status)
    _write(root / "research/Q-0002-bad.md",
        "---\ntype: research_report\ncreated: 2026-06-22\nupdated: 2026-06-22\n"
        "written_by: research\n---\n"
        "Missing id and status. Enough body text to clear the stub check here.\n"
    )


def build_all_areas_fixture(root: Path) -> None:
    """Build all three areas for the cross-area tests."""
    build_wiki_fixture(root)
    build_objective_fixture(root)
    build_research_fixture(root)


# ---------------------------------------------------------------------------
# (a) Existing wiki-only checks (all must remain green)
# ---------------------------------------------------------------------------

def test_wiki_code_fence_wrapped() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert any("fenced.md" in i for i in issues["code_fence_wrapped"]), \
            "code_fence_wrapped must flag fenced.md"


def test_wiki_unfilled_template() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert any("unfilled.md" in i for i in issues["unfilled_template"]), \
            "unfilled_template must flag unfilled.md"


def test_wiki_orphan_flagged() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert any("orphan.md" in i for i in issues["orphaned"]), \
            "orphaned must flag orphan.md"


def test_wiki_dead_link_flagged() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert any("ghost-page" in i for i in issues["dead_links"]), \
            "dead_links must flag ghost-page"


def test_wiki_path_qualified_link_not_dead() -> None:
    """Path-qualified [[sources/cited-src]] must NOT produce a dead link."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert not any("cited-src" in i for i in issues["dead_links"]), \
            "path-qualified [[sources/cited-src]] must not be flagged dead"


def test_wiki_path_qualified_target_not_orphaned() -> None:
    """cited-src.md linked via path-qualified [[sources/cited-src]] must NOT be orphaned."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert not any("cited-src.md" in i for i in issues["orphaned"]), \
            "path-qualified link target cited-src.md must not be orphaned"


def test_wiki_duplicate_flagged() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert any("dupe" in i for i in issues["duplicate"]), \
            "duplicate must flag dupe-thing / Dupe_Thing"


def test_wiki_fenced_not_double_reported_as_missing_fm() -> None:
    """A fence-wrapped page must NOT also appear in missing_frontmatter."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert not any("fenced.md" in i for i in issues["missing_frontmatter"]), \
            "fenced.md must not be double-reported as missing_frontmatter"


def test_wiki_total_pages_counted() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        assert total >= 7, f"expected >= 7 non-skippable wiki pages; got {total}"


def test_render_report_produces_report() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        body, score = vh.render_report(issues, total, areas=("wiki",))
        assert "Vault health report" in body, "render_report must produce a health report header"
        assert isinstance(score, int), "score must be an int"


# ---------------------------------------------------------------------------
# (b) --area wiki reproduces wiki-only results exactly
# ---------------------------------------------------------------------------

def test_area_wiki_same_as_wiki_areas_param() -> None:
    """audit(areas=('wiki',)) and the full all-area scan with wiki filter give same issues."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)

        # Single-area audit
        issues_wiki, total_wiki = vh.audit(root, areas=("wiki",))

        # All-area audit (only wiki area exists here) -> must produce same results
        issues_all, total_all = vh.audit(root, areas=("wiki", "objective", "research"))

        # With only wiki pages present, the results must be identical
        assert total_wiki == total_all, "total pages must match when only wiki area exists"
        for key in vh.ISSUE_KEYS:
            assert len(issues_wiki[key]) == len(issues_all[key]), \
                f"issue count for '{key}' diverges: wiki={len(issues_wiki[key])} all={len(issues_all[key])}"


def test_area_wiki_cli_filter() -> None:
    """_issues_for_area filters issues to the wiki area correctly."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        # Every issue item must start with "[wiki]"
        for key, items in issues.items():
            for item in items:
                assert item.startswith("[wiki]"), \
                    f"issue '{item}' in '{key}' must be tagged [wiki]"


# ---------------------------------------------------------------------------
# (c) All-area scan surfaces objective and research issues
# ---------------------------------------------------------------------------

def test_all_areas_surfaces_objective_issues() -> None:
    """objective/ area issues must appear when scanning all areas."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))

        # Q-0002-bad is missing solved, priority
        assert any("Q-0002-bad" in i for i in issues["missing_frontmatter"]), \
            "Q-0002-bad (missing required frontmatter) must be flagged"

        # DIR-0001-bad is missing serves_question
        assert any("DIR-0001-bad" in i for i in issues["missing_frontmatter"]), \
            "DIR-0001-bad (missing serves_question) must be flagged"


def test_all_areas_surfaces_research_issues() -> None:
    """research/ area issues must appear when scanning all areas."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))

        # Q-0002-bad in research/ is missing id and status
        assert any("Q-0002-bad" in i for i in issues["missing_frontmatter"]), \
            "research/Q-0002-bad (missing id and status) must be flagged"


def test_all_areas_issue_tags() -> None:
    """Every issue item must carry an area tag [wiki], [objective], [research], or [cross-area]."""
    valid_tags = {"[wiki]", "[objective]", "[research]", "[cross-area]", "[unknown]"}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))
        for key, items in issues.items():
            for item in items:
                assert item.startswith("["), \
                    f"issue '{item}' in '{key}' must start with an area tag like [wiki]"
                tag = item[:item.index("]") + 1]
                assert tag in valid_tags, \
                    f"issue '{item}' in '{key}' has unrecognised area tag '{tag}'"


def test_all_areas_per_area_breakdown() -> None:
    """_area_breakdown returns correct per-area counts."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))
        areas = ("wiki", "objective", "research")
        breakdown = vh._area_breakdown(issues, areas)

        # Each area key is present
        for area in areas:
            assert area in breakdown, f"{area} must be in the breakdown"
            assert "total" in breakdown[area], f"{area} breakdown must have a 'total' key"

        # objective area must have at least 2 missing_frontmatter issues
        obj_mf = breakdown["objective"].get("missing_frontmatter", 0)
        assert obj_mf >= 2, \
            f"objective must have >= 2 missing_frontmatter issues; got {obj_mf}"


def test_json_output_has_per_area_breakdown() -> None:
    """JSON output from _main --json must include 'per_area' breakdown key."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)

        buf = StringIO()
        import sys as _sys
        old_stdout = _sys.stdout
        _sys.stdout = buf
        try:
            rc = vh._main(["--path", str(root), "--area", "all", "--json"])
        finally:
            _sys.stdout = old_stdout
        assert rc == 0

        data = json.loads(buf.getvalue())
        assert "per_area" in data, "JSON output must contain 'per_area' breakdown"
        assert "areas" in data, "JSON output must contain 'areas' list"
        for area in ("wiki", "objective", "research"):
            assert area in data["per_area"], f"per_area must include '{area}'"


# ---------------------------------------------------------------------------
# (d) Cross-area [[wiki/...]] link from an objective node resolves correctly
# ---------------------------------------------------------------------------

def test_cross_area_wiki_link_resolves() -> None:
    """An objective node linking [[wiki/concepts/hub]] must not produce a dead link,
    and the wiki page hub must not be orphaned due to that cross-area link.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))

        # Q-0001-good links to [[wiki/concepts/hub]]; hub exists in wiki/
        # The link must NOT be flagged as dead
        assert not any("wiki/concepts/hub" in i for i in issues["dead_links"]), \
            "[[wiki/concepts/hub]] from an objective node must not be a dead link"

        # hub.md gets a backlink from Q-0001-good, so it must NOT be orphaned
        # (unless the wiki fixture itself orphans it - check that at least the
        # cross-area backlink is counted by verifying hub is not orphaned overall)
        # In the full fixture, hub is linked from target, orphan, fenced, unfilled,
        # hub2, dupe-thing, Dupe_Thing, Q-0001-good, and Q-0001 (research) -- not orphaned.
        assert not any("wiki/concepts/hub.md" in i for i in issues["orphaned"]), \
            "hub.md must not be orphaned when linked from cross-area objective nodes"


def test_cross_area_research_link_resolves() -> None:
    """A research/ node linking [[wiki/concepts/hub]] must not produce a dead link."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))

        # research/Q-0001.md links [[wiki/concepts/hub]]
        assert not any("wiki/concepts/hub" in i for i in issues["dead_links"]), \
            "[[wiki/concepts/hub]] from a research node must not be a dead link"


# ---------------------------------------------------------------------------
# (e) Per-type required frontmatter enforcement
# ---------------------------------------------------------------------------

def test_wiki_page_missing_sources_flagged() -> None:
    """Wiki pages require {type, created, updated, sources}; missing sources is flagged."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wiki = root / "wiki" / "concepts"
        wiki.mkdir(parents=True)
        _write(wiki / "no-sources.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\n---\n"
            "A concept page with no sources key. Enough body text for stub check.\n"
        )
        _write(wiki / "hub.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
            "[[no-sources]] links back. Enough body here for all the checks in this test.\n"
        )
        issues, total = vh.audit(root, areas=("wiki",))
        assert any("no-sources.md" in i and "sources" in i for i in issues["missing_frontmatter"]), \
            "wiki concept missing 'sources' must be flagged in missing_frontmatter"


def test_research_question_missing_solved_flagged() -> None:
    """research_question type requires solved, priority; missing ones flagged."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "objective/research_question/Q-bad.md",
            "---\ntype: research_question\nid: Q-bad\ncreated: 2026-06-01\n"
            "updated: 2026-06-18\n---\n"
            "Missing solved, priority. Enough body text here.\n"
        )
        issues, total = vh.audit(root, areas=("objective",))
        mf_items = issues["missing_frontmatter"]
        assert any("Q-bad" in i for i in mf_items), \
            "Q-bad must appear in missing_frontmatter"
        # Check that at least one of the missing keys is mentioned
        q_bad_issues = [i for i in mf_items if "Q-bad" in i]
        combined = " ".join(q_bad_issues)
        assert "solved" in combined or "priority" in combined, \
            f"missing keys (solved/priority) must be mentioned; got: {q_bad_issues}"


def test_decision_missing_scope_flagged() -> None:
    """decision type requires scope; missing scope must be flagged."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "objective/decision/D-bad.md",
            "---\ntype: decision\nid: D-bad\ncreated: 2026-06-01\n"
            "updated: 2026-06-18\nstatus: active\n---\n"
            "Missing the scope key. Enough body text for the stub check.\n"
        )
        issues, total = vh.audit(root, areas=("objective",))
        assert any("D-bad" in i and "scope" in i for i in issues["missing_frontmatter"]), \
            "decision missing 'scope' must be flagged"


def test_research_report_missing_id_flagged() -> None:
    """research_report type requires id; missing id must be flagged."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "research/Q-noid.md",
            "---\ntype: research_report\ncreated: 2026-06-22\nupdated: 2026-06-22\n"
            "status: partial-answer\n---\n"
            "Missing the id field. Enough body text here to pass the stub check.\n"
        )
        issues, total = vh.audit(root, areas=("research",))
        assert any("Q-noid" in i and "id" in i for i in issues["missing_frontmatter"]), \
            "research_report missing 'id' must be flagged"


def test_unknown_type_falls_back_to_generic() -> None:
    """An unknown type falls back to {type, created, updated}; sources NOT required."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wiki = root / "wiki" / "concepts"
        wiki.mkdir(parents=True)
        _write(wiki / "unknown-typed.md",
            "---\ntype: my_custom_type\ncreated: 2026-06-01\nupdated: 2026-06-18\n---\n"
            "An unknown type that should not require 'sources'. Enough body text here.\n"
        )
        _write(wiki / "hub.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
            "[[unknown-typed]] backlink. Enough body text here for all checks.\n"
        )
        issues, total = vh.audit(root, areas=("wiki",))
        # unknown_typed should NOT be flagged for missing sources
        mf = issues["missing_frontmatter"]
        assert not any("unknown-typed" in i and "sources" in i for i in mf), \
            "unknown type must not require 'sources' (fallback = {type, created, updated})"
        # unknown_typed SHOULD NOT be flagged at all (type, created, updated all present)
        assert not any("unknown-typed" in i for i in mf), \
            "unknown-typed with {type, created, updated} must not trigger missing_frontmatter"


# ---------------------------------------------------------------------------
# (f) Degree-based orphan detection for objective and research areas
# ---------------------------------------------------------------------------

def test_objective_leaf_outbound_not_orphaned() -> None:
    """An objective page with outbound links but no inbound links must NOT be flagged orphaned.
    (Degree-based rule: only truly isolated nodes -- degree 0 -- are orphaned in
    the objective area.)
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))
        assert not any("DIR-leaf-outbound" in i for i in issues["orphaned"]), \
            "DIR-leaf-outbound has outbound links so must NOT be flagged orphaned (degree-based rule)"


def test_objective_isolated_node_orphaned() -> None:
    """An objective page with NO inbound AND NO outbound links (degree 0) IS orphaned."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))
        assert any("DIR-isolated" in i for i in issues["orphaned"]), \
            "DIR-isolated (no links either way) must be flagged orphaned"


def test_wiki_outbound_only_still_orphaned() -> None:
    """A wiki page with outbound links but no inbound IS still flagged orphaned.
    This verifies the back-compat inbound-only rule is preserved for the wiki area.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        # orphan.md links to [[hub]] (outbound) but nobody links to it (no inbound).
        # Under the wiki inbound-only rule it must still be flagged.
        assert any("orphan.md" in i for i in issues["orphaned"]), \
            "wiki orphan.md (outbound-only) must still be flagged orphaned (inbound-only rule)"


# ---------------------------------------------------------------------------
# (g) missing_written_by provenance check
# ---------------------------------------------------------------------------

def test_missing_written_by_flags_absent_key() -> None:
    """A page with no written_by key must be flagged in missing_written_by."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wiki = root / "wiki" / "concepts"
        wiki.mkdir(parents=True)
        _write(wiki / "no-wb.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
            "This page has no written_by key. Enough body text for the stub check.\n"
        )
        _write(wiki / "hub.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
            "written_by: wiki\n---\n"
            "[[no-wb]] gets a backlink here. Enough body text for all the checks here.\n"
        )
        issues, _ = vh.audit(root, areas=("wiki",))
        assert any("no-wb" in i for i in issues["missing_written_by"]), \
            "page with no written_by key must be flagged in missing_written_by"


def test_missing_written_by_flags_bad_value() -> None:
    """A page with an unrecognised written_by value must be flagged in missing_written_by."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wiki = root / "wiki" / "concepts"
        wiki.mkdir(parents=True)
        _write(wiki / "bad-wb.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
            "written_by: robot\n---\n"
            "This page has an unrecognised written_by value. Enough body text here.\n"
        )
        _write(wiki / "hub.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
            "written_by: wiki\n---\n"
            "[[bad-wb]] gets a backlink. Enough body text for all the checks here.\n"
        )
        issues, _ = vh.audit(root, areas=("wiki",))
        assert any("bad-wb" in i for i in issues["missing_written_by"]), \
            "page with unrecognised written_by must be flagged in missing_written_by"
        # The issue string must mention the invalid value
        bad_items = [i for i in issues["missing_written_by"] if "bad-wb" in i]
        assert any("robot" in i for i in bad_items), \
            f"issue string must mention the invalid value 'robot'; got: {bad_items}"


def test_valid_written_by_not_flagged() -> None:
    """A page with a valid written_by value must NOT be flagged in missing_written_by."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wiki = root / "wiki" / "concepts"
        wiki.mkdir(parents=True)
        for val in ("USER", "research", "wiki", "web", "backend"):
            _write(wiki / f"wb-{val.lower()}.md",
                f"---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
                f"written_by: {val}\n---\n"
                f"This page has written_by: {val}. Enough body text for the stub check.\n"
            )
        _write(wiki / "hub.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
            "written_by: wiki\n---\n"
            "Links to [[wb-user]] [[wb-research]] [[wb-wiki]] [[wb-web]] [[wb-backend]].\n"
            "Enough body text here to clear the stub check in this test fixture.\n"
        )
        issues, _ = vh.audit(root, areas=("wiki",))
        for val in ("USER", "research", "wiki", "web", "backend"):
            stem = f"wb-{val.lower()}"
            assert not any(stem in i for i in issues["missing_written_by"]), \
                f"written_by: {val!r} is valid; wb-{val.lower()}.md must not be flagged"


def test_missing_written_by_in_json_counts() -> None:
    """JSON output must include missing_written_by in the 'counts' dict."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wiki = root / "wiki" / "concepts"
        wiki.mkdir(parents=True)
        _write(wiki / "no-wb.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n---\n"
            "Page missing written_by. Enough body text here for the stub check.\n"
        )
        _write(wiki / "hub.md",
            "---\ntype: concept\ncreated: 2026-06-01\nupdated: 2026-06-18\nsources: []\n"
            "written_by: wiki\n---\n"
            "[[no-wb]] backlink. Enough body text here for all the checks.\n"
        )
        buf = StringIO()
        import sys as _sys
        old_stdout = _sys.stdout
        _sys.stdout = buf
        try:
            rc = vh._main(["--path", str(root), "--area", "wiki", "--json"])
        finally:
            _sys.stdout = old_stdout
        assert rc == 0
        data = json.loads(buf.getvalue())
        assert "missing_written_by" in data["counts"], \
            "JSON counts must include missing_written_by key"
        assert data["counts"]["missing_written_by"] >= 1, \
            "JSON counts['missing_written_by'] must be >= 1 for a page lacking the key"


# ---------------------------------------------------------------------------
# Multi-area render_report produces per-area sections
# ---------------------------------------------------------------------------

def test_render_report_multi_area_has_per_area_sections() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_all_areas_fixture(root)
        issues, total = vh.audit(root, areas=("wiki", "objective", "research"))
        body, score = vh.render_report(issues, total, areas=("wiki", "objective", "research"))
        assert "Per-area issue counts" in body, "multi-area report must have per-area count table"
        assert "Area: wiki" in body, "multi-area report must have wiki section"
        assert "Area: objective" in body, "multi-area report must have objective section"
        assert "Area: research" in body, "multi-area report must have research section"
        assert "advisory" in body.lower(), "report must note that score is advisory"


def test_render_report_single_area_flat_layout() -> None:
    """Single-area (wiki-only) report uses the legacy flat layout, not per-area sections."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_wiki_fixture(root)
        issues, total = vh.audit(root, areas=("wiki",))
        body, score = vh.render_report(issues, total, areas=("wiki",))
        # Should NOT have the multi-area per-area section header
        assert "Per-area issue counts" not in body, \
            "single-area report must not have 'Per-area issue counts' table"
        # Should still have report header
        assert "Vault health report" in body


# ---------------------------------------------------------------------------
# Self-running fallback (for python tests/test_vault_health.py direct execution)
# ---------------------------------------------------------------------------

def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failures.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\nFAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print(f"\nAll {len(tests)} vault_health tests passed.")
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_run_all())
