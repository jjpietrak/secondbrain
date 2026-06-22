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

## Phase 2 CLOSEOUT (2026-06-22)
- **User signed off P2** ("skip — P2 validated"): the obj-synth/deep-synthesis/wiki-gaps
  live demo is sufficient. `question-promote`/`question-solve` (user-authority, R4) and
  `obj-reconcile`/`obj-query` are NOT separately demoed — they get exercised in real use.
  QP-0001 remains a pending proposal awaiting a real user promote decision.
- **R8 legacy `research/` reconcile DONE** (vault commit `88494fe`). User rule: only `/notes`
  is valuable. Lifted `research/afd-simulator/notes/` (2 hand-authored notes) → `research/notes/`
  (git renames, content preserved). Removed the `afd-simulator/` wrapper + its `.nlm-sync.json`
  (nlm sync is P5; re-establishes from each note's `nlm_note_id` frontmatter) and the empty
  `research/daily/` + `research/notebooklm/` dirs. `research/` now matches plan: `deep/ query/
  notes/ <question_id>(Q-0001.md)`. **P5 flag:** when nlm sync is built, re-key it to the new
  `research/notes/` path (the old per-notebook `.nlm-sync.json` ledger is gone).
- **Phase 2 COMPLETE.** Next per plan = Phase 3 (web), which is GATED on a dedicated Web
  DECISION grill-me (combine `objective/direction` + wiki `gaps.md`, rank, de-dup, crawl-plan)
  before any building. Also pending: `agent-learn` grill-me before Phase 4.
