---
description: Smart vault search - returns results with context, not just filenames
category: vault
triggers_en: ["find in vault", "search my notes", "where is", "what did I write about"]
---

Execute the following for `$ARGUMENTS`:

The argument is the search query.

1. Read `$VAULT_ROOT/_CLAUDE.md` first if it exists
2. Run `search(query="...")` with the provided query
3. Also try variations if results are sparse (synonyms, related terms)
4. Return results with context: note title, folder, a relevant excerpt, and what type of note it is
5. If results are ambiguous, group them by type (people, projects, tasks, etc.)
6. Offer to open, update, or link any of the found notes

Do not just return filenames - return enough context for the user to act on the results.

---

**AI-first rule:** Every note created or updated by this command MUST follow `/home/jpietrak/second_brain/skills/references/ai-first-rules.md` - `## For future Claude` preamble, rich frontmatter (`type`, `date`, `tags`, `ai-first: true`, plus type-specific fields), recency markers per external claim, mandatory `[[wikilinks]]` for every person/project/concept referenced, sources preserved verbatim with URLs inline, and confidence levels where applicable. The vault is for future-Claude retrieval - not human reading.

**Anti-fabrication:** Search exhaustively before claiming any note, person, or file is absent - false absence is the most common failure mode - and never invent facts, entities, or dates (mark unknowns as `TBD`). See the anti-fabrication and search-completeness hard rules in `/home/jpietrak/second_brain/skills/references/ai-first-rules.md`.
