#!/usr/bin/env python3
"""Hermetic tests for scripts/wiki_gaps_split.py.

No LLM, no network, no real vault.  $0.

Covers:
  slugify
  - basic lowercase + alphanumeric-to-hyphen conversion
  - collapse multiple separators
  - strip leading/trailing hyphens
  - truncate to max_len

  parse_analysis
  - gaps list: id, title, shows_up_in (split on ';'), missing, fillable_by (bare tags),
    topics (T-NNNN list), priority (first word), why (remainder after priority word)
  - multi-topic + multi-tag fillable_by + priority-with-note
  - Coverage Map, Stale, Self-contained, Open-Question Harvest sections extracted
  - robust to missing sections (returns empty string / empty list)

  split(apply=False)
  - returns planned files dict, writes NOTHING to disk
  - files list contains one entry per gap; index entry present
  - gap file content has correct frontmatter fields (type:gap, id, status:open,
    topics as YAML list, fillable_by as YAML list, priority, shows_up_in as YAML list)
  - gap file has ## Missing section with missing text
  - index has coverage map content
  - index has a Gap files table linking each gap

  split(apply=True)
  - writes wiki/gap/GAP-NN-<slug>.md files under vault_root
  - writes wiki/gap/index.md under vault_root
  - idempotent: second apply preserves created: and bumps updated:
  - apply creates wiki/gap/ directory if absent
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.wiki_gaps_split import slugify, parse_analysis, split  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_frontmatter(content: str) -> str:
    """Return the raw YAML text between the first pair of --- fences."""
    lines = content.split("\n")
    if lines[0].strip() != "---":
        raise ValueError("Content does not start with ---")
    end = next(i for i, l in enumerate(lines[1:], 1) if l.strip() == "---")
    return "\n".join(lines[1:end])


# ---------------------------------------------------------------------------
# Fixture gap-analysis markdown
# ---------------------------------------------------------------------------

FIXTURE_ANALYSIS = textwrap.dedent("""\
    ## Open-Question Harvest (TOP PRIORITY)
    - What is the achievable HBM bandwidth utilization at batch size 1? [[wiki/concepts/hbm-bandwidth]]
    - Is there a roofline model for H100 decode phase? [[wiki/concepts/hbm-bandwidth]]

    ## Coverage Map
    - T-0001: thin -- only [[wiki/concepts/latency-floor]] covers decode latency
    - T-0006: sparse -- Photons-to-Tokens source ingested but citations missing

    ## Knowledge Gaps
    ### GAP-01: HBM bandwidth utilization measurement at batch-size-1
    - shows_up_in: [[wiki/concepts/hbm-bandwidth]]; [[wiki/entities/nvidia-h100]]
    - missing: a benchmarked HBM utilization figure at batch size 1 on H100
    - fillable_by: arxiv | web (recent GPU benchmarks)
    - topic: T-0001, T-0002
    - priority: high  -  appears directly in Open-Question Harvest

    ### GAP-02: Roofline model for H100 decode phase
    - shows_up_in: [[wiki/concepts/hbm-bandwidth]]
    - missing: a published roofline analysis for the H100 decode phase
    - fillable_by: arxiv
    - topic: T-0002
    - priority: medium  -  flagged in Open Questions but less urgent than GAP-01

    ### GAP-08: Optical prior art -- citations [3]-[6] from Photons to Tokens not ingested
    - shows_up_in: [[wiki/sources/photons-to-tokens]]
    - missing: the 4 cited prior-art papers are not yet ingested as wiki/sources/ entries
    - fillable_by: arxiv | web | github
    - topic: T-0006
    - priority: low  -  informational only, no active research question depends on it

    ## Stale / Unverified
    - The HBM3 bandwidth figure (3.35 TB/s) needs re-verification [[wiki/concepts/hbm-bandwidth]]

    ## Self-contained (no external source needed)
    - none

    READY
