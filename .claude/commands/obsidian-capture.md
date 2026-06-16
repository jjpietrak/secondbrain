---
description: Quick idea capture - zero friction, saves to concepts/ and mentions in daily note
category: vault
triggers_en: ["capture this idea", "save this idea", "quick note", "drop a thought"]
---

Execute the following for `$ARGUMENTS`:

The optional argument is the idea text. If not provided, pull the most recent idea or thought from the conversation.

1. Read `/mnt/c/Obsidian/_CLAUDE.md` first if it exists
2. Take the argument as the idea, or pull from recent conversation context
3. Search `/mnt/c/Obsidian/wiki/concepts/` for a related existing note - if found, append to it
4. If new: create `/mnt/c/Obsidian/wiki/concepts/Title.md` starting from `/mnt/c/Obsidian/wiki/concepts/_template.md`, ensuring frontmatter has `type, created, updated, sources` (and `tags: [idea]`)
5. Write the idea with any supporting context from the conversation
6. Add a brief mention in today's daily note (`/mnt/c/Obsidian/daily/`) under an Ideas or Captures section

---

**AI-first rule:** Every note created or updated by this command MUST follow `/home/jpietrak/second_brain/skills/references/ai-first-rules.md` - `## For future Claude` preamble, rich frontmatter (`type`, `date`, `tags`, `ai-first: true`, plus type-specific fields), recency markers per external claim, mandatory `[[wikilinks]]` for every person/project/concept referenced, sources preserved verbatim with URLs inline, and confidence levels where applicable. The vault is for future-Claude retrieval - not human reading.

**Anti-fabrication:** Search exhaustively before claiming any note, person, or file is absent - false absence is the most common failure mode - and never invent facts, entities, or dates (mark unknowns as `TBD`). See the anti-fabrication and search-completeness hard rules in `/home/jpietrak/second_brain/skills/references/ai-first-rules.md`.
