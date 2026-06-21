# Fact: Phase 2 objective/ graph seeded 2026-06-21

## What was done
- Safety checkpoint committed: vault commit 5addc0d (pre-seed state preserved).
- `python scripts/obj_init.py --apply` ran on Inference-Disagg vault: 7 type folders
  + 7 _template.md files + objective/index.md + objective/hot.md created.
- All approved nodes placed from plans/phase-2-seed-proposal.md with the
  user-instructed merge+renumber applied.
- `python -m agents.objectives scan` ran to regenerate index.md.
- next_id counters patched to: topic=9, research_question=8, decision=2.
- Vault commit: baa3996 "seed objective/ graph: purpose + 8 topics + 7 research
  questions + D-0001". NOT pushed.

## RBAC audit trail
User explicitly granted a ONE-TIME exceptional RBAC bypass on 2026-06-21 for
the backend agent to author purpose/topic/research_question nodes (normally
USER-ONLY per vault-schema.md RBAC). This was a sanctioned seeding operation.
The bypass is NOT standing permission -- future runs must not write user-only
objective nodes unless the user grants another explicit bypass.

## Node map placed

purpose/PURPOSE.md  (id=purpose)

topic/ (8 nodes):
  T-0001-disaggregated-inference.md      related_questions: [Q-0006]
  T-0002-kv-cache-systems.md             related_questions: [Q-0003]
  T-0003-llm-serving-systems.md          related_questions: [Q-0004]
  T-0004-layer-expert-disaggregation.md  related_questions: [Q-0001]
  T-0005-heterogeneous-cluster-inference.md  related_questions: []
  T-0006-optical-accelerators.md         related_questions: [Q-0001, Q-0002, Q-0005, Q-0007]
  T-0007-specialised-ai-accelerators.md  related_questions: []
  T-0008-prefill-bound-workloads.md      related_questions: []

research_question/ (7 nodes after merge+renumber):
  Q-0001-afd-dead-zone-optical-bw-target.md  topic=T-0004  priority=high
    [MERGED: old Q-0001 optical-fabric-bw-target + old Q-0006 afd-dead-zone-conditions]
    Secondary topic noted in frontmatter: T-0006
  Q-0002-iris-tetra-simulator-design.md      topic=T-0006  priority=high
  Q-0003-kv-cache-transfer-cost.md           topic=T-0002  priority=high
  Q-0004-llmservingsim-extension.md          topic=T-0003  priority=medium
  Q-0005-optical-prior-art.md                topic=T-0006  priority=medium
  Q-0006-3tier-all-to-all-cost.md            topic=T-0001  priority=medium  [was old Q-0007]
  Q-0007-iris-tetra-arch-design-point.md     topic=T-0006  priority=low     [was old Q-0008]

decision/ (1 node):
  D-0001-no-web-research.md  scope=research  status=active
  [Backend-placed: research agent MUST NOT fetch web in Phase 2; all web via web agent Phase 3]

## next_id counters (post-seed)
topic: 9, research_question: 8, decision: 2,
research_question_proposal: 1, direction: 1, agent_todo: 1

## Frontier output (post-seed)
7 open research questions ranked:
  [high]   Q-0001 AFD dead-zone + optical BW target (merged)
  [high]   Q-0002 Iris Tetra simulator design
  [high]   Q-0003 KV-cache transfer cost
  [medium] Q-0004 LLMServingSim extension
  [medium] Q-0005 Optical prior art
  [medium] Q-0006 3-tier All-to-All cost
  [low]    Q-0007 Iris Tetra arch design point
0 open directions.
