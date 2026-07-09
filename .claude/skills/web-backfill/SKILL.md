---
name: web-backfill
description: >
  Fill KNOWN gaps first: stage papers already CITED in the wiki (arXiv / DOI links) but not
  yet truly ingested, before any exploratory crawl. Scans wiki/**/*.md + open gap "## Shows up
  in" pages for cited ids, truth-checks them against the ingest index AND raw/ on disk, and
  stages the cited-but-missing set as waiting_approval with a backfill report. Free APIs, $0.
allowed-tools: Read, Write, Glob, Grep, Bash, WebFetch
---

**Ownership: `web` agent.** If you are NOT the `web` subagent (e.g. the main orchestrator or
another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: web`,
pass the user's full request, let the web agent run the steps below, and relay its result.
Do NOT run the steps yourself - running as the `web` agent is what activates the RBAC/write-scope
boundary. If you ARE the `web` agent, proceed.

# web-backfill: fill KNOWN cited-but-missing papers first

Owner: **web**. Stages papers the vault ALREADY cites (arXiv / DOI links in `wiki/**/*.md`)
but has not truly ingested, as `waiting_approval` in the ingest index plus an approval report
`meta/nightly_report/backfill-<date>.md`. This is the "known gaps first, then explore" step:
run it BEFORE the exploratory `web-scrape` crawl to close the papers the wiki already wants.

It is a **separate, on-demand command** -- NOT folded into the nightly `web-scrape` crawl.
Free-tier, $0 marginal: it reads the vault + fetches arXiv metadata by id (no paid engines,
no LLM calls).

## When to use

- Trigger phrases: "/web-backfill", "backfill", "fill known gaps", "ingest cited papers".
- After the wiki grows new pages that cite papers by arXiv/DOI link but the papers were never
  ingested (e.g. a source page written from a secondary summary, a prior-art citation list).
- Before `web-scrape`: close the cited-but-missing set first so the exploratory budget is spent
  on genuinely NEW discovery.

## Task-start protocol (MANDATORY)

1. **Decisions (hard constraints):**
   `.venv/bin/python -m agents.objectives --json decisions` -- honor every decision with
   `scope: all` or `scope: web` (cost caps, no-web-proxy rules).
2. **Memory:** read `.claude/memory/web/MEMORY.md` + referenced facts (prior approvals/rejections).

## Command

```bash
# Resolve the active vault first:
eval "$(.venv/bin/python -m agents.vault_config env)"

# Preview only (writes nothing; prints what WOULD stage) -- recommended first:
.venv/bin/python scripts/web_backfill.py --vault "$VAULT_ROOT" --dry-run --explain --limit 10

# Real run -- enqueues waiting_approval + writes meta/nightly_report/backfill-<date>.md:
.venv/bin/python scripts/web_backfill.py --vault "$VAULT_ROOT" --limit 10
```

Flags: `--dry-run` (write nothing, print the would-stage set), `--json` (machine-readable
result to stdout), `--limit N` (max candidates to stage, default 10), `--explain` (decision
trace to stderr: cited ids, truly-ingested excluded, already-queued excluded, backfill set).

## What it does internally

1. **HARVEST cited ids.** Scan `wiki/**/*.md` for `arxiv.org/(abs|pdf)/<id>` links (version
   suffix stripped, consistent with `web_harvest.candidate_ident`) and DOIs (`doi.org/<doi>`
   or a bare `10.NNNN/...`). Each OPEN gap's `## Shows up in` body section names wiki pages;
   ids cited on those gap-referenced pages are the HIGHEST signal. Records which wiki page(s)
   cite each id, for the rationale.
2. **TRUTH-CHECK (status alone can lie).** An id counts as ingested ONLY if the ingest index
   has it with status `ingested` AND its `filename` (e.g. `raw/papers/<id>vN.pdf`) EXISTS on
   disk. The Splitwise case proves status alone is unreliable, so the on-disk file is the
   arbiter. `pending`/`waiting_approval` = already queued (skip). `rejected` = sticky (skip).
   Everything cited-but-not-truly-ingested-and-not-already-decided = the BACKFILL SET.
3. **FETCH + STAGE.** For each backfill id fetch metadata by id (arXiv `?id_list=<id>`),
   tag lane `backfill`, **bypass the seen.json cache** (a known-missing paper must never be
   suppressed by "seen"), and stage as `waiting_approval` via
   `ingest_index.enqueue(discovered_by="web", objective_ids=[citing GAP-ids], ...)` with a
   rationale naming the citing page(s). Ordered highest-signal first: gap `## Shows up in`
   cites before plain wiki cites. Respects `--limit` (default 10).
4. **REPORT.** Writes `meta/nightly_report/backfill-<date>.md` in the SAME interactive
   approve/reject checkbox block format `web-scrape` uses, so `/wiki-approve`
   (`scripts/report_approve.py`) parses it unchanged.

## Approval flow (user-driven)

The user reviews `meta/nightly_report/backfill-<date>.md` and picks which to ingest, then runs
`/wiki-approve` (or it is read on the next nightly run):
- **Approve:** flips `waiting_approval` -> `pending`; the **wiki** agent then fetches the paper
  into `raw/papers/` and runs `wiki-ingest`.
- **Reject:** sticky `rejected` (never re-proposed; the feedback signal).

## Cost discipline ($0)

- Free only: local wiki scan + arXiv Atom API by-id fetch. No paid engines, no LLM calls.
- `--dry-run` does zero network + zero writes; use it to preview before staging.

## RBAC boundaries

- **MAY write:** `meta/nightly_report/backfill-<date>.md`; the ingest queue via
  `agents.ingest_index enqueue`.
- **MUST NOT write:** `wiki/` (wiki owns ingest), `raw/` (Needs-Approval; wiki writes on
  approve), `objective/*`, `meta/health_report|cost_report`.
- **No `Edit`** anywhere; analysis is Read/Grep/Glob + WebFetch only.

## Conventions

- Follow `skills/references/ai-first-rules.md` + `write-rules.md`. ASCII only.
- Candidates key on stable per-item id (arXiv / DOI), never by filename.
- `written_by: web` on the backfill report (provenance unify; never `generated_by`).
