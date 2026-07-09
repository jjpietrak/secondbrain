# Web-scrape fix Phase 3 -- /web-backfill command (2026-07-09, not committed)

New on-demand command that fills KNOWN gaps first: papers already CITED in the wiki
(arXiv/DOI links) but not yet truly ingested. Separate from `web-scrape` (NOT folded in).

## Files created (worktree gracious-boyd-4d9ea4)
- `scripts/web_backfill.py` -- standalone command. Key functions:
  - `harvest_cited_ids(vault_root)` -- scan wiki/**/*.md for `arxiv.org/(abs|pdf)/<id>`
    (version stripped) + DOIs (`doi.org/<doi>` or bare `10.NNNN/...`); returns
    id -> {id_type, pages:set}.
  - `gap_referenced_pages(vault_root)` -- parse each OPEN gap's `## Shows up in` body
    (reuses web_decision._parse_frontmatter + _parse_section) -> page -> {GAP ids}.
  - `_ingest_rows` / `ingest_status_map` -- reuse `web_crawl._load_ingest_rows` but wrap
    in `except BaseException` + direct-file fallback (that helper raises SystemExit, which
    it does NOT catch, for vaults unregistered in vault_config -- needed for hermetic tests).
  - `build_backfill(vault_root)` -- TRUTH-CHECK: truly-ingested = status `ingested` AND
    filename exists on disk (status alone lies). pending/waiting_approval = already-queued;
    rejected = sticky-skip; everything else (incl. `deleted`, not-in-index) = backfill set.
    Ordered gap-signal first (cited on a gap `## Shows up in` page), then wiki-only, then id.
  - `fetch_arxiv_by_id(arxiv_id, timeout=20)` -- minimal by-id fetch (arXiv Atom
    `?id_list=`); bounded; BYPASSES seen.json (never calls dedup_seen).
  - `_write_backfill_report` -- reuses web_crawl `_CANDIDATE_BLOCK`/`_REPORT_INSTRUCTION`/
    `_REPORT_FOOTER`/`_content_summary`/`_relevance_paragraph`/`_relevance_links` so
    `report_approve.parse_candidates` parses `meta/nightly_report/backfill-<date>.md`.
  - `run(...)` -- dry_run writes nothing (prints would-stage); live run fetches + enqueues
    (`ii.enqueue(..., discovered_by="web", objective_ids=[GAP ids], name=vault_name)`) +
    writes report. CLI: `--vault --dry-run --json --limit N(=10) --explain`.
- `.claude/skills/web-backfill/SKILL.md` -- owner `web`; FU2 dispatch header (same pattern as
  web-scrape), command invocation, truth-check semantics, approve via `/wiki-approve`. Trigger
  phrases: "/web-backfill", "backfill", "fill known gaps", "ingest cited papers".
- `tests/test_web_backfill.py` -- 5 offline tests (tiny fixtures): arxiv abs/pdf + DOI harvest;
  truth-check EXCLUDES Splitwise 2311.18677 (status=ingested + file present) and INCLUDES a
  status=ingested-but-file-MISSING id + a not-in-index id; gap-signal orders first; seen.json
  bypass (monkeypatch dedup_seen to raise -> not called); dry-run writes nothing. All green.

## Live demo (Inference-Disagg, --dry-run --explain --limit 10)
- 16 cited arxiv ids found; 11 truly ingested (EXCLUDED incl. Splitwise 2311.18677 status=
  ingested + file present); BACKFILL SET = 5: 2308.16369 (sarathi), 2504.09775 (mist),
  2509.17357 (cronus), 2509.17542 (zte-multivendor-pd), 2510.08544 (spad) -- all prior
  status `deleted` in the index (PDFs on disk but index marked deleted -> not truly ingested).
- NOTE: the gap-named-but-UNLINKED papers (optical prior-art LightML/Demirkiran/etc.,
  vLLM/SGLang/llm-d/Dynamo) do NOT appear -- they are named in gap `## Missing` text but have
  NO arxiv/DOI link anywhere in the wiki, so backfill (id-driven) cannot reach them; they need
  `web-scrape` WebSearch discovery, not backfill. Backfill only fills LINKED cited ids.
- No live staging run performed (would mutate live vault + hit arxiv); report writer verified
  offline against report_approve.parse_candidates.

## Not done / follow-on
- Live staging + real `/wiki-approve` round-trip not exercised (dry-run only, per task).
- The 5 `deleted`-status ids re-staging is per the strict truth-check spec; if `deleted` should
  count as decided-and-skip, that is a policy change to raise with the user (not assumed here).