""")


# ---------------------------------------------------------------------------
# Tests: slugify
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert slugify("HBM bandwidth") == "hbm-bandwidth"


def test_slugify_special_chars():
    assert slugify("Optical prior art -- citations [3]-[6]") == "optical-prior-art-citations-3-6"


def test_slugify_collapse_separators():
    slug = slugify("foo   bar___baz")
    assert "--" not in slug
    assert slug == "foo-bar-baz"


def test_slugify_strip_edges():
    slug = slugify("  --leading trailing--  ")
    assert not slug.startswith("-")
    assert not slug.endswith("-")


def test_slugify_truncate():
    long_title = "a" * 100
    slug = slugify(long_title, max_len=45)
    assert len(slug) <= 45


def test_slugify_title_from_spec():
    """Match the example from the pinned schema."""
    title = "Optical prior art -- citations [3]-[6] from Photons to Tokens not ingested"
    slug = slugify(title)
    assert len(slug) <= 45
    assert slug.startswith("optical-prior-art")


# ---------------------------------------------------------------------------
# Tests: parse_analysis
# ---------------------------------------------------------------------------

def test_parse_analysis_gap_count():
    result = parse_analysis(FIXTURE_ANALYSIS)
    assert len(result["gaps"]) == 3


def test_parse_analysis_gap_ids():
    result = parse_analysis(FIXTURE_ANALYSIS)
    ids = [g["id"] for g in result["gaps"]]
    assert "GAP-01" in ids
    assert "GAP-02" in ids
    assert "GAP-08" in ids


def test_parse_analysis_gap_titles():
    result = parse_analysis(FIXTURE_ANALYSIS)
    g01 = next(g for g in result["gaps"] if g["id"] == "GAP-01")
    assert "HBM bandwidth utilization" in g01["title"]


def test_parse_analysis_shows_up_in_split_on_semicolon():
    """shows_up_in with ';' separator yields a list of two entries."""
    result = parse_analysis(FIXTURE_ANALYSIS)
    g01 = next(g for g in result["gaps"] if g["id"] == "GAP-01")
    assert len(g01["shows_up_in"]) == 2
    assert "[[wiki/concepts/hbm-bandwidth]]" in g01["shows_up_in"]
    assert "[[wiki/entities/nvidia-h100]]" in g01["shows_up_in"]


def test_parse_analysis_fillable_by_bare_tags():
    """fillable_by strips parenthetical notes and returns bare engine tags."""
    result = parse_analysis(FIXTURE_ANALYSIS)
    g01 = next(g for g in result["gaps"] if g["id"] == "GAP-01")
    assert g01["fillable_by"] == ["arxiv", "web"]


def test_parse_analysis_fillable_by_multi_tag():
    """GAP-08 has three tags: arxiv, web, github."""
    result = parse_analysis(FIXTURE_ANALYSIS)
    g08 = next(g for g in result["gaps"] if g["id"] == "GAP-08")
    assert set(g08["fillable_by"]) == {"arxiv", "web", "github"}


def test_parse_analysis_topics_list():
    """GAP-01 has two topics: T-0001, T-0002."""
    result = parse_analysis(FIXTURE_ANALYSIS)
    g01 = next(g for g in result["gaps"] if g["id"] == "GAP-01")
    assert "T-0001" in g01["topics"]
    assert "T-0002" in g01["topics"]


def test_parse_analysis_priority_first_word():
    """Priority is just the first word (high/medium/low), no trailing text."""
    result = parse_analysis(FIXTURE_ANALYSIS)
    g01 = next(g for g in result["gaps"] if g["id"] == "GAP-01")
    assert g01["priority"] == "high"
    g02 = next(g for g in result["gaps"] if g["id"] == "GAP-02")
    assert g02["priority"] == "medium"
    g08 = next(g for g in result["gaps"] if g["id"] == "GAP-08")
    assert g08["priority"] == "low"


def test_parse_analysis_why_extracted():
    """'why' is the text after the priority word (stripped of separators)."""
    result = parse_analysis(FIXTURE_ANALYSIS)
    g01 = next(g for g in result["gaps"] if g["id"] == "GAP-01")
    assert "Open-Question Harvest" in g01["why"]


def test_parse_analysis_missing_text():
    result = parse_analysis(FIXTURE_ANALYSIS)
    g01 = next(g for g in result["gaps"] if g["id"] == "GAP-01")
    assert "HBM utilization" in g01["missing"]


def test_parse_analysis_coverage_map():
    result = parse_analysis(FIXTURE_ANALYSIS)
    assert "T-0001" in result["coverage_map"]
    assert "latency-floor" in result["coverage_map"]


def test_parse_analysis_stale():
    result = parse_analysis(FIXTURE_ANALYSIS)
    assert "3.35 TB/s" in result["stale"]


def test_parse_analysis_self_contained():
    result = parse_analysis(FIXTURE_ANALYSIS)
    # The fixture has "- none" as a bullet; check the content is present and READY stripped
    assert "none" in result["self_contained"]
    assert "READY" not in result["self_contained"]


def test_parse_analysis_open_questions():
    result = parse_analysis(FIXTURE_ANALYSIS)
    assert "batch size 1" in result["open_questions"]
    assert "roofline model" in result["open_questions"]


def test_parse_analysis_missing_sections_robust():
    """parse_analysis returns empty strings when sections are absent."""
    minimal = textwrap.dedent("""\
        ## Knowledge Gaps
        ### GAP-01: A simple gap
        - shows_up_in: [[wiki/page]]
        - missing: something missing
        - fillable_by: web
        - topic: T-0001
        - priority: medium
    """)
    result = parse_analysis(minimal)
    assert len(result["gaps"]) == 1
    assert result["coverage_map"] == ""
    assert result["stale"] == ""
    assert result["self_contained"] == ""
    assert result["open_questions"] == ""


def test_parse_analysis_no_knowledge_gaps():
    """parse_analysis without ## Knowledge Gaps returns empty gaps list."""
    no_gaps = textwrap.dedent("""\
        ## Coverage Map
        - T-0001: ok

        ## Stale / Unverified
        - nothing

        READY
    """)
    result = parse_analysis(no_gaps)
    assert result["gaps"] == []
    assert "T-0001" in result["coverage_map"]


