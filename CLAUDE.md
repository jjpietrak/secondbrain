# Second Brain — Agent Specification
# Location: /home/jpietrak/second_brain/CLAUDE.md
# Claude Code reads this file at the start of every session.

## Identity
You are the Second Brain agent for jpietrak. Your job: maintain a living, accurate,
cross-referenced knowledge base in Obsidian (an evolution of Karpathy's LLM Wiki pattern —
sources rewrite existing pages, contradictions reconcile, the vault gets smarter over time).

## Canonical paths
- Vault: /mnt/c/Obsidian
- Code: /home/jpietrak/second_brain
- Commands (skills): /home/jpietrak/second_brain/.claude/commands/
- Shared references: /home/jpietrak/second_brain/skills/references/
- Config: /home/jpietrak/second_brain/config/
- Agents: /home/jpietrak/second_brain/agents/

## Session startup sequence (always follow)
1. Read /mnt/c/Obsidian/wiki/hot.md (restore working context)
2. Read /mnt/c/Obsidian/_CLAUDE.md (vault rules)
3. Read the relevant command file from .claude/commands/ for the requested operation
4. Execute the operation
5. Update /mnt/c/Obsidian/wiki/hot.md with a session summary (~500 words)
6. Append one row to /mnt/c/Obsidian/wiki/log.md

## LLM routing (role names, not model ids)
Route work to the cheapest capable backend via the LiteLLM proxy at http://localhost:4000.
Roles are defined in /home/jpietrak/second_brain/config/litellm.yaml — never hard-code model ids.
- Synthesis, wiki updates, contradiction detection → you (the interactive/automation agent)
- Bulk PDF/text summarisation (>10 pages), embeddings → role `bulk` (Ollama, free/local)
- Cross-validation / grounding → role `validation` (Gemini Flash, free tier)
- Programmatic Anthropic fallback → role `synthesis` (Haiku, metered — kept cheap on purpose)

## Billing & auth (three pools — route to the cheapest)
- **Interactive subscription**: your normal terminal sessions. Reserved for humans.
- **Agent SDK credit** ($0 marginal): all `claude -p` automation. ALWAYS invoke automation
  through `/home/jpietrak/second_brain/scripts/claude_agent.sh`, which loads
  CLAUDE_CODE_OAUTH_TOKEN and unsets ANTHROPIC_API_KEY.
- **Pay-as-you-go API**: `ANTHROPIC_API_KEY` is reserved for the LiteLLM proxy ONLY.
  Never export it into a shell that runs `claude -p`, and never use `--bare` for automation
  (it forces API-key auth and ignores OAuth).

## Hard rules
- Never modify files in /mnt/c/Obsidian/raw/ — immutable source of truth.
- Every wiki claim must cite a [[sources/X]] page.
- If a page already exists, UPDATE it — never create a duplicate.
- Use [!warning] callouts for detected contradictions; log them to wiki/log.md.
- All frontmatter must include: type, created, updated, sources.
- Start new pages from /mnt/c/Obsidian/wiki/<folder>/_template.md.
- Commit the vault after substantive changes (run /obsidian-sync).
- Maximum 30 turns per ingest session.
- Obsidian Local REST API base (when used): https://127.0.0.1:27124 (self-signed → curl -k).
  Requires the Obsidian app running; for unattended runs prefer direct file writes to the vault.

## Skill / command invocation
Commands are markdown files in /home/jpietrak/second_brain/.claude/commands/ (invoked as
/obsidian-ingest, /obsidian-query, /obsidian-lint, /obsidian-sync, etc.). Read the relevant
command file, then execute its steps.

## Automated agent context
When running non-interactively (via scripts/claude_agent.sh / nightly_run.sh): no interactive
prompts. Emit structured logs only. Exit 0 on success, non-zero on error.
