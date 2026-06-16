---
description: Save everything worth keeping from this conversation to the vault
category: vault
triggers_en: ["save this", "save the conversation", "save to vault", "obsidian save"]
---

Execute the following:

1. Read `/mnt/c/Obsidian/_CLAUDE.md` first if it exists
2. Scan the entire conversation and identify all vault-worthy items: decisions, tasks, people mentioned, projects started, ideas, learnings, deals, mentions/shoutouts, AND content-worthy items (hooks, data points, swipe-file material, research findings)
3. Group items by type: people, projects, tasks, decisions, ideas, deals, content
4. Spawn parallel subagents - one per group - so all note types are handled simultaneously:
   - **People agent**: search for each person, create or update notes in `/mnt/c/Obsidian/wiki/entities/`, log interactions
   - **Projects agent**: search for each project (a project is an entity with `entity_type: project`), create or update notes in `/mnt/c/Obsidian/wiki/entities/`
   - **Tasks agent**: parse tasks, add to the right kanban columns
   - **Decisions agent**: find relevant project notes in `/mnt/c/Obsidian/wiki/entities/`, append to Key Decisions sections
   - **Ideas agent**: search `/mnt/c/Obsidian/wiki/concepts/` for related notes, create or append
   - **Content agent** (if a `social-media/` folder exists in the vault): scan for content-worthy items and route them:
     - **Hooks, angles, contrarian takes** → append to `social-media/ideas.md` (dated bullet)
     - **Specific numbers, stats, reusable data points** → append to `social-media/data-points.md` (with source)
     - **External posts that hit + why** → append to `social-media/swipe-file.md` (link + reason)
     - **Research findings, frameworks, methodologies** → create `social-media/research/YYYY-MM-DD — topic.md`
5. After all agents complete: update today's daily note in `/mnt/c/Obsidian/daily/` with links to everything saved
6. Report back: a clean list of what was saved and where

When creating pages under the wiki layer (`/mnt/c/Obsidian/wiki/entities/`, `/mnt/c/Obsidian/wiki/concepts/`, `/mnt/c/Obsidian/wiki/synthesis/`, `/mnt/c/Obsidian/wiki/sources/`), start from the matching `/mnt/c/Obsidian/wiki/<folder>/_template.md` and ensure frontmatter has `type, created, updated, sources`.

Search before creating anything - duplicate notes are vault rot. Propagate every write to boards, daily note, and linked notes. Never create an orphaned note.

The content agent only runs if `social-media/` exists in the vault. If it doesn't exist, skip silently - don't create the folder unprompted.

---

**AI-first rule:** Every note created or updated by this command MUST follow `/home/jpietrak/second_brain/skills/references/ai-first-rules.md` - `## For future Claude` preamble, rich frontmatter (`type`, `date`, `tags`, `ai-first: true`, plus type-specific fields), recency markers per external claim, mandatory `[[wikilinks]]` for every person/project/concept referenced, sources preserved verbatim with URLs inline, and confidence levels where applicable. The vault is for future-Claude retrieval - not human reading.

**Anti-fabrication:** Search exhaustively before claiming any note, person, or file is absent - false absence is the most common failure mode - and never invent facts, entities, or dates (mark unknowns as `TBD`). See the anti-fabrication and search-completeness hard rules in `/home/jpietrak/second_brain/skills/references/ai-first-rules.md`.