# ---------------------------------------------------------------------------
# Tests: split(apply=False)
# ---------------------------------------------------------------------------

def test_split_dry_run_writes_nothing():
    """apply=False must not write any files."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        result = split(FIXTURE_ANALYSIS, vault, apply=False, today="2026-06-23")

        # vault/wiki/gap/ should NOT have been created
        gap_dir = vault / "wiki" / "gap"
        assert not gap_dir.exists(), "Dry-run must not create directories"

    assert "files" in result
    assert "index" in result


def test_split_dry_run_returns_three_gap_files():
    result = split(FIXTURE_ANALYSIS, "/tmp/nonexistent_vault_dryrun", apply=False, today="2026-06-23")
    assert len(result["files"]) == 3


def test_split_dry_run_gap_paths_contain_id_and_slug():
    result = split(FIXTURE_ANALYSIS, "/tmp/nonexistent_vault_dryrun", apply=False, today="2026-06-23")
    paths = [f["path"] for f in result["files"]]
    assert any("GAP-01" in p for p in paths)
    assert any("GAP-08" in p for p in paths)
    # slugs should appear
    assert any("hbm-bandwidth-utilization" in p for p in paths)


def test_split_dry_run_index_path():
    result = split(FIXTURE_ANALYSIS, "/tmp/nonexistent_vault_dryrun", apply=False, today="2026-06-23")
    assert result["index"]["path"].endswith("index.md")
    assert "wiki/gap" in result["index"]["path"].replace("\\", "/")


def test_split_dry_run_gap_file_frontmatter():
    """Gap file frontmatter has type:gap, id, status:open, topics/fillable_by/priority."""
    result = split(FIXTURE_ANALYSIS, "/tmp/nonexistent_vault_dryrun", apply=False, today="2026-06-23")
    gap01 = next(f for f in result["files"] if "GAP-01" in f["path"])
    content = gap01["content"]

    assert "type: gap" in content
    assert "id: GAP-01" in content
    assert "status: open" in content
    # topics is a YAML list (block or flow style -- just check key + values present)
    assert "topics:" in content
    assert "T-0001" in content
    assert "T-0002" in content
    # fillable_by is a YAML list (block or flow style)
    assert "fillable_by:" in content
    assert "arxiv" in content
    assert "priority: high" in content
    assert "written_by: wiki" in content


def test_split_dry_run_gap_file_missing_section():
    """Gap file has ## Missing section with the missing text."""
    result = split(FIXTURE_ANALYSIS, "/tmp/nonexistent_vault_dryrun", apply=False, today="2026-06-23")
    gap01 = next(f for f in result["files"] if "GAP-01" in f["path"])
    content = gap01["content"]
    assert "## Missing" in content
    assert "HBM utilization" in content


