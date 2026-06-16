---
description: Web research with citations. Selectable engine - claude (native WebSearch+WebFetch, $0, default), perplexity (Sonar, metered), or free (key-less arXiv/HN/Reddit/Wikipedia). Deep dossier with summary, facts, timeline, players, contrarian views, open questions.
category: research
triggers_en: ["research this", "look up", "find information about", "perplexity research"]
---

Execute the following for `$ARGUMENTS`:

1. Load the vault **PURPOSE** (`$VAULT_ROOT/_CLAUDE.md` → "## Vault
   purpose", mirrored in the active vault's `config/vaults/<vault>/vault.yaml`). Then resolve the topic from the user's
   argument. Multi-word topics fine ("AI memory tools", "vector databases for RAG"). If no
   topic, default to the PURPOSE as the subject. Bias queries and source selection toward
   the PURPOSE, and prefer recent, on-purpose sources; note clearly off-purpose findings
   rather than expanding scope into them.

2. **Choose the research ENGINE.** Precedence: an explicit flag in `$ARGUMENTS`
   (`--claude` | `--perplexity` | `--free`) > the vault default
   (`python -m agents.vault_config engine`) > `claude`. Tell the user which engine ran.

3. Run by engine:

   **A) `claude` (default — $0 on the subscription / Agent SDK credit pool, no API key):**
   - Fan out 3–6 targeted **WebSearch** queries, biased to the PURPOSE and the topic; prefer
     recent results (current year). Use `allowed_domains` / `blocked_domains` to focus when useful.
   - **WebFetch** the 3–8 most relevant results and read them in full — do not synthesise from
     search snippets alone. If WebFetch returns a cross-host redirect, call it again on that URL.
   - Synthesize the dossier (sections in step 4). Every Key Fact carries a recency marker + source URL.

   **B) `perplexity` (metered — needs `PERPLEXITY_API_KEY`):**
   - `uv run -m scripts.research.research "<topic>"` from the repo root. It prints a finished
     dossier and saves the AI-first note to `$VAULT_ROOT/research/daily/` itself — show it
     verbatim, surface the path. (Step 4 is already done by the script for this engine.)

   **C) `free` (key-less sources — $0 but shallow):**
   - `uv run -m scripts.research.research "<topic>" --free` (add `--academic` to restrict to
     arXiv / Semantic Scholar / OpenAlex / CrossRef). The script prints a JSON block
     (`"mode": "free-sources"`) with `results`, `stats`, `warnings`. YOU synthesize the dossier:
     read it; if `stats.success` is false (fewer than 3 sources), flag the thin coverage in Open
     Questions — do not pad.

4. **Save the dossier** (engines A and C — engine B's script already saved it). Write an AI-first
   note at `$VAULT_ROOT/research/daily/YYYY-MM-DD - <slug>.md`, starting from
   `$VAULT_ROOT/wiki/sources/_template.md` and following `/home/jpietrak/second_brain/skills/references/ai-first-rules.md`
   (preamble; frontmatter `type: research`, `ai-first: true`, `created`, `updated`,
   `engine: <claude|perplexity|free>`, a `sources` list of every result URL verbatim, tags).
   Append one line to `$VAULT_ROOT/wiki/log.md`. Dossier sections: Summary, Key Facts (recency
   markers), Timeline, Key Players, Contrarian Views, Further Reading, Open Questions, Sources.
   Never invent facts to fill a section; thin sources → short/empty section (see anti-fabrication).

5. Plain English triggers: "research [topic]", "look up [topic]", "find me info on [topic]"
   ("do deep research" / "research deep" routes to `/obsidian-research-deep` instead). If the user
   wants full vault-aware synthesis with propagation, suggest `/obsidian-research-deep [topic]`.

6. Errors: for engines B/C the script auto-retries transient failures; surface fatal errors verbatim.

---

**AI-first rule:** Every note created or updated by this command MUST follow `/home/jpietrak/second_brain/skills/references/ai-first-rules.md` - `## For future Claude` preamble, rich frontmatter (`type`, `date`, `tags`, `ai-first: true`, plus type-specific fields), recency markers per external claim, mandatory `[[wikilinks]]` for every person/project/concept referenced, sources preserved verbatim with URLs inline, and confidence levels where applicable. The vault is for future-Claude retrieval - not human reading.

**Anti-fabrication:** Search exhaustively before claiming any note, person, or file is absent - false absence is the most common failure mode - and never invent facts, entities, or dates (mark unknowns as `TBD`). See the anti-fabrication and search-completeness hard rules in `/home/jpietrak/second_brain/skills/references/ai-first-rules.md`.
