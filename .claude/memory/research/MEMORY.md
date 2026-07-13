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

## obj-synth run (2026-07-13, Q-0002 LLMServingSim vs Frontier synthesis)

- Triggered by: raw/notes/LLMServingSim vs Frontier (internal Lumai comparison note, ingested same day).
- User context: LLMServingSim 2.0 provides all necessary features to model PD and AFD disagg.
- DECISION RESOLVED: LLMServingSim 2.0 is the definitive simulator base. Q-0002 criterion (2) ANSWERED.
  LLMServingSim wins: AFD in code (MSG splitting), 0.95% error, bench/ harness, required ASTRA-sim.
  Frontier (pre-release v0.2): PDD only, AFD explicitly deferred; 16-23% error; no bench harness.
- DESIGN PATTERN: Frontier's ping-pong event graph (ATTN_COMPUTE->A_TO_F->FFN_COMPUTE->F_TO_A per
  layer per micro-batch) to be IMPORTED into LLMServingSim DAG Generator (not a code fork).
- OptiSim role clarified: profile GENERATOR (not runtime). Optical arithmetic intensity profiles
  feed LLMServingSim's extended Operator-Level Profiler.
- DIR-0011 created: optical operator profile schema (Lstab/Lprog/fanalog/etc) + DAG extension +
  attention kernel profile plan. Serves Q-0002 criteria (3) and (7).
- QP-0005 created: simulator selection answer sub-question (pending user approval). Proposes
  formalising criterion (2) answer as a promotable question.
- Objective graph: 11 directions, 2 proposals pending (QP-0004, QP-0005), 7 open questions.
- Q-0002 now has 3 directions: DIR-0002 (criteria 1,2,4,5,6), DIR-0008 (3BO feasibility),
  DIR-0011 (criteria 3,7 -- new).

## obj-synth run (2026-07-10, post-promote direction audit)

- DIR-0007 reassigned: serves_question changed from Q-0001 to Q-0007 (C_min_afd content belongs to Q-0007, not Q-0001).
- DIR-0003 updated: revised framing to match Q-0003 elevated priority; BW_egress formula / FTL / CPO vs PCIe / GAP-05.
- DIR-0010 created: serves Q-0008 (software disagg + compiler design); covers EaaS interface, Iris Tetra FFN-only fit, compiler graph partitioning + A2E/E2A IR + PhaseGate.
- No stray directions found. All 10 active directions cover valid open questions.
- hot.md rewritten: current state table, phase 3 crawl targets, already-answerable list.
- Phase 3 crawl blockers for Q-0008: arXiv 2509.17863 (EaaS) and 2508.02520 (xDeepServe) un-ingested.
- GAP-05 (optical KV layout / format-translation cost) unresolved; blocks DIR-0003 acceptance criterion D.

## Objective graph state (updated 2026-07-13, post question-promote QP-0005 -> Q-0009)

- Current open research questions: 7 (Q-0001, Q-0003, Q-0005..Q-0009); Q-0002 SOLVED, Q-0004 SOLVED.
- Current open directions: 11 (DIR-0001..DIR-0005, DIR-0007..DIR-0011; DIR-0006 in complete/).
  DIR-0011 (2026-07-13): optical operator profile schema + DAG extension for Q-0002 (still active).
- Current proposals: 1 (QP-0004 pending); QP-0005 approved/promoted -> Q-0009.
- Last skill run: question-promote QP-0005 -> Q-0009, 2026-07-13.
- Q-0009 created: definitive LLMServingSim reuse/extension map (simulator selection answer).
  promoted_from: QP-0005; parent_question: Q-0002; priority: high; topic: simulator-design.
  File: objective/research_question/Q-0009-llmservingsim-reuse-extension-map.md
- index.md next_id.research_question = 10 (Q-0009 allocated). next_id.research_question_proposal = 6.
- relink applied after promote.

## Objective graph state (updated 2026-07-10, post-promote QP-0001 -> Q-0008)

- Current open research questions: 7 (Q-0001..Q-0003, Q-0005..Q-0008); Q-0004 SOLVED.
- Q-0007 promoted from QP-0003 (chunked-prefill AFD C_min_afd); user-assigned ID (not auto).
- Q-0008 promoted from QP-0001 (software disagg primitives + serving interface); extended
  with compiler design scope (AFD inter-pool data movement, XLA/TVM/IREE gap, DSL primitives).
  Priority: high. Topic: software-disagg-serving. File: Q-0008-software-disagg-compiler-heterogeneous-inference.md
- Current open directions: 10 (DIR-0001..DIR-0005, DIR-0007..DIR-0010; DIR-0006 in complete/).
  DIR-0007 reassigned Q-0001->Q-0007; DIR-0003 updated for revised Q-0003; DIR-0010 new for Q-0008.
- Current proposals: 1 (QP-0004 pending); QP-0001 (approved/promoted -> Q-0008) deleted 2026-07-10.
- Last skill run: question-promote QP-0001 -> Q-0008 (with compiler design extension), 2026-07-10.
- Report: research/deep/2026-06-26-megascale-infer-pa-afd-disagg-chunked-prefill.md (revised)
- index.md next_id.research_question = 9 (Q-0008 allocated; next auto is Q-0009)

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
- Q-0003 -> REVISED + ELEVATED (2026-07-10): priority raised low -> high. Challenge analysis added.
  Revised framing: NVIDIA BW_egress formula vs. Iris Tetra FTL; PCIe 256 GB/s floor coverage;
  CPO vs. PCIe per-token latency penalty. GAP-05 identified as blocking prerequisite.
  Key finding: beyond-the-buzz "not a bottleneck" conclusion conditional on homogeneous Blackwell
  clusters; does not apply to Tetra (BW_egress proportional to 1/FTL).
- Q-0002 -> SOLVED (2026-07-13): solved: yes; answer_ref: objective/hot.md.
  Criterion (2) met: LLMServingSim 2.0 wins over Frontier (AFD in code, 0.95% error, bench/ harness, ASTRA-sim).
  Remaining design work continues via DIR-0002 (criteria 1,4,5,6), DIR-0008 (3BO), DIR-0011 (criteria 3,7).
  question-solve used SB_SANCTIONED_SKILL=question-solve; file moved to solved/; index updated; relink applied.
- Q-0005..Q-0006 -> no reports yet.
- Q-0003 body update (2026-07-10): added "Known techniques" subsection with F3 (pipelined
  layer-by-layer KV transfer for 128K+) and F4 (ZTE 1D tensor flattening; blocker-dependency
  Q-0003 <-> Q-0008). Written via SB_SANCTIONED_SKILL=question-promote.
- QP-0004 body update (2026-07-10): added "Segment-level plan search" subsection -- segment-level
  Algorithm-1 framing, 6-complexity-dimension mapping, cross-refs to chunked-prefill-afd and
  deployment-plan-search concepts. updated: set to 2026-07-10.
- DIR-0007 body update (2026-07-10): added "C_min_afd contradiction note" between expected_evidence
  and seed_queries. Documents 8K analytical floor vs. 512-4096 empirical AFD2 sweep (lumai-afd-
  modelling-june2026 slide 15). Three reconciliation hypotheses enumerated. solves_when now requires
  reconciliation as fourth condition.

## RBAC R4 contract reminder

The `question-promote` and `question-solve` skills MUST export `SB_SANCTIONED_SKILL` before
writing to `objective/research_question/`. See `.claude/agents/research.md` for the full contract.
Wave-3 skill authors: enforce this or the RBAC guard will block the write.