def test_split_dry_run_index_has_coverage_map():
    result = split(FIXTURE_ANALYSIS, "/tmp/nonexistent_vault_dryrun", apply=False, today="2026-06-23")
    index_content = result["index"]["content"]
    assert "Coverage Map" in index_content
    assert "T-0001" in index_content


def test_split_dry_run_index_has_gap_table():
    """Index contains a ## Gap files table with links to each gap file."""
    result = split(FIXTURE_ANALYSIS, "/tmp/nonexistent_vault_dryrun", apply=False, today="2026-06-23")
    index_content = result["index"]["content"]
    assert "## Gap files" in index_content
    assert "GAP-01" in index_content
    assert "GAP-02" in index_content
    assert "GAP-08" in index_content
    # table links use [[wiki/gap/...]] format
    assert "[[wiki/gap/GAP-" in index_content


def test_split_dry_run_index_frontmatter():
    result = split(FIXTURE_ANALYSIS, "/tmp/nonexistent_vault_dryrun", apply=False, today="2026-06-23")
    index_content = result["index"]["content"]
    assert "type: gaps_index" in index_content
    assert "written_by: wiki" in index_content
    # Date may be YAML-quoted ('2026-06-23') or bare; check key + value separately
    assert "updated:" in index_content
    assert "2026-06-23" in index_content


# ---------------------------------------------------------------------------
# Tests: split(apply=True)
# ---------------------------------------------------------------------------

def test_split_apply_creates_files():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        result = split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-23")

    assert "written" in result
    assert len(result["written"]) == 4  # 3 gaps + 1 index


def test_split_apply_gap_files_exist_on_disk():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-23")

        gap_dir = vault / "wiki" / "gap"
        assert gap_dir.is_dir()
        files = list(gap_dir.iterdir())
        # 3 gap files + 1 index
        assert len(files) == 4


def test_split_apply_gap_file_content():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-23")

        gap_dir = vault / "wiki" / "gap"
        gap01_files = list(gap_dir.glob("GAP-01-*.md"))
        assert len(gap01_files) == 1, "Expected exactly one GAP-01-*.md file"

        content = gap01_files[0].read_text(encoding="utf-8")
        assert "type: gap" in content
        assert "id: GAP-01" in content
        assert "status: open" in content
        assert "topics:" in content
        assert "T-0001" in content
        assert "fillable_by:" in content
        assert "arxiv" in content
        assert "priority: high" in content
        assert "## Missing" in content
        assert "HBM utilization" in content


def test_split_apply_index_content():
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-23")

        index_path = vault / "wiki" / "gap" / "index.md"
        assert index_path.exists()
        content = index_path.read_text(encoding="utf-8")
        assert "type: gaps_index" in content
        assert "Coverage Map" in content
        assert "## Gap files" in content
        assert "GAP-01" in content


def test_split_apply_idempotent_preserves_created():
    """Second apply must preserve created: and bump updated:."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()

        # First apply on day 1
        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-22")

        gap_dir = vault / "wiki" / "gap"
        gap01_files = list(gap_dir.glob("GAP-01-*.md"))
        assert len(gap01_files) == 1
        content_first = gap01_files[0].read_text(encoding="utf-8")
        # Date may be YAML-quoted ('2026-06-22') or bare; check key + value separately
        assert "created:" in content_first
        assert "2026-06-22" in content_first

        # Second apply on day 2 (different today)
        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-23")

        content_second = gap01_files[0].read_text(encoding="utf-8")
        fm_second = yaml.safe_load(_extract_frontmatter(content_second))
        # created: must still be the original date (round-tripped through yaml cleanly)
        assert fm_second["created"] == "2026-06-22", (
            "Second apply must preserve the original created: date"
        )
        # updated: must be bumped to the new date
        assert fm_second["updated"] == "2026-06-23", (
            "Second apply must bump updated: to the new date"
        )


def test_split_apply_idempotent_index_preserves_created():
    """Second apply preserves index.md created: date."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()

        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-22")
        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-23")

        index_path = vault / "wiki" / "gap" / "index.md"
        content = index_path.read_text(encoding="utf-8")
        fm = yaml.safe_load(_extract_frontmatter(content))
        assert fm["created"] == "2026-06-22"
        assert fm["updated"] == "2026-06-23"


