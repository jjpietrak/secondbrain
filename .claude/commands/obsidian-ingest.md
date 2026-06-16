---
description: Ingest a source (file/url/text) into the vault - or batch-ingest every not-yet-ingested raw source (no arg, or --new/--all). The vault rewrites itself around new knowledge; a per-vault content-hash index dedupes so nothing is ingested twice.
category: research
triggers_en: ["ingest this source", "add this article", "import this", "absorb this"]
---

Execute the following for `$ARGUMENTS`:

The argument is a URL, file path, or pasted text — OR a batch directive (`--new` / `--all`,
or empty). Resolve which mode:

- **Single source** — `$ARGUMENTS` is a specific URL / file path / pasted text → ingest just that.
- **Batch (no argument, or `--new` / `--all`)** — ingest every raw source not yet ingested.
  Get the list from the per-vault ingest index (content-hash keyed, idempotent):
  ```bash
  python -m agents.ingest_index pending      # one raw relpath per line (new or changed files)
  ```
  If the list is empty, report "nothing new to ingest" and stop. Otherwise ingest each listed
  file by running steps 1–8 for it (process them one at a time; `--all` re-ingests changed files
  too — same list). Report a per-file summary at the end.

For EACH source ingested (single or batch), run steps 1–9. After step 7 (structural files),
**record it in the index** so it is not re-ingested next time:
```bash
python -m agents.ingest_index mark "<raw relpath>" --source-page "wiki/sources/<the page you wrote>"
```
(URLs/pasted text are saved into `raw/` first in step 5, so they get a raw relpath to mark too.)

1. Read `$VAULT_ROOT/_CLAUDE.md` first if it exists. Load the
   **"## Vault purpose"** statement and hold it as a high-priority relevance filter for
   this ingest: frame all extraction around the PURPOSE, pull out what advances it, and
   for a clearly off-purpose source ingest only the on-purpose slice and mark the rest
   `> [!note] Off-purpose: …` rather than dropping it. (A human instruction to ingest
   something in full overrides this for that run.)

2. Classify the source type before reading the full content:
   - **Article/blog post** - extract key claims, people, tools, concepts
   - **PDF/document** - extract structure, findings, recommendations
   - **Transcript (meeting/podcast)** - extract speakers, decisions, action items, quotes
   - **YouTube video** - pull metadata, description, and transcript (see step 3 for method)
   - **Audio file** (.m4a, .mp3, .wav, .ogg, .webm) - transcribe, identify speakers, extract decisions/tasks/promises
   - **Image/screenshot** (.png, .jpg, .jpeg, .webp) - read/OCR the image, extract text and context
   - **Raw text** - classify by content (opinion, technical, narrative) and extract accordingly

3. Read or fetch the full source content:

   **For YouTube URLs** - try methods in this order (use the first one that works):

   **Method A - `yt-dlp` (best, works in Claude Code / terminal):**
   ```bash
   which yt-dlp || brew install yt-dlp
   yt-dlp --skip-download --print title --print description --print duration_string --print view_count --print like_count --print upload_date --print channel "URL"
   yt-dlp --write-auto-sub --sub-lang en --skip-download -o "/tmp/%(id)s" "URL"
   ```

   **Method B - YouTube MCP tools (works in Claude Desktop if configured):**
   Check if YouTube MCP tools are available. If so, use them.

   **Method C - oEmbed fallback (works everywhere, limited data):**
   Fetch `https://www.youtube.com/oembed?url=URL&format=json` - gives title and channel only. Ask user to paste description for full ingest.

   **For audio files** (.m4a, .mp3, .wav, .ogg, .webm):
   ```bash
   # Transcribe with Whisper (install if missing)
   which whisper || pip install openai-whisper
   whisper "path/to/audio.m4a" --model base --output_format txt --output_dir /tmp
   ```
   If `whisper` can't be installed, ask the user to paste the transcript.
   After transcription: identify speakers if possible, extract decisions, action items, promises, and who said what.
   Save the transcript to `$VAULT_ROOT/raw/transcripts/`.

   **For images/screenshots** (.png, .jpg, .jpeg, .webp):
   Claude can read images directly. Analyze the image for:
   - Text content (OCR) - extract all readable text
   - UI screenshots - describe what's shown, extract data from tables/forms/dashboards
   - Whiteboard/diagram photos - describe the structure and extract concepts
   - Chat screenshots - extract messages, people, decisions
   Save the image description to `$VAULT_ROOT/raw/articles/` as a markdown summary with context.

   **For articles** - use WebFetch to pull the page content
   **For PDFs** - read the file directly
   **For pasted text** - use as-is

