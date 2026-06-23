# Gap readers migration -- 2026-06-23

## What changed
Migrated gap readers from the single `wiki/gaps.md` file format to per-gap files
`wiki/gap/GAP-NN-<slug>.md` with YAML frontmatter.

## New parse_gaps signature
```python
parse_gaps(gap_dir: str, *, trace=None) -> list[dict]
```
- `gap_dir` = `<vault>/wiki/gap`
- Globs `GAP-*.md`, skips `_template.md` and `index.md`
- Only includes `status: open` (missing status treated as open)
- Output dict shape UNCHANGED: `{id, title, shows_up_in, missing, fillable_by, topics, priority}`
  - `missing` = `## Missing` body text
  - `shows_up_in` = frontmatter list joined as comma-separated string
  - `fillable_by` = list of engine tags extracted via `_FILLABLE_TAG_RE`
  - `topics` = list of T-NNNN ids from frontmatter
  - `priority` = first word (high/medium/low) from frontmatter

## build_plan update
- Reads from `vault_path / "wiki" / "gap"` (was `vault_path / "wiki" / "gaps.md"`)
- Note: `f"parsed {n} gaps from wiki/gap/"` (was `from wiki/gaps.md`)
- Missing dir -> 0 gaps + `"wiki/gap/ not found -- no gaps loaded"` (no crash)

## _relevance_links GAP resolution (both files updated)
- Files: `agents/ingest_index.py` + `scripts/web_crawl.py` (inline copy)
- New logic: GLOB `<vault>/wiki/gap/GAP-##-*.md`
  - Found -> `[[wiki/gap/<stem>]]`
  - Not found -> `[[wiki/gap/index]] (GAP-##)` (was `[[wiki/gaps]] (GAP-##)`)

## Tests
- `tests/test_web_decision.py`: 115 passed
- `tests/test_ingest_index_columns.py`: 28 passed
- `tests/test_web_crawl.py`: 103 passed
- Full suite: 814 passed
