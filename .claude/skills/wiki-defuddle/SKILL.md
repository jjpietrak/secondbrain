---
name: wiki-defuddle
description: >
  Strip clutter from a web page (ads, nav, headers, footers, cookie banners) before it is
  ingested into the wiki, leaving clean readable Markdown in raw/articles/. Shells out to the
  defuddle-cli tool; saves 40-60% of tokens on typical articles. Triggers on: "defuddle",
  "clean this page", "clean this url", "strip this url", "fetch and clean", "clean web content
  before ingesting", "remove clutter", "readable markdown from url".
allowed-tools: Read, Bash
---

**Ownership: `wiki` agent.** If you are NOT the `wiki` subagent (e.g. the main orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: wiki`, pass the user's full request, let the wiki agent run the steps below, and relay its result. Do NOT run the steps yourself - running as the `wiki` agent is what activates the RBAC/write-scope boundary. If you ARE the `wiki` agent, proceed.

# wiki-defuddle: Web Page Cleaner

> ## For future Claude
> This skill turns a messy HTML page (or a local .html file) into clean Markdown and drops
> it into `raw/articles/<slug>-<date>.md` so `wiki-ingest` can pick it up. It is a thin,
> deterministic wrapper around the external `defuddle-cli` tool - no LLM reasoning, no vault
> knowledge edits beyond writing one immutable raw file. ASCII output only.
> ai-first: true

- **Owner:** wiki agent.
- **LLM route:** none. This skill shells out to `defuddle-cli` (an npm tool). No model call,
  $0. Tool-only.
- **Writes:** exactly one file under `raw/articles/` (immutable raw, per the vault schema). It
  does NOT touch `wiki/`, the index, the log, or the ingest_index - that is `wiki-ingest`'s job
  after the file lands.

---

## Dependency: defuddle-cli

`defuddle-cli` is an external Node tool (kepano/defuddle). It is not bundled.

```bash
npm install -g defuddle-cli      # one-time install
defuddle --version               # verify
```

If it is absent, this skill degrades gracefully (see "Fallback" below) rather than failing.

---

## Where things go

Resolve the active vault root once - never hard-code a path:

```bash
VAULT_ROOT="$(.venv/bin/python -m agents.vault_config path)"
```

Cleaned articles are written to `"$VAULT_ROOT/raw/articles/<slug>-<YYYY-MM-DD>.md"`. The slug
is the last URL path segment, lowercased, spaces and slashes turned to hyphens, query string
and fragment stripped, leading dots/hyphens removed (no path traversal).

A small helper does the slug + fetch + frontmatter + routing deterministically:

```bash
.venv/bin/python -m scripts.wiki_ingest_defuddle "https://example.com/some/article"
# -> writes raw/articles/article-2026-06-19.md and prints the absolute path on stdout
```

The helper:
1. Resolves `$VAULT_ROOT` via `agents.vault_config path`.
2. Derives the slug + dated filename.
3. Runs `defuddle "<url>"` (URL is passed as a single quoted arg - no shell interpolation of
   the URL into a command string, so a hostile URL cannot inject).
4. Prepends a minimal frontmatter header (`source_url`, `fetched`, `source_type: article`) so
   `ingest_index` can derive a stable id and `wiki-ingest` knows the provenance.
5. Writes the result to `raw/articles/<slug>-<date>.md` and prints the path.

If `defuddle` is not on PATH the helper exits non-zero with a clear message; the caller then
falls back (below). The helper never overwrites an existing raw file - raw is immutable; on a
name clash it appends a numeric suffix.

---

## Manual usage (when you want to inspect before saving)

Clean a URL to stdout to eyeball it first:

```bash
defuddle "https://example.com/article"
```

Clean a local HTML file (e.g. a saved page):

```bash
defuddle ./page.html
```

---

## Fallback (defuddle not installed)

```bash
if ! command -v defuddle >/dev/null 2>&1; then
  echo "defuddle-cli not installed (npm i -g defuddle-cli); falling back" >&2
fi
```

When `defuddle` is absent and the article is short, the wiki agent may save the page content
it already has (e.g. from a user paste) directly into `raw/articles/` with the same frontmatter
header, then hand off to `wiki-ingest`. wiki-defuddle does NOT fetch the web itself and the
wiki agent has no web tools - it only cleans content that is provided to it (a URL handed to
defuddle-cli, or pasted HTML/text).

---

## When to use / skip

Use defuddle when:
- A user provides a web article, blog post, or docs page URL to ingest.
- The page has heavy surrounding chrome (most pages do) and you want to stay in token budget.

Skip defuddle when:
- The source is already clean Markdown or a PDF (use `scripts/pdf_extract.py` for PDFs).
- The page is an app/dashboard/structured data, not article-style content (defuddle's
  heuristics expect prose).

---

## Handoff to wiki-ingest

After the cleaned file lands in `raw/articles/`, it is a normal raw source. The wiki agent runs
`wiki-ingest` on it (or it is auto-detected on the next `ingest_index scan`). wiki-defuddle does
not lock or write any shared append target - it only creates one immutable raw file - so no
locking snippet is required here.

---

## Compliance

- ai-first-rules + write-rules: the raw file is immutable source content, kept verbatim from
  defuddle plus a small provenance header. The downstream `wiki/sources/<x>.md` page (written by
  `wiki-ingest`) is what carries the `## For future Claude` preamble, recency markers, and
  wikilinks.
- ASCII only. The helper does not emit em-dashes, curly quotes, or Unicode math of its own; if
  the source page contains such characters they are preserved verbatim in raw (raw is a faithful
  copy), and `wiki-ingest` normalizes when it writes the wiki page.