4. Extract and organize:
   - **Entities**: people mentioned, companies, tools, projects
   - **Concepts**: key ideas, frameworks, methodologies
   - **Claims**: specific assertions with supporting evidence
   - **Action items**: anything actionable for the user
   - **Quotes**: notable quotes worth preserving

5. Save the raw source to `$VAULT_ROOT/raw/` (immutable - never modify after saving):
   - Create `$VAULT_ROOT/raw/articles/YYYY-MM-DD — Source Title.md` (or transcripts/, pdfs/, videos/)
   - Frontmatter: `date`, `tags: [source, <type>]`, `source_url`, `source_type`, `content_hash`

6. **REWRITE the vault** - this is the critical step. Don't just create new pages. Rewrite existing ones.

   Read `$VAULT_ROOT/wiki/index.md` first to understand what already exists in the vault. Then spawn parallel subagents:

   - **Entities agent**: for each person/company/tool mentioned:
     - Search `$VAULT_ROOT/wiki/entities/` for existing page
     - If found: REWRITE the page - merge new info with old, update role/context/interactions, add new links. Don't just append - integrate.
     - If not found: create new entity page (start from `$VAULT_ROOT/wiki/entities/_template.md`, frontmatter with `type, created, updated, sources`) with full context
   
   - **Concepts agent**: for each idea/framework/methodology:
     - Search `$VAULT_ROOT/wiki/concepts/` for existing or related pages
     - If found: REWRITE - update the concept with new evidence, new examples, new connections. If the new source adds depth, rewrite the whole section.
     - If not found: create new concept page (start from `$VAULT_ROOT/wiki/concepts/_template.md`, frontmatter with `type, created, updated, sources`)
     - If the ingest reveals a PATTERN across multiple existing concepts: create a new synthesis page in `$VAULT_ROOT/wiki/synthesis/` (start from `$VAULT_ROOT/wiki/synthesis/_template.md`) that connects them (e.g., "Three sources now mention X - this is a trend, not a one-off")
   
   - **Projects agent**: for each project referenced (a project is an entity with `entity_type: project`):
     - Search `$VAULT_ROOT/wiki/entities/` for matching project
     - If found: update with new findings, add to Recent Activity, update Key Decisions if the source contains relevant decisions
   
   - **Contradictions agent**: for each claim in the new source:
     - Search the vault for CONFLICTING claims in existing pages
     - If contradiction found: UPDATE the existing page to note the conflict, add the new evidence, and mark which claim is more recent/authoritative
     - If the new source SUPERSEDES old info: rewrite the old page with updated info and note what changed and why in the page's history section

7. Update structural files:
   - REBUILD `$VAULT_ROOT/wiki/index.md` - don't just append. Regenerate the sections that changed so descriptions stay current with the rewritten pages.
   - Append to the operation log at `$VAULT_ROOT/wiki/log.md`: `## [YYYY-MM-DD] ingest | Source Title (type) — X created, Y rewritten, Z contradictions resolved`
   - **Record the source in the ingest index** so it is not re-ingested:
     `python -m agents.ingest_index mark "<raw relpath>" --source-page "wiki/sources/<page>"`

8. Update today's daily note (`$VAULT_ROOT/daily/`) with:
   - What was ingested
   - What pages were REWRITTEN (not just created - this is the important part)
   - Any contradictions found and how they were resolved
   - Any new synthesis pages created from emerging patterns

9. Report back:
   - Source title and type
   - **New pages created** (list)
   - **Existing pages rewritten** (list with what changed)
   - **Contradictions resolved** (list with old claim vs new claim)
   - **Synthesis pages created** (patterns that emerged from this + existing knowledge)

The vault should be DIFFERENT after every ingest - not just bigger. Pages that existed before should be smarter, more connected, and more current. If an ingest only creates new pages and doesn't rewrite anything, it wasn't deep enough.

---

**AI-first rule:** Every note created or updated by this command MUST follow `/home/jpietrak/second_brain/skills/references/ai-first-rules.md` - `## For future Claude` preamble, rich frontmatter (`type`, `date`, `tags`, `ai-first: true`, plus type-specific fields), recency markers per external claim, mandatory `[[wikilinks]]` for every person/project/concept referenced, sources preserved verbatim with URLs inline, and confidence levels where applicable. The vault is for future-Claude retrieval - not human reading.

**Anti-fabrication:** Search exhaustively before claiming any note, person, or file is absent - false absence is the most common failure mode - and never invent facts, entities, or dates (mark unknowns as `TBD`). See the anti-fabrication and search-completeness hard rules in `/home/jpietrak/second_brain/skills/references/ai-first-rules.md`.
