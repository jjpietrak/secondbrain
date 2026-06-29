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

## Objective graph state (updated 2026-06-24)

- Current open research questions: 6 (Q-0001..Q-0003, Q-0005..Q-0007); Q-0004 SOLVED.
- Current open directions: 6 (DIR-0001..DIR-0006), DIR-0006 is the Q-0004 solve provenance record.
- Current proposals: 2 (QP-0001 software disagg primitives, QP-0002 Iris Tetra optical node extension), both pending user approval.
- Last skill run: question-solve Q-0004, 2026-06-24.
- Q-0004 SOLVED. DIR-0002 unblocked (PIM path is Iris Tetra extension template). Q-0002 is the next deep-synthesis candidate.

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

## Objective graph state (updated 2026-06-26, post-correction)

- Current open research questions: 5 (Q-0001..Q-0003, Q-0005..Q-0006); Q-0004 SOLVED.
- Current open directions: 9 (DIR-0001..DIR-0009; DIR-0006 in complete/ as provenance record).
  DIR-0007/0008/0009 bodies corrected for Tetra=FFN-only role 2026-06-26.
- Current proposals: 4 (QP-0001, QP-0002, QP-0003, QP-0004), all pending user approval.
- Last skill run: obj-reconcile + arch-correction, 2026-06-26.
- Report: research/deep/2026-06-26-megascale-infer-pa-afd-disagg-chunked-prefill.md (revised)

## Key synthesis: AFD + chunked prefill (2026-06-26) -- CORRECTED 2026-06-26

CRITICAL ARCHITECTURE CORRECTION: Tetra = FFN compute only (streaming matmul). Cannot do
SIMT-based attention. GPU/AMD = Attention compute (SIMT). This is WITHIN-PREFILL FFN-Attn
disaggregation, not just prefill-vs-decode:
  - Prefill pair: Tetra (FFN layers) + GPU/AMD (Attention + KV cache) per chunk per layer
  - Decode: GPU/AMD (attn + KV) + Cerebras (FFN-only, stateless)
  - KV cache is GPU/AMD owned throughout; Tetra does NOT generate or transfer KV cache

- AFD dead zone translates to C space for Tetra FFN utilisation: C_min_afd ~= 4K-8K tokens
  for DeepSeek-V3-class MoE on Tetra. Below C_min_afd, Tetra FFN disaggregation is harmful.
- New GAP-B2: activation BW at per-layer Tetra <-> GPU/AMD boundary (58 MB per layer at
  C=8192, hidden=7168, fp8). At 256 GB/s this takes 0.23 ms per layer -- may dominate.
- 3BO asymmetric: Tetra FFN path = stochastic (Lstab jitter); GPU/AMD Attn = deterministic.
  3BO feasibility must be assessed separately per path.
- 128K KV streaming at 256 GB/s = 7.8 ms applies to GPU/AMD -> GPU/AMD decode handoff.
- Algorithm 1 extension needs: separate na_attn + na_ffn search variables, activation BW
  model at Tetra <-> GPU/AMD boundary, Lstab distribution for Tetra FFN path.

## obj-reconcile (2026-06-26)

- 5 passes clean after correction.
- Pass A: 0 stale (9 directions all have associated Qs)
- Pass B: 0 dup proposals (4 proposals distinct)
- Pass C: 0 uncovered questions (all 6 open Qs have directions)
- Pass D: 3 direction nodes corrected (DIR-0007/0008/0009) for wrong Tetra role
- Pass E: QP-0003/0004 remain valid under corrected architecture
- Report revised: research/deep/2026-06-26-megascale-infer-pa-afd-disagg-chunked-prefill.md

## Question -> answer linkage

- Q-0001 -> research/Q-0001.md (partial, 2026-06-22): criteria A+B answered analytically;
  criterion C (optical fabric scale-out BW) deferred to Phase 3 (DIR-0004 needed).
- Q-0004 -> SOLVED (2026-06-24): solved: yes; answer_ref: wiki/sources/llmservingsim-2-2602.23036.md.
  All 3 acceptance criteria met. No separate research/Q-0004.md written; source page is the answer ref.
  question-solve used SB_SANCTIONED_SKILL=question-solve; index updated (6 open, 1 solved); relink applied.
- Q-0002..Q-0003, Q-0005..Q-0006 -> no reports yet.

## RBAC R4 contract reminder

The `question-promote` and `question-solve` skills MUST export `SB_SANCTIONED_SKILL` before
writing to `objective/research_question/`. See `.claude/agents/research.md` for the full contract.
Wave-3 skill authors: enforce this or the RBAC guard will block the write.
