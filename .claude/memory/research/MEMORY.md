# Research Agent -- Memory index

One line per memory. Fact files live beside this index (one fact per file). Keep this index
current; never store fact bodies here.

## Project context

- Second Brain v0.2 build on branch `claude/v2-prototype` in `/home/jpietrak/second_brain`.
- Active vault: Inference-Disagg (inference disaggregation research).
- Vault PURPOSE: resolve the role of memory-bandwidth disaggregation in serving large language
  models at scale (see `objective/purpose/PURPOSE.md` once seeded by the user).
- Phase 2 plan: `plans/phase-2-objectives.md`. Research agent definition: `.claude/agents/research.md`.
- Authoritative docs (frozen, read-only): `docs/{requirements,agents,vault-schema,skills-description}.md`.

## Phase 2 - what was built (populate after Phase 2 completes)

- [ ] W1d shipped: `.claude/agents/research.md` created (Phase 2 Wave 1).
- [ ] `scripts/rbac_guard.py` extended with `research` role + R4 sanction signal.
- [ ] W2: skills obj-query, obj-synth, deep-synthesis, wiki-gaps created.
- [ ] W3: skills question-promote, question-solve, obj-reconcile created.
- [ ] W4: `objective/` scaffold applied to Inference-Disagg vault; user seeded purpose/topic/questions.

## Objective graph state (populate after user seeds objective/ nodes)

- Current open research questions: (none yet - pending user seed)
- Current open directions: (none yet - pending obj-synth run)
- Last synthesis run: (none yet)

## Synthesis decisions (populate as patterns are discovered)

- _(record prompt-fill strategies, retrieval depth decisions, ranking choices that worked well)_

## Question -> answer linkage

- _(record Q-NNNN -> research/Q-NNNN.md mappings after question-solve runs)_

## RBAC R4 contract reminder

The `question-promote` and `question-solve` skills MUST export `SB_SANCTIONED_SKILL` before
writing to `objective/research_question/`. See `.claude/agents/research.md` for the full contract.
Wave-3 skill authors: enforce this or the RBAC guard will block the write.
