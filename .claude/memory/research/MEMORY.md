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

## obj-reconcile run (2026-06-22)

- All 5 passes clean: 0 stale dirs, 0 dup proposals, 0 no-topic Qs.
- D-0001 (no-web) blocks validation proxy -> any future ambiguous-duplicate case must go to TODO, not adjudication.
- No TODO nodes created; no direction or proposal files modified.

## Objective graph state (updated 2026-06-22)

- Current open research questions: 7 (Q-0001..Q-0007), none solved.
- Current open directions: 5 (DIR-0001..DIR-0005), all emitted 2026-06-22 by obj-synth.
- Current proposals: 1 (QP-0001 software disagg primitives), pending user approval.
- Last synthesis run: obj-synth + deep-synthesis (Q-0001), 2026-06-22.

## Synthesis decisions (updated 2026-06-22)

- `--json` IS supported but it is a GLOBAL flag that must come BEFORE the verb:
  `python -m agents.objectives --json frontier` / `--json decisions` (NOT `frontier --json`).
  Post-verb `--json` errors with "unrecognized arguments" - that is placement, not absence.
- next-id uses hyphen not underscore: `next-id direction`, `next-id research_question_proposal`.
- Write files to vault via wsl.exe bash + python3 (Windows path EPERM blocks Write tool).
- Heredoc in wsl.exe bash breaks on parentheses in content; use python3 inline or Write tool
  to UNC path (\\wsl.localhost\ubuntu\tmp\...) then copy with python3.
- gather_local_context + excerpts_to_wiki_baseline work correctly for keyword scoring.
- obj-synth reasoning pattern: B_rank formula derivation from Baidu sources is strong signal.
- Deep zone analysis: H800 dead zone for DeepSeek-V3 at NF<=2; Step-3 essentially immune.
- Minimum B_ScaleOut to eliminate dead zone at NF=X for DeepSeek-V3: 20*X GB/s.

## Question -> answer linkage

- Q-0001 -> research/Q-0001.md (partial, 2026-06-22): criteria A+B answered analytically;
  criterion C (optical fabric scale-out BW) deferred to Phase 3 (DIR-0004 needed).
- Q-0002..Q-0007 -> no reports yet.

## RBAC R4 contract reminder

The `question-promote` and `question-solve` skills MUST export `SB_SANCTIONED_SKILL` before
writing to `objective/research_question/`. See `.claude/agents/research.md` for the full contract.
Wave-3 skill authors: enforce this or the RBAC guard will block the write.
