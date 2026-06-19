---
name: wiki-ingest
description: >
  Ingest a source (PDF, article, transcript, image, or text) from raw/ into the wiki: read it
  completely, extract entities and concepts, create or UPDATE wiki pages with wikilink
  citations, flag contradictions, refresh the index/hot/log, and record the ingest in the
  ingest_index. Handles single sources and batches. Triggers on: "ingest", "ingest this",
  "process this source", "add this to the wiki", "read and file this", "ingest all of these",
  "batch ingest", "ingest the pending sources", "ingest raw/...".
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

# wiki-ingest: Source Ingestion

> ## For future Claude
> Read the source. Write the wiki. Cross-reference everything. A single source typically
> touches 5-15 wiki pages (one source summary, several entities/concepts, and the index/log/
> hot updates). This skill is the heart of the librarian role: it turns immutable raw material
> into a connected, citable, contradiction-aware knowledge graph. It PATCHES existing pages
> rather than rewriting them, and it never overwrites a page's role/status - bi-temporal facts
> are appended to `timeline:`.
> ai-first: true

- **Owner:** wiki agent (the librarian). Reads all of the vault; writes `wiki/`, `raw/<type>/`
  (only adds, never mutates existing raw), and `meta/ingest_index.*`.
- **LLM route:** ingest reasoning runs on the **wiki agent's Agent-SDK credit pool**
  (`scripts/claude_agent.sh`, $0 marginal). No paid API for extraction. PDF text extraction is
  the deterministic `scripts/pdf_extract.py` (PyMuPDF, $0). Optional later fact-checking of
  claims is delegated to `wiki-cite` (Gemini Flash validation) - not done inline here.
- **Obsidian syntax:** wikilinks `[[Note Name]]`, callouts `> [!type] Title`, embeds
  `![[file]]`, YAML frontmatter properties. ASCII only (no em-dashes, curly quotes, or Unicode
  math) per `skills/references/{ai-first-rules,write-rules}.md`.

---

## What feeds this skill: the ingest_index (no manifest)

There is NO `.manifest.json` and NO DragonScale address map. Delta tracking and source identity
are owned by `agents/ingest_index.py` (keyed by a stable source id - arXiv/DOI/YouTube/canonical
URL/hash, NOT the filename, so renames are not re-ingested).

```bash
VAULT_ROOT="$(.venv/bin/python -m agents.vault_config path)"
PY=".venv/bin/python"

# What needs ingesting (new or content-changed sources already on disk in raw/):
$PY -m agents.ingest_index pending            # one vault-relative path per line
# Approval-queue candidates the user APPROVED become 'pending' once fetched into raw/:
$PY -m agents.ingest_index status             # counts incl. waiting_approval / rejected
```

Statuses you act on:
- **pending** - a raw file is new or its content hash changed. Ingest it.
- **approved -> pending** - a web/research candidate the user approved; the wiki agent fetches
  it into `raw/<type>/` (via `wiki-defuddle` for URLs, or `scripts/pdf_extract.py` for PDFs),
  which flips it to `pending`, then ingest as normal.

Statuses you DO NOT act on:
- **waiting_approval** / **rejected** - these have no file on disk (web candidates not yet
  fetched, or user-rejected). They are surfaced to the user by the approval flow, not ingested
  here. (`ingest_index scan` deliberately skips them in its deletion sweep.)
- **ingested** - already done (unchanged hash). Skip unless the user says "force re-ingest".
- **deleted** - the raw file is gone; kept for history.

User-dropped raw files still auto-ingest: a file the user drops into `raw/<type>/` shows up in
`pending` on the next `ingest_index scan` with no approval needed (approval gating is only for
web/research-DISCOVERED candidates).

**Raw is immutable.** Never modify, reformat, or delete a file under `raw/`. wiki-ingest reads
raw and writes `wiki/` + `meta/ingest_index.*`. The only raw-adjacent write it does is letting
`wiki-defuddle`/`pdf_extract.py` ADD a new fetched file before ingest.

---

## Source-type handlers

Detect the source type from the raw subfolder / extension and prepare clean text:

- **PDF** (`raw/papers/*.pdf`, etc.): extract text deterministically -
  ```bash
  $PY -m scripts.pdf_extract "$VAULT_ROOT/raw/papers/Foo.pdf" --max-chars 120000
  ```
  Do NOT vision-read text PDFs page by page (far more expensive). Scanned/image PDFs that
  produce empty text need OCR first (out of scope; flag to the user).