def test_split_apply_creates_directory():
    """split creates wiki/gap/ if it does not exist."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        # Do NOT pre-create wiki/gap/
        assert not (vault / "wiki" / "gap").exists()

        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-23")
        assert (vault / "wiki" / "gap").is_dir()


def test_split_apply_does_not_delete_other_files():
    """split(apply=True) does not remove files it did not generate."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        gap_dir = vault / "wiki" / "gap"
        gap_dir.mkdir(parents=True)

        # Pre-existing file not in the analysis
        extra = gap_dir / "GAP-99-unrelated.md"
        extra.write_text("---\ntype: gap\nid: GAP-99\n---\n", encoding="utf-8")

        split(FIXTURE_ANALYSIS, vault, apply=True, today="2026-06-23")

        # The extra file must still be there
        assert extra.exists(), "split must not delete files it did not generate"


# ---------------------------------------------------------------------------
# Tests: YAML round-trip safety (quotes, colons, wikilinks in values)
# ---------------------------------------------------------------------------

# A fixture whose title contains both a double-quote AND a colon, and whose
# shows_up_in items contain both double-quotes and [[wikilinks]].
FIXTURE_QUOTES = textwrap.dedent("""\
    ## Knowledge Gaps
    ### GAP-01: Optical prior art: "Photons to Tokens" citations
    - shows_up_in: [[wiki/sources/photons-to-tokens]] Open Questions; [[wiki/concepts/free-space-optics]] references ... as "cited optical-AI prior art"
    - missing: the 4 cited prior-art papers are not yet ingested
    - fillable_by: arxiv | web
    - topic: T-0006
    - priority: low  -  informational only

    ## Coverage Map
    - T-0006: sparse
""")

_EXPECTED_TITLE = 'Optical prior art: "Photons to Tokens" citations'
_EXPECTED_SHOWS_UP_IN = [
    "[[wiki/sources/photons-to-tokens]] Open Questions",
    '[[wiki/concepts/free-space-optics]] references ... as "cited optical-AI prior art"',
]


def test_yaml_roundtrip_gap_file_frontmatter():
    """Gap file frontmatter with embedded double-quotes and colons round-trips safely."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        split(FIXTURE_QUOTES, vault, apply=True, today="2026-06-23")

        gap_dir = vault / "wiki" / "gap"
        gap_files = list(gap_dir.glob("GAP-01-*.md"))
        assert len(gap_files) == 1, "Expected exactly one GAP-01-*.md file"

        content = gap_files[0].read_text(encoding="utf-8")

        # Extract and parse frontmatter
        fm_text = _extract_frontmatter(content)
        fm = yaml.safe_load(fm_text)

        assert isinstance(fm, dict), "Frontmatter must parse to a dict"
        assert fm["title"] == _EXPECTED_TITLE, (
            f"Title round-trip failed: {fm['title']!r}"
        )
        assert fm["shows_up_in"] == _EXPECTED_SHOWS_UP_IN, (
            f"shows_up_in round-trip failed: {fm['shows_up_in']!r}"
        )


def test_yaml_roundtrip_index_frontmatter():
    """index.md frontmatter always round-trips safely through yaml.safe_load."""
    with tempfile.TemporaryDirectory() as tmp_str:
        vault = Path(tmp_str) / "vault"
        vault.mkdir()
        split(FIXTURE_QUOTES, vault, apply=True, today="2026-06-23")

        index_path = vault / "wiki" / "gap" / "index.md"
        assert index_path.exists()
        content = index_path.read_text(encoding="utf-8")

        fm_text = _extract_frontmatter(content)
        fm = yaml.safe_load(fm_text)

        assert isinstance(fm, dict), "Index frontmatter must parse to a dict"
        assert fm["type"] == "gaps_index"
        assert fm["written_by"] == "wiki"
        assert fm["created"] == "2026-06-23"
        assert fm["updated"] == "2026-06-23"


# ---------------------------------------------------------------------------
# Tests: ## Shows up in body section (Obsidian graph edges)
# ---------------------------------------------------------------------------

# Fixture: two shows_up_in entries, each with a [[wiki/...]] wikilink + trailing text.
FIXTURE_SHOWS_UP_IN = textwrap.dedent("""\
    ## Knowledge Gaps
    ### GAP-01: Missing optical prior art
    - shows_up_in: [[wiki/sources/photons-to-tokens]] Open Questions; [[wiki/concepts/free-space-optics]] cited optical-AI prior art
    - missing: the 4 cited papers are not ingested
    - fillable_by: arxiv
    - topic: T-0006
    - priority: low  -  informational only

    ## Coverage Map
    - T-0006: sparse
