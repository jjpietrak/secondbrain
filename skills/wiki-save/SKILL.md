---
name: wiki-save
description: >
  Save the current conversation, answer, or insight into the wiki as a structured, self-contained
  note. Picks the right wiki subfolder (entities / concepts / synthesis / sources), writes full
  frontmatter and a "For future Claude" preamble, links what was mentioned, and updates the
  index/log/hot cache. Triggers on: "save this", "save that answer", "save to wiki",
  "file this", "file this conversation", "keep this", "save this analysis", "save this session",
  "add this to the wiki".
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

# wiki-save: File Conversations Into the Wiki

> ## For future Claude
> Good answers and insights should not vanish into chat history. This skill takes what was just
> discussed and files it as a permanent wiki page, written in declarative present tense (the
> knowledge, not the transcript), fully linked and citable. The wiki compounds - save often.
> Unlike `wiki-ingest`, there is no raw source file: the conversation IS the source.
> ai-first: true

- **Owner:** wiki agent. Writes `wiki/`; reads the conversation + the existing vault.
- **LLM route:** the reasoning (deciding type, extracting the durable content, rewriting it as
  declarative knowledge) runs on the **wiki agent's Agent-SDK credit pool** (`claude_agent.sh`,
  $0 marginal). No paid API.
- **No personal-vault routing, no transport selection, no methodology-mode router.** This vault
  is filesystem-only and its schema is fixed by `docs/vault-schema.md`. The note always lands in
  the project's own `wiki/` under one of its four subfolders. ASCII only.

---

## Note-type decision (map to OUR four wiki subfolders)

This vault has no `decision/`, `session/`, or `questions/` folder. Map every save to one of the
four real subfolders:

| Content is...                                              | Folder            | type:      |
|------------------------------------------------------------|-------------------|------------|
| Multi-step analysis, comparison, or answer to a question   | `wiki/synthesis/` | synthesis  |
| An idea, framework, theory, or method being explained      | `wiki/concepts/`  | concept    |
| A concrete person, org, tool, company, project, or place   | `wiki/entities/`  | entity     |
| A summary of external material discussed in the session    | `wiki/sources/`   | source     |

If the user names a type, use it. Otherwise pick the best fit. When in doubt, use **synthesis**.
A decision or session summary files as a **synthesis** note (with a clear title); it is also
recorded in `wiki/log.md` (see step 8).

---

## Save workflow

Resolve the vault root once; never hard-code a path:
```bash
VAULT_ROOT="$(.venv/bin/python -m agents.vault_config path)"
```

1. **Scan** the current conversation. Identify the most valuable durable content to preserve.
2. **Ask the name** (if not already obvious): "What should I call this note?" Keep it short and
   descriptive.
3. **Determine the type/folder** from the table above.
4. **Search before write** (anti-duplicate, anti-false-absence): Grep `wiki/index.md` and the
   target folder by every plausible name/alias before creating. If a page on the same concept
   exists, UPDATE it (PATCH the relevant section) instead of creating a duplicate.
5. **Extract + rewrite** the content in declarative present tense - the knowledge itself, not
   "the user asked / Claude explained". The note must be readable cold with zero context.
6. **Create** the note at `"$VAULT_ROOT/wiki/<folder>/<Title>.md"` with the merged-superset
   frontmatter for that type (use the folder's `_template.md`), a `## For future Claude`
   preamble, recency markers on external facts, and `ai-first: true`. If the path already exists,
   ASK before overwriting.
7. **Collect links:** add every wiki page, entity, or concept mentioned to `related:` in
   frontmatter and as `[[wikilinks]]` in the body. Cite external material as `[[sources/X]]`
   (wikilinks only). Create a one-line stub for any linked page that does not exist yet (per
   write-rules Stub Notes).
8. **Update the shared targets** under the locking snippet (below): add an entry to
   `wiki/index.md`, append a `save` line to the TOP of `wiki/log.md`, and refresh
   `wiki/hot/hot.md`.
9. **Confirm:** "Saved as [[Title]] in wiki/<folder>/."

---

## Log entry (append at the TOP of wiki/log.md)

```markdown
## [YYYY-MM-DD] save | <Note Title>
- Type: <synthesis|concept|entity|source>
- Location: wiki/<folder>/<Note Title>.md
- From: conversation on <brief topic>
```

---

## Frontmatter

Use the target folder's `_template.md` (the merged superset: live fields + frozen tags +
`ai-first: true`). Always include the universal fields (`type`, `created`, `updated`,
`tags` including the type, `status`, `ai-first: true`) plus `related:` and, where the note
draws on external material, `sources:` with `[[sources/X]]` links. For an entity note, follow
the bi-temporal rule: append to `timeline:`, never overwrite `role`/`status`.

---

## Writing style

- Declarative, present tense. Write the knowledge, not the conversation.
  - Not: "The user asked about X and Claude explained..."
  - Yes: "X works by doing Y. The key insight is Z (as of <date>)."
- Self-contained: a future session must be able to read this page with no surrounding context.
  No "see above" / "as mentioned".
- Link every mentioned concept/entity/page with `[[wikilinks]]`. Cite sources as `[[sources/X]]`.
- Bullets and structure over prose (better for retrieval).

---

## What to save vs skip

Save: non-obvious insights or synthesis; decisions with rationale; analyses that took real
effort; comparisons likely to be referenced again; research findings.

Skip: mechanical Q&A with obvious answers; setup steps documented elsewhere; throwaway debugging
with no lasting insight; anything already in the wiki (UPDATE the existing page instead).

---

## Locking: shared append targets (REQUIRED)

`wiki/index.md`, `wiki/log.md`, and `wiki/hot/hot.md` are multi-writer targets. Follow
`skills/references/locking.md` exactly: acquire all three in sorted-path order, write, release.
The new content page itself is a single owned note (lock optional but harmless).

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

PATHS=$(printf '%s\n' wiki/hot/hot.md wiki/index.md wiki/log.md | sort)
held=(); ok=1
for p in $PATHS; do
  if acquire_or_skip "$p"; then held+=("$p"); else ok=0; break; fi
done
if [ "$ok" -eq 1 ]; then
  # ... add the index entry, prepend the log entry, refresh hot ...
  :
fi
for p in "${held[@]}"; do bash "$LOCK" release "$p"; done
```

On rc=75 after the retry, log and skip that write. The Layer-1 cross-host lease is NOT called
from this skill (nightly orchestrator only, Phase 4).