- **URL / HTML**: the URL should already be a cleaned file in `raw/articles/` produced by
  `wiki-defuddle` (see `skills/wiki-defuddle/SKILL.md`). Ingest that file. The wiki agent has no
  web tools and does not fetch the web itself.
- **Image** (`raw/.../*.png|jpg|...`): Read the image with the Read tool (native vision),
  transcribe text + describe diagrams/entities into the source summary. The image file itself
  stays in raw (immutable).
- **Transcript / text / markdown**: read directly.

In all cases: **read the source completely. Do not skim.** Long sources are why the read-budget
discipline below exists.

---

## Single-source ingest

Trigger: a file in `raw/` is `pending`, or the user points at one source.

1. **Read** the source completely (via the right handler above).
2. **Discuss** (skip if the user said "just ingest it"): one line on what to emphasize and how
   granular. Editorial judgment is part of ingest.
3. **Orient cheaply** (context-window discipline, below): read `wiki/hot/hot.md`, then
   `wiki/index.md`, then 3-5 existing pages that are plausibly affected. Do not read the whole
   wiki.
4. **Create the source summary** in `wiki/sources/<Title>.md` using the sources `_template.md`
   in that folder (see Frontmatter below). Its `sources:` frontmatter lists the `[[raw/...]]`
   file(s) it summarizes. This page is the `[[sources/X]]` citation target every claim links to.
5. **Create or UPDATE entity pages** (`wiki/entities/`) for every person, organization, tool,
   company, project, or place mentioned that is wiki-worthy. One page per entity. UPDATE, do not
   duplicate - search the index first.
6. **Create or UPDATE concept pages** (`wiki/concepts/`) for significant ideas, frameworks,
   theories, and methods.
7. **Cite every external claim** with a wikilink to its source page: `[[sources/<Title>]]`.
   Citations are **wikilinks only** - never bare markdown `[text](url)` for an internal source.
   Attach a recency marker per external fact (`(as of 2026-06, ...)`) per ai-first rule 4.
8. **Bi-temporal updates (never overwrite).** When new info changes a fact about an entity or
   concept, APPEND an entry to that page's `timeline:` frontmatter (from/until/learned/source);
   do NOT overwrite the existing `role`, `status`, or prior facts. The page keeps its history.
9. **Update the shared targets** - `wiki/index.md`, `wiki/hot/hot.md`, `wiki/log.md` - using the
   locking snippet below (these are multi-writer append targets).
10. **Check for contradictions** (below).
11. **Record the ingest** in the index:
    ```bash
    $PY -m agents.ingest_index mark "raw/papers/Foo.pdf" --source-page "wiki/sources/Foo.md"
    ```
    This flips the source to `ingested` and re-renders `meta/ingest_index.md`.

---

## Batch ingest

Trigger: several files pending, or "ingest all of these".

1. List all `pending` files; confirm with the user before starting on large batches.
2. Ingest each source via the single-source flow, but DEFER cross-referencing between the new
   sources until the end.
3. **Cross-reference pass:** after all sources are in, look for connections among the newly
   ingested sources (shared entities, agreeing/conflicting claims). Add the links + contradiction
   callouts then.
4. Update `wiki/index.md`, `wiki/hot/hot.md`, `wiki/log.md` ONCE at the end (single locked
   write), not per source.
5. Check in with the user every ~10 sources on large batches. Report: "Processed N sources,
   created X pages, updated Y pages; key connections: ...".

---

## Context-window discipline

Token budget matters - keep ingest cheap:
- Read `wiki/hot/hot.md` first. If it has the context you need, do not re-read full pages.
- Read `wiki/index.md` to find existing pages before creating new ones (avoid duplicates).
- Read only 3-5 existing pages per ingest. Needing 10+ means you are reading too broadly.
- Edit surgically (PATCH a section), do not re-read and rewrite a whole file to change one field.
- Keep wiki pages short (100-300 lines). Split pages that grow past 300 lines.
- Use Grep/Glob over the vault to locate specific content instead of reading full pages.

---

## Contradictions (flag, never silently overwrite)

When new info conflicts with an existing page, add a `> [!contradiction]` callout on BOTH pages.

On the existing page:
```markdown
> [!contradiction] Conflict with [[sources/New Source]]
> [[Existing Page]] states X (as of <date>, [[sources/Old Source]]).
> [[sources/New Source]] says Y (as of <date>). Needs resolution - check dates and primary sources.
```

On the new source summary:
```markdown
> [!contradiction] Contradicts [[Existing Page]]
> This source says Y; the existing wiki says X. See [[Existing Page]].
```

Do not overwrite the old claim. Flag it and let the user (or `wiki-reconcile`) adjudicate.
Contradictions found mid-ingest are the highest-signal output of this skill.

