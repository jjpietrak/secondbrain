# Fact: Phase 2 plan drafted 2026-06-21

Draft at `plans/phase-2-objectives.md`. NOT YET built (awaiting grill-me).

## Key decisions encoded in the plan

- objective/ is MARKDOWN-NATIVE (no JSON store). agents/objectives.py scans it (mirrors wiki_index.py).
- 7 node types: purpose (single file), topic (T-NNNN), research_question (Q-NNNN, solved:yes/no),
  decision (D-NNNN), research_question_proposal (QP-NNNN), direction (DIR-NNNN), agent_todo (TODO-NNNN).
- hot + index are FILES (objective/hot.md, objective/index.md), not subdirs.
- research agent: LOCAL-ONLY (no web tools); reads wiki/ + objective/; writes objective/(agent area)
  + research/ + meta/nightly_report/; NEVER writes wiki/ or user-only objective nodes.
- decision nodes read at task start via `python -m agents.objectives decisions --json`.
- rbac_guard.py gets a `research` role entry (Wave 1 unit W1c).
- 7 Phase 2 skills: obj-query, obj-synth, obj-reconcile, deep-synthesis, wiki-gaps,
  question-promote, question-solve.
- Scripts to create: scripts/obj_init.py (scaffold, mirrors wiki_init.py),
  agents/objectives.py (markdown-native store).
- 4-wave parallel plan: W1(plumbing), W2(core skills), W3(state-transition+reconcile), W4(integration+demo).

## 8 open risks (surface at grill-me before building)

R1: Phase 1 follow-ups status (esp. dispatch layer #2 - if not done, RBAC not enforced).
R2: obj_init.py separate vs extending wiki_init.py --init-objectives flag.
R3: wiki-gaps write destination (research/query/ vs meta/nightly_report/).
R4: question-promote/solve writing USER-ONLY nodes (RBAC exception design: skill-aware vs staging).
R5: deep-synthesis as SKILL.md vs Python helper script.
R6: research memory = fresh scan vs cached direction/question catalog (recommendation: fresh scan).
R7: obj-synth vs deep-synthesis prompt-fill overlap (extract shared helper?).
R8: research/ legacy content (afd-simulator/daily/notebooklm/youtube) - leave or reconcile.
