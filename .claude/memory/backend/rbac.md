---
type: reference/architecture
---
# Fact: per-agent RBAC model

Related: [[locking]], [[cost-ledger]], [[retrieval-pipeline]]

Each agent is confined to a write allowlist of vault path prefixes. The enforcement is a
PreToolUse hook (`scripts/rbac_guard.py`) that intercepts Write/Edit/MultiEdit/NotebookEdit
tool calls and denies writes outside the agent's allowlist. Read/Grep/Glob/Bash are not
blocked by this guard (egress is gated separately).

## Role resolution (first hit wins)

1. `$SB_AGENT_ROLE` environment variable.
2. `agent` / `subagent_type` / `agent_id` / `role` fields in the hook event JSON.
3. Unknown role -> guard does NOT block (avoids breaking the top-level interactive agent
   and non-vault code work; only known restricted roles are enforced).

## Per-role write allowlists (vault-relative path prefixes)

**wiki**: `wiki/`, `raw/papers/`, `raw/articles/`, `raw/transcripts/`, `raw/notes/`,
`raw/opinions/`, `raw/assets/`, `raw/code/`, `raw/notebooklm/`, `meta/ingest_index`.

**research**: `objective/research_question_proposal/`, `objective/direction/`,
`objective/agent_todo/`, `objective/hot.md`, `objective/index.md`, `research/`,
`meta/nightly_report/`.

**web**: `meta/nightly_report/` only. (The ingest index enqueue is a Bash CLI call, not
a Write tool, so it is not governed by this guard.)

## R4 user-proxy exception (research role)

`objective/research_question/` is user-only territory. The research agent may write there
ONLY when `$SB_SANCTIONED_SKILL` equals `question-promote` or `question-solve`. These two
skills are user-invoked, user-approved state transitions. All other objective/ sub-folders
(`purpose/`, `topic/`, `decision/`) remain denied regardless of SB_SANCTIONED_SKILL.

Contract for skill authors: set `export SB_SANCTIONED_SKILL=<skill-name>` before the
write to `objective/research_question/` and unset it after. The variable must be in the
subagent's own shell environment (not a child subprocess).

## Dispatch pattern

Skills are owned by their respective subagent. To activate RBAC for a write, skills must
be dispatched to the subagent that owns the target path. A skill writing wiki/ paths must
run under the wiki agent (SB_AGENT_ROLE=wiki). Cross-agent writes are denied by design.

## Guard posture

The guard is a backstop. The primary enforcement is the agent definition
(`.claude/agents/<id>.md`) which documents the write allowlist in plain text so the LLM
internalizes it before calling any tool. The hook catches any case where the LLM ignores
or misreads the allowlist.
