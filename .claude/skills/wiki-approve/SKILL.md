---
name: wiki-approve
description: >
  Read back ticked approve/reject checkboxes from a nightly_report (or the backlog) and
  apply them to the ingest_index, then fetch and ingest each approved source. Triggers on:
  "wiki-approve", "/wiki-approve", "wiki-approve backlog", "process approvals",
  "apply report decisions", "ingest approved sources", "review backlog".
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

**Ownership: `wiki` agent.** If you are NOT the `wiki` subagent, DISPATCH it: call the Task
tool with `subagent_type: wiki`, pass the user's full request, let the wiki agent run the
steps below, and relay its result. Do NOT run the steps yourself.

# wiki-approve: Interactive Report Read-Back

> ## For future Claude
> The user ticked boxes in a nightly_report (or backlog.md) and you are now turning those
> ticks into real ingest_index status flips, raw-file fetches, and wiki pages. Four outcomes:
> approve -> fetch + wiki-ingest, reject -> sticky, deferred -> backlog.md (report mode) or
> kept in backlog (backlog mode), conflict -> skip + surface.
> ai-first: true

- **Owner:** wiki agent (the librarian). Reads meta/nightly_report/; writes via
  ingest_index Bash calls (approve/reject); writes raw/<type>/ (fetch) and wiki/ (ingest).
  Appends to / rewrites meta/nightly_report/backlog.md for deferred sources.
- **LLM route:** reasoning runs on the wiki agent's Agent-SDK credit pool
  (scripts/claude_agent.sh, $0 marginal). Fetch is defuddle/pdf_extract ($0).
- **RBAC:** wiki MAY write raw/<type>/ (Needs-Approval is satisfied by the user's tick in
  the report) and wiki/ via wiki-ingest. report_approve.py only flips ingest_index (a Bash
  call -- not a file Write, so RBAC guard does not block it). Conflict ids are SKIPPED and
  surfaced back to the user; never approve-or-reject-guessed.

---

## When to use

- **`/wiki-approve` (no arg) -- report mode:** after the user opens the latest
  `meta/nightly_report/<date>.md` in Obsidian, ticks approve/reject boxes for the
  candidates, and invokes the command. Deferred (unticked) sources are accumulated in
  `meta/nightly_report/backlog.md` with a `- **search-date**: <D>` bullet so you can
  see how long each item has waited.
- **`/wiki-approve backlog` -- backlog mode:** the user has reviewed `backlog.md`, ticked
  approve/reject for some items, and wants to process those decisions. Approved/rejected
  blocks are removed from backlog.md; still-undecided blocks stay.
- **Nightly auto-read** (Phase 4, called by nightly_run.sh): the orchestrator calls
  report_approve.py on the prior night's report to harvest any overnight user decisions
  before generating the new report. Parser is shared -- same logic both paths.

---

## Steps for `/wiki-approve` (report mode, default)

### 1. Locate the report

```bash
VAULT_ROOT="$(.venv/bin/python -m agents.vault_config path)"
# Most recent nightly report (or the user specifies a date):
REPORT="$(ls -1t "$VAULT_ROOT/meta/nightly_report/"*.md 2>/dev/null | grep -v backlog | head -1)"
```

Confirm the path with the user if ambiguous.

### 2. Dry-run preview

Show the user EXACTLY what will happen BEFORE applying. Print the JSON plan and ask for
confirmation if there are conflicts.

```bash
.venv/bin/python scripts/report_approve.py \
    --report "$REPORT" \
    --vault  "$VAULT_ROOT" \
    --mode   report \
    --json
```

Output fields:
- `approved`   -- ids that will flip waiting_approval -> pending
- `rejected`   -- [{id, reason}] that will flip -> rejected (sticky)
- `conflicts`  -- ids where BOTH boxes are ticked; will be SKIPPED, surfaced to user
- `deferred`   -- ids with neither box ticked (will be written to backlog.md)
- `backlogged` -- ids that will be newly appended to backlog.md (subset of deferred)
- `blocked`    -- approved ids with no working URL (cannot approve; surfaced to user)
- `applied`    -- false (dry-run)

If `conflicts` is non-empty, stop and ask the user to untick one box per conflicted id
before proceeding. If `blocked` is non-empty, surface them -- the source needs a URL in
the ingest_index before it can be approved.

### 3. Apply

Add `--apply` to flip statuses in the ingest_index and write deferred blocks to backlog.md:

```bash
.venv/bin/python scripts/report_approve.py \
    --report "$REPORT" \
    --vault  "$VAULT_ROOT" \
    --mode   report \
    --apply  --json
```

This calls `ingest_index.approve(id)` (waiting_approval -> pending) and
`ingest_index.reject(id, reason)` (-> rejected, sticky) for each decided id.
Deferred (unticked) sources are appended to `meta/nightly_report/backlog.md` with a
`- **search-date**: <D>` bullet recording the crawl date of the source report. If
backlog.md does not exist it is created with YAML frontmatter and an instruction header.
Deferred sources are NOT deduped: if an id is already in backlog.md it is not re-added,
and the original search-date is preserved.

### 4. Fetch and ingest each approved source

For each id in the `approved` list, the wiki agent fetches the source into the correct
raw/ subdirectory, then runs the full wiki-ingest flow:

```
arxiv / doi / pdf URL  ->  raw/papers/
blog / news / article  ->  raw/articles/
forum / opinion post   ->  raw/opinions/
```

