# Phase 2 live demo — research + wiki agents over the live vault (2026-06-22)

First real multi-agent run over `Inference-Disagg` via the actual subagents (NOT a
build wave): dispatch + RBAC + reasoning validated end-to-end. Code commit `5544c70`,
vault commit `e3ad8b3`. Not pushed.

## What ran (real LLM, on the agent credit pool — $0 metered; no LiteLLM/pay-as-you-go call)
- **research / obj-synth** → 5 directions `DIR-0001..0005` (one per open question) +
  proposal `QP-0001` (software disagg primitives). Updated `objective/index`+`hot` (locked).
- **research / deep-synthesis Q-0001** → `research/Q-0001.md` (144 lines): derived the AFD
  dead-zone phase diagram from Baidu Eq.7 with `[[wikilink]]` citations; flagged
  "answerable now (criteria A+B); criterion C = optical fabric scale-out BW deferred to P3".
  Did NOT set `solved` (user's call). Counts now: 7 RQ open, 5 directions, 1 proposal.
- **wiki / wiki-gaps** → `wiki/gaps.md` (180 lines): Open-Question Harvest (32 items from 11
  pages) + 10 gaps. Touched only `wiki/gaps.md`+`log`.

## RBAC validated LIVE
- research wrote only `objective/` agent-area + `research/`; wiki wrote only `wiki/`.
- No guard blocks fired; no user-only objective node or cross-agent write attempted. No web.

## Bug the demo surfaced (FIXED in 5544c70)
- `scripts/wiki_gaps_gather.py::_extract_section` matched `## Open Questions` by EXACT
  equality → missed parenthetical headers (`## Open questions (TBD for [[iris-tetra]])`),
  reporting `open_question_count: 0`. Fixed to `startswith` + a level-2-heading guard
  (`line.strip().startswith("## ")`). New regression test; 27/27 + full suite 293 passed.

## CLI gotcha (no code change; agent memory corrected)
- `agents/objectives.py --json` is a GLOBAL flag — must precede the verb
  (`--json frontier`, not `frontier --json`). The research agent mis-recorded it as
  unsupported; corrected in `.claude/memory/research/MEMORY.md`. Optional later ergonomics
  fix: also accept `--json` post-verb (argparse subparser-default clobber risk — do it with
  `default=SUPPRESS` or a parent parser; NOT done, to avoid regressing the pre-verb form).

## Phase 2 — what's left (NOT yet exercised live)
- `question-promote QP-0001` → `research_question/Q-0008` (user-gated, R4 SB_SANCTIONED_SKILL).
- `question-solve` on a question (user SOLVED authority).
- `obj-reconcile` (contradiction/dup adjudication) + `obj-query` (read path) — not run.
- **Legacy `research/` reconcile (R8) — Checkpoint B, NOT done.** Must be dry-run-on-copy →
  user diff approval before any move; touches hand-authored `research/afd-simulator/` (do
  not blind-move). This is a stop-and-confer gate.
