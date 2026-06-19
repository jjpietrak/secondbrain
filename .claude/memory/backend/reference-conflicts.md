# Fact: conflicts between reference architectures and our v0.2 plan

From the reference-comparison study (`plans/reference-comparison.md` section 6). These shape how the
ported skills must be adapted; none are blockers but several need user resolution or careful rewrite.

1. **Path remapping required.** Neither repo has our `objective/`, `research/`, `meta/` split, or
   `raw/<type>/` subfolders. CO uses `wiki/comparisons` (= our `synthesis/`) and `wiki/questions`.
   OSB hard-codes `Research/Deep/`, `wiki/sources`, People/Projects/Ideas. Every ported skill must
   remap its hard-coded paths onto our authoritative `docs/vault-schema.md` matrix.
2. **concepts<->entities swap (vault-schema open note #1):** both CO and OSB use the CONVENTIONAL
   mapping (entities = concrete people/companies/tools; concepts = abstract ideas/frameworks). Our v0.2
   matrix has them REVERSED. Reference precedent supports conventional. FLAG for user; backend cannot
   edit docs. Skill ports need the resolved mapping.
3. **RBAC has no precedent to copy.** CO subagents share full tool access; OSB has no roster. The
   `PreToolUse` path-guard hooks that enforce the per-agent permission matrix are 100% new code.
   CO `hooks/hooks.json` shapes are the starting point only.
4. **Cost accounting is a greenfield blocker.** Neither repo records usage. `claude_agent.sh` today
   `exec claude -p` and discards usage. P0 step 4 (capture `--output-format json` usage into
   `cost_tracker.record`) is a hard prerequisite for window budgeting.
5. **research_deep propagation conflicts with RBAC.** OSB `research_deep.py` emits a payload telling
   Claude to run `/obsidian-save` writing across wiki. Our `research` agent CANNOT write `wiki/`. Port
   the gap-analysis/synthesis logic but REWRITE the propagation target to enqueue/route to `wiki` agent
   + `objective/` only.
6. **autoresearch ownership.** Catalog says `research+code`, but its writes land in `wiki/`. Under RBAC
   only `wiki` writes `wiki/`. Resolve: `research` runs the loop + discovers/enqueues; `wiki` files on
   approval. Confirm when building P2/P3.
7. **No nightly/cron in either repo.** All of Phase 4 (multi-agent nightly orchestrator + 5h token-
   window budgeting + agent-learn) and the web-agent accept/reject feedback loop are greenfield.