---

## Log entry (append at the TOP of wiki/log.md)

```markdown
## [YYYY-MM-DD] ingest | <Source Title>
- Source: [[raw/papers/Foo.pdf]]
- Summary: [[sources/Foo]]
- Pages created: [[entities/Bar]], [[concepts/Baz]]
- Pages updated: [[index]]
- Key insight: one sentence on what is new.
```

---

## Frontmatter (merged superset - additive)

Use the `_template.md` already present in each `wiki/<type>/` folder. The agreed schema is the
SUPERSET of the live templates plus the frozen-doc tags - additive, drop nothing. Every page
also carries the `## For future Claude` preamble and `ai-first: true`.

- **entities/** (concrete: person/organization/tool/company/project/place): keep `type: entity`,
  `entity_type`, `role`, `status`, `aliases`, `related`, `sources`, `tags: [entity]`; ADD the
  bi-temporal `timeline:` (from/until/learned/source) and `company`/`last_interaction` where
  useful. Append to `timeline:`; never overwrite `role`/`status`.
- **concepts/** (abstract: ideas/frameworks/theories/methods): `type: concept`, `complexity`,
  `domain`, `status`, `aliases`, `related`, `sources`, `tags: [concept]`.
- **sources/** (one summary per ingested raw source): `type: source`, `source_type`, `author`,
  `date_published`, `url`, `confidence`, `key_claims`, `status`, `related`, `sources` (the
  `[[raw/...]]` file(s)), `tags: [source]`. ADD `content_hash` where useful (mirrors the index).
- **synthesis/**: `type: synthesis`, `synthesis_type`, `subjects`, `dimensions`, `verdict`,
  `status`, `tags: [synthesis]` (produced mainly by `wiki-synth`, not the per-source path).

Frontmatter writes are `.get()`-safe and additive: never drop an existing field, never regress
`status` or `role`.

---

## Locking: shared append targets (REQUIRED)

`wiki/index.md`, `wiki/log.md`, and `wiki/hot/hot.md` are written by multiple agents/skills. The
ingest_index mirror (`meta/ingest_index.md`) is updated by `ingest_index.py` itself (which has
its own atomic write); your skill only needs to lock the three wiki shared files. Per-page writes
to a single owned note (one entity/concept/source page) do not strictly need a lock, but locking
is cheap and harmless. Follow `skills/references/locking.md` exactly. Acquire ALL the shared
targets in sorted-path order (deadlock avoidance), write, release.

```bash
LOCK="scripts/wiki-lock.sh"

acquire_or_skip() {            # $1 = vault-relative path; 0 = acquired, 1 = skip
  local path="$1"
  if bash "$LOCK" acquire "$path"; then return 0; fi
  sleep 2                       # rc=75 (held): retry ONCE after 2s
  if bash "$LOCK" acquire "$path"; then return 0; fi
  echo "wiki-lock: $path still held after retry -> skipping this write" >&2
  return 1
}

# Multi-file shared write in sorted-path order:
PATHS=$(printf '%s\n' wiki/hot/hot.md wiki/index.md wiki/log.md | sort)
held=(); ok=1
for p in $PATHS; do
  if acquire_or_skip "$p"; then held+=("$p"); else ok=0; break; fi
done
if [ "$ok" -eq 1 ]; then
  # ... append the log entry, add index rows, refresh hot ...
  :
fi
for p in "${held[@]}"; do bash "$LOCK" release "$p"; done
```

On rc=75 after the retry, log and skip that write (never spin, never trample). The Layer-1
cross-host lease (`scripts/vault_lease.sh`) is NOT called from this skill - it is wired into the
nightly orchestrator only (Phase 4).

---

## What not to do

- Do not modify, reformat, or delete files under `raw/` (immutable source content).
- Do not create duplicate pages - always check `wiki/index.md` and Grep the vault first
  (anti-false-absence: search by every plausible name/alias/folder before concluding a page is
  missing).
- Do not skip the log entry, the hot-cache refresh, or the `ingest_index mark`.
- Do not overwrite a claim, `role`, or `status` - append to `timeline:` and flag contradictions.
- Do not write bare markdown links for internal sources - wikilinks only.
- Do not call any web tool. URL sources arrive pre-fetched in `raw/articles/`.

---

## Idempotency

Re-running ingest on an unchanged source is a no-op: `ingest_index pending` will not list it
(same content hash, status `ingested`), so the wiki agent skips it unless the user forces a
re-ingest. A forced re-ingest UPDATES the same pages (PATCH) rather than creating duplicates.