Fetch methods (choose by id type):
- **arxiv:** `curl -sL "https://arxiv.org/pdf/<id>.pdf" -o "$VAULT_ROOT/raw/papers/<id>.pdf"`
  (strip the arxiv: prefix and version suffix for the filename)
- **doi:** resolve the DOI to a PDF URL first, then download.
- **url:** run `wiki-defuddle` on the URL to produce a clean markdown in raw/articles/.
- After fetching, run `wiki-ingest` on the fetched file (see wiki-ingest/SKILL.md).

`ingest_index scan` is called implicitly by `ingest_index mark` at the end of each ingest,
which flips the row from pending -> ingested.

### 5. Annotate the report

After all fetches and ingests complete, mark each row in the report file:
- Approved + ingested: change `- [x] approve` to `- [x] approve (ingested YYYY-MM-DD)`
  and add a `[[wiki/sources/<Title>]]` link.
- Rejected: change `- [x] reject` to `- [x] reject (recorded YYYY-MM-DD)`.
- Deferred: leave untouched (they will appear in backlog.md for later review).
- Do NOT edit the Decision trace section or any untouched rows.

Use the wiki-lock snippet for the report file if writing back (it is a shared output target).

---

## Steps for `/wiki-approve backlog` (backlog mode)

### 1. Locate the backlog

```bash
VAULT_ROOT="$(.venv/bin/python -m agents.vault_config path)"
BACKLOG="$VAULT_ROOT/meta/nightly_report/backlog.md"
```

The `- **search-date**: <D>` bullet on each block shows how long that source has waited.

### 2. Dry-run preview

```bash
.venv/bin/python scripts/report_approve.py \
    --mode   backlog \
    --vault  "$VAULT_ROOT" \
    --json
```

(--report defaults to backlog.md when --mode backlog is given with no --report.)

Output fields:
- `approved`   -- ids that will flip -> pending (and be removed from backlog.md)
- `rejected`   -- [{id, reason}] that will flip -> rejected (and be removed from backlog.md)
- `remaining`  -- ids with neither box ticked; will be kept in backlog.md
- `conflicts`  -- ids where BOTH boxes ticked; kept in backlog.md
- `blocked`    -- approved ids with no working URL; kept in backlog.md
- `applied`    -- false (dry-run)

Surface conflicts and blocked items to the user before proceeding.

### 3. Apply

```bash
.venv/bin/python scripts/report_approve.py \
    --mode   backlog \
    --vault  "$VAULT_ROOT" \
    --apply  --json
```

backlog.md is rewritten to contain ONLY the still-deferred (remaining + conflict + blocked)
blocks. The frontmatter is preserved and `updated:` is bumped to today's date. The
`- **search-date**:` bullet for each kept block is preserved unchanged.

### 4. Fetch and ingest approved sources (same as report mode Step 4)

Follow the same fetch + wiki-ingest flow for each approved id.

---

## RBAC summary

| Operation               | How it happens        | RBAC boundary                      |
|-------------------------|-----------------------|------------------------------------|
| Flip ingest_index       | Bash call (Python)    | Bash tool (not blocked)            |
| Write raw/<type>/       | Bash curl / defuddle  | wiki ALLOW raw/                    |
| Write wiki/             | wiki-ingest           | wiki ALLOW wiki/                   |
| Annotate report         | Edit tool             | wiki ALLOW meta/nightly_report/    |
| Write/rewrite backlog   | Python (report_approve.py) | wiki ALLOW meta/nightly_report/ |

---

## Both workflows share the same parser

`scripts/report_approve.py::parse_candidates(md_text)` is the single source of truth for
checkbox parsing (returns ALL candidates including deferred). `parse_report(md_text)` is a
backward-compatible wrapper that filters out deferred entries. Both the manual `/wiki-approve`
command (this skill) and the Phase-4 nightly auto-read import and call them directly. Do NOT
duplicate the parsing logic.

---

## Backlog file format

`meta/nightly_report/backlog.md` is a standard candidate-block file with YAML frontmatter:

```markdown
---
type: nightly_backlog
written_by: web
updated: 2026-06-23
---

<!-- Items here are undecided (deferred) candidates from nightly reports.
     Review with: python scripts/report_approve.py --mode backlog --vault <V> [--apply]
     Tick approve or reject for each item, then re-run with --apply. -->

### 1. <title>
- [ ] approve * `<id>`
- [ ] reject * `<id>`
    - reason:
- **retrieved via**: arxiv * **source**: <sid> * **published**: <d> * **score**: <s> * **lane**: <lane>
- **url**: <url>
- **search-date**: 2026-06-20

**Summary** -- ...
**Relevance** -- ...
```

The `- **search-date**:` bullet is inserted right after the `- **url**:` line by
report_approve.py when writing to the backlog. It records the crawl date of the original
nightly report so you can see how long a source has been waiting.

---

## What not to do

- Do not apply without the dry-run preview step (user needs to see conflicts/blocked first).
- Do not fetch or ingest a rejected source.
- Do not guess when both boxes are ticked -- skip and surface.
- Do not ignore the Decision trace section when parsing (the parser already stops there;
  never re-implement parsing inline).
- Do not bypass RBAC by writing wiki/ pages as the backend or web agent.
- Do not re-add an id to backlog.md that is already present (dedupe is enforced by
  report_approve.py; the original search-date is preserved).
