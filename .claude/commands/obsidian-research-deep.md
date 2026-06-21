---
description: Vault-first deep research - scans the vault, fills gaps, synthesizes a delta, then propagates updates via /obsidian-save. Selectable engine - claude (native WebSearch+WebFetch + deep-research skill, $0, default), perplexity (Sonar+Grok, metered), or free (key-less).
category: research
triggers_en: ["deep research", "thorough research", "vault-first research", "research gaps"]
---

> NOTE: v0.1; superseded by the `research-deep` skill in Phase 2. The `research-deep` skill is not yet built; this command remains available in the interim.

Execute the following for `$ARGUMENTS`:

1. Load the vault **PURPOSE** (`$VAULT_ROOT/_CLAUDE.md` → "## Vault
   purpose", mirrored in the active vault's `config/vaults/<vault>/vault.yaml`) and hold it as a high-priority relevance
   filter for gap analysis and source selection. Then resolve the topic from the user's
   argument. If no topic, default to the PURPOSE as the subject. Gaps and queries should
   advance the PURPOSE; flag clearly off-purpose findings rather than expanding into them.

2. **Choose the research ENGINE.** Precedence: an explicit flag in `$ARGUMENTS`
   (`--claude` | `--perplexity` | `--free`) > the vault default
   (`python -m agents.vault_config engine`) > `claude`. Tell the user which engine ran.

   Phase 1 — **vault scan** — is the same for every engine: find existing notes mentioning the
   topic (the baseline). Do it with Grep/Glob over `$VAULT_ROOT/wiki` + `$VAULT_ROOT/research`,
   or via the Python script's free mode (which prints `vault_baseline_notes`).

3. **`claude` engine (default — $0 on subscription / Agent SDK credit, no API key):** you run
   the full vault-first pipeline yourself.
   - **Gap analysis:** from the vault baseline + PURPOSE, list what's missing/stale → 3–6 targeted
     queries.
   - **Gap-fill:** run the queries with **WebSearch** (prefer current-year results; `allowed_domains`
     to focus), then **WebFetch** the most relevant 4–10 results to read them in full. For maximum
     rigor you may invoke the **`deep-research`** skill (fan-out + adversarial verification of claims)
     and fold its cited report into the synthesis.
   - Produce the delta (sections in step 4), save it, then propagate (step 5).

4. **`perplexity` engine (metered — needs `PERPLEXITY_API_KEY`):**
   `uv run -m scripts.research.research_deep "<topic>"` from the repo root runs a 4-phase pipeline
   (vault scan → Perplexity sonar-pro gap analysis → Perplexity/Grok gap-fill → delta synthesis),
   saves the report to `$VAULT_ROOT/research/deep/YYYY-MM-DD - <slug>.md`, and emits a JSON payload
   between `<<<RESEARCH_DEEP_PROPAGATION_PAYLOAD>>>` markers. Show the synthesis verbatim, parse the
   payload, then propagate (step 5). Cost: typically $0.20–$0.80.

   **`free` engine (key-less — $0, shallow):** `uv run -m scripts.research.research_deep "<topic>" --free`
   (add `--academic` to restrict to scholarly sources). It prints `"mode": "free-sources-deep"` with
   `vault_baseline_notes`, `sources`, `stats`, `warnings`. YOU synthesize the delta from it.

   **Delta sections (engines `claude` and `free`)** — save yourself to
   `$VAULT_ROOT/research/deep/YYYY-MM-DD - <slug>.md`, starting from `$VAULT_ROOT/wiki/synthesis/_template.md`
   and following `/home/jpietrak/second_brain/skills/references/ai-first-rules.md` (preamble; frontmatter
   `type: research-deep`, `ai-first: true`, `created`, `updated`, `engine: <engine>`, `vault-baseline-notes`,
   a `sources` list of every URL verbatim). Sections, exactly: What's New Since Vault Baseline, What's
   Confirmed, Contradictions / Updates Needed (name the `[[vault path]]`), Synthesis, Recommended Vault
   Updates, Open Questions. Every external claim carries a recency marker + source; every vault reference
   uses `[[wikilinks]]`. If coverage is thin (<3 sources), flag it in Open Questions — never pad.

5. **Propagation (all engines):**
   - For the `perplexity` engine, parse the JSON payload; for `claude`/`free`, use the note you just wrote and its synthesis.
   - Treat the synthesis body as the "conversation context" input to `/obsidian-save`.
   - Run the standard `/obsidian-save` flow: spawn parallel subagents (People, Projects, Tasks, Decisions, Ideas) and update vault notes per the synthesis's "Recommended Vault Updates" bullets.
   - Apply the AI-first vault rule on every note created or updated (preamble, frontmatter, recency markers, wikilinks, sources).
   - Link the new research note from today's daily note (`$VAULT_ROOT/daily/`).
   - Then report back a clean list - "Updated [[X]], created [[Y]], linked [[Z]] from today's daily note."

6. Plain English triggers: "do deep research on [topic]", "research properly [topic]", "vault-aware research on [topic]", "research and update the vault on [topic]".

7. If any source/phase fails (a WebFetch redirect/timeout, Grok unavailable in the perplexity
   engine, or a free source times out), continue with what you have and flag the gap in the
   synthesis. Surface partial results — don't silently fail. A partial synthesis beats none.

8. Cost: `claude` engine is $0 on the subscription / Agent SDK credit pool (WebSearch/WebFetch
   draw from that pool, no API key); `perplexity` is metered (~$0.20–$0.80 per run, Perplexity +
   Grok); `free` is $0 (key-less sources, synthesis by the calling Claude).

---

**AI-first rule:** Every note created or updated by this command MUST follow `/home/jpietrak/second_brain/skills/references/ai-first-rules.md` - `## For future Claude` preamble, rich frontmatter (`type`, `date`, `tags`, `ai-first: true`, plus type-specific fields), recency markers per external claim, mandatory `[[wikilinks]]` for every person/project/concept referenced, sources preserved verbatim with URLs inline, and confidence levels where applicable. The vault is for future-Claude retrieval - not human reading.

**Anti-fabrication:** Search exhaustively before claiming any note, person, or file is absent - false absence is the most common failure mode - and never invent facts, entities, or dates (mark unknowns as `TBD`). See the anti-fabrication and search-completeness hard rules in `/home/jpietrak/second_brain/skills/references/ai-first-rules.md`.