""")


def test_shows_up_in_body_section_two_bullets():
    """A gap with 2 shows_up_in entries produces a ## Shows up in section with 2 bullets."""
    result = split(FIXTURE_SHOWS_UP_IN, "/tmp/nonexistent_vault_sui", apply=False, today="2026-06-23")
    gap01 = next(f for f in result["files"] if "GAP-01" in f["path"])
    content = gap01["content"]

    assert "## Shows up in" in content, "Body must contain ## Shows up in section"
    assert "[[wiki/sources/photons-to-tokens]]" in content, (
        "Wikilink from first shows_up_in entry must appear in body"
    )
    assert "[[wiki/concepts/free-space-optics]]" in content, (
        "Wikilink from second shows_up_in entry must appear in body"
    )
    # Both bullets should use ' -- ' separator
    assert "- [[wiki/sources/photons-to-tokens]] -- Open Questions" in content
    assert "- [[wiki/concepts/free-space-optics]] -- cited optical-AI prior art" in content


def test_shows_up_in_body_is_in_body_not_only_frontmatter():
    """The wikilinks must appear AFTER the frontmatter closing ---, not just inside it."""
    result = split(FIXTURE_SHOWS_UP_IN, "/tmp/nonexistent_vault_sui", apply=False, today="2026-06-23")
    gap01 = next(f for f in result["files"] if "GAP-01" in f["path"])
    content = gap01["content"]

    # Split on the closing --- of frontmatter
    parts = content.split("---", 2)
    # parts[0] = empty, parts[1] = YAML frontmatter body, parts[2] = markdown body
    assert len(parts) >= 3, "Content must have frontmatter fences"
    body = parts[2]
    assert "[[wiki/sources/photons-to-tokens]]" in body, (
        "Wikilinks must appear in the markdown body (after frontmatter), not only in frontmatter"
    )


def test_shows_up_in_plain_bullet_when_no_wikilink():
    """An entry with no [[...]] wikilink is rendered as a plain bullet without crashing."""
    plain_entry_fixture = textwrap.dedent("""\
        ## Knowledge Gaps
        ### GAP-01: Plain entry gap
        - shows_up_in: some page without a wikilink
        - missing: something
        - fillable_by: web
        - topic: T-0001
        - priority: medium

        ## Coverage Map
        - T-0001: ok
    """)
    result = split(plain_entry_fixture, "/tmp/nonexistent_vault_plain", apply=False, today="2026-06-23")
    gap01 = next(f for f in result["files"] if "GAP-01" in f["path"])
    content = gap01["content"]

    assert "## Shows up in" in content
    # Plain bullet: no [[ prefix, just the text
    assert "- some page without a wikilink" in content


def test_shows_up_in_empty_omits_section():
    """A gap with no shows_up_in field must NOT produce a ## Shows up in section."""
    # When shows_up_in is absent entirely, the section is omitted.
    no_shows_up_in = textwrap.dedent("""\
        ## Knowledge Gaps
        ### GAP-01: No source gap
        - missing: something missing
        - fillable_by: web
        - topic: T-0001
        - priority: medium

        ## Coverage Map
        - T-0001: ok
    """)
    result = split(no_shows_up_in, "/tmp/nonexistent_vault_empty_sui", apply=False, today="2026-06-23")
    assert len(result["files"]) == 1
    content = result["files"][0]["content"]
    assert "## Shows up in" not in content, (
        "Empty shows_up_in must not produce a ## Shows up in section"
    )


def test_shows_up_in_frontmatter_still_present():
    """Frontmatter shows_up_in list is preserved alongside the body section."""
    result = split(FIXTURE_SHOWS_UP_IN, "/tmp/nonexistent_vault_fm", apply=False, today="2026-06-23")
    gap01 = next(f for f in result["files"] if "GAP-01" in f["path"])
    content = gap01["content"]

    fm_text = _extract_frontmatter(content)
    fm = yaml.safe_load(fm_text)

    assert "shows_up_in" in fm, "shows_up_in key must remain in frontmatter"
    assert isinstance(fm["shows_up_in"], list), "shows_up_in frontmatter value must be a list"
    assert len(fm["shows_up_in"]) == 2, "Both entries must be preserved in frontmatter"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
