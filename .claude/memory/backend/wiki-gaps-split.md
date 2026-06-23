# wiki-gaps per-gap split -- 2026-06-23

## What was built

- `scripts/wiki_gaps_split.py`: new module; `slugify`, `parse_analysis`, `split`.
  Module docstring declares this as the writer; `web_decision.parse_gaps` is the reader.
- `scripts/templates/gap_template.md`: canonical gap file template (copied to
  `wiki/gap/_template.md` on first vault run by the skill).
- `tests/test_wiki_gaps_split.py`: 39 hermetic tests (parse_analysis, slugify,
  split dry-run and apply, idempotency).
- `scripts/wiki_gaps_fill.py`: `build_gaps_frontmatter` marked DEPRECATED; module
  docstring updated to show the new pipeline (fill -> LLM -> split).
- `.claude/skills/wiki-gaps/SKILL.md`: full rewrite. Step 5 now pipes to
  `wiki_gaps_split.py --apply --stdin`; locks wiki/gap/index.md (not gaps.md);
  Step 5b drops _template.md on first run; log entry links to [[wiki/gap/index]].

## Pre-existing partial work found (NOT my changes)

Working tree already had partial per-gap migration across 7 files:
- `scripts/web_decision.py`: `parse_gaps` signature changed from `(gaps_md_text)` to
  `(gap_dir)`, reads per-gap files, uses new `_parse_gap_frontmatter_list` helper.
- `scripts/web_crawl.py`: `_relevance_links` now links to specific gap files or
  `[[wiki/gap/index]] (GAP-NN)` fallback.
- `agents/ingest_index.py`, `tests/test_ingest_index_columns.py`,
  `tests/test_web_decision.py`: related updates (not yet fully traced).
- `tests/test_web_crawl.py`: `test_relevance_field_present` updated from
  `[[wiki/gaps]]` to `[[wiki/gap/` (my fix to match new behavior).
All 814 tests green (tests pass individually and in full suite).

## Schema

Gap file path: `wiki/gap/GAP-NN-<slug>.md`
Index path: `wiki/gap/index.md`
Template: `scripts/templates/gap_template.md` -> vault `wiki/gap/_template.md`

Slug: lowercase, alnum->hyphen, collapse, strip, max 45 chars.
Frontmatter: type:gap, id, title (quoted), status:open, topics (YAML list),
fillable_by (YAML list, bare engine tags), priority (one word),
shows_up_in (YAML list of wikilinks), created, updated, written_by:wiki.
