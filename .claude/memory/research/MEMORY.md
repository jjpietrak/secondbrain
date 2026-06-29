# Research agent memory

Research agent: manages the objective graph (`objective/`) and produces synthesis over
the local vault (`research/`). Owns direction tracking, question lifecycle
(promote/solve), and deep synthesis. Works only with local vault content -- no web
browsing.

Add one fact per file and link it here with a one-line pointer. Never duplicate facts
that are derivable from vault contents or the code.

## Index

_(link fact files here as you add them)_

## Standing conventions

- Write allowlist: `objective/research_question_proposal/`, `objective/direction/`,
  `objective/agent_todo/`, `objective/hot.md`, `objective/index.md`, `research/`,
  `meta/nightly_report/`. Never `wiki/`, `raw/`, `meta/{health,cost}_report/`, `docs/`.
  A PreToolUse RBAC hook enforces this -- see [[rbac]] in backend memory.
- R4 exception: writing to `objective/research_question/` requires
  `export SB_SANCTIONED_SKILL=question-promote` (or `question-solve`) set in the
  subagent's shell environment before the write, unset after.
- `python -m agents.objectives --json <verb>` -- the `--json` flag is GLOBAL and must
  come before the verb. Post-verb placement errors with "unrecognized arguments".
- `next-id` uses hyphens: `next-id direction`, `next-id research_question_proposal`.
