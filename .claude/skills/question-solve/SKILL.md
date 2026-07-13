---
name: question-solve
description: >
  Mark a research_question as solved. On explicit user decision, update the target
  research_question node (Q-NNNN.md) to set solved: yes and answer_ref: research/<question_id>.md.
  Updates objective/index.md (locked). Does NOT autonomously decide questions are solved -
  the user asserts it. RBAC: research agent writes objective/research_question/ ONLY when
  SB_SANCTIONED_SKILL=question-solve is set. Phase 2 scope: user explicitly invokes this
  skill when they confirm a question has been answered (e.g. by deep-synthesis).
  Triggers on: "solve", "question-solve", "solved", "mark solved", "question answered",
  "mark Q- solved", "solved QP-".
allowed-tools: Read Edit Write Glob Grep Bash
subagent_type: research
---

**Ownership: `research` agent.** If you are NOT the `research` subagent (e.g. the main
orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with
`subagent_type: research`, pass the user's full request, let the research agent run the
steps below, and relay its result. Do NOT run the steps yourself - running as the `research`
agent is what activates the RBAC/write-scope boundary. If you ARE the `research` agent,
proceed.

# question-solve: mark a research question as solved

Owner: **research**. User-invoked state transition: the user explicitly decides that a
`research_question` node (Q-NNNN.md) has been answered and sets `solved: yes` with a
reference to the answer (typically `research/Q-NNNN.md` from a prior `deep-synthesis` run).
The skill reads the question, updates its frontmatter, and logs the action in the objective
index. All file writes respect the R4 RBAC user-proxy exception: the skill sets
`SB_SANCTIONED_SKILL=question-solve` before writing to `objective/research_question/`.

Phase 2 scope: the user explicitly invokes this skill (no autonomous closure).

## Task start protocol (mandatory)

Before any other action:

1. **Read decisions:**
   ```bash
   eval "$(wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.vault_config env')"
   wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.objectives decisions --json'
   ```
   Parse the JSON. Apply any decision with `scope: all` or `scope: research` as a hard
   constraint. If the scaffold is not yet applied, skip gracefully.

2. **Read memory:**
   Read `.claude/memory/research/MEMORY.md` and any referenced fact files.

## Step 0 - resolve vault and parse arguments

```bash
eval "$(wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.vault_config env')"
# $VAULT_ROOT is now set
```

The user provides the question id in the trigger (e.g. "solve Q-0001" or "mark Q-0001
solved"). Parse it:
- Trim whitespace.
- Validate format: `Q-NNNN` (letters + hyphen + 4 digits).
- If format is invalid, ask the user: "Please provide the question id in the format Q-NNNN."

Optionally, the user may also provide the answer reference path (e.g. "solve Q-0001 with
research/Q-0001.md"). If not provided, default to `research/{QUESTION_ID}.md`.

## Step 1 - read and validate the question

```bash
QUESTION_PATH="$VAULT_ROOT/objective/research_question/${QUESTION_ID}-*.md"
ls "$QUESTION_PATH" 2>/dev/null | head -1
```

Read the matched question file. Verify frontmatter fields:
- `type: research_question` present.
- `id: Q-NNNN` matches the user-provided id.
- `solved: "no"` (if already solved, inform the user: "This question is already marked
  solved with answer_ref: {ref}." and stop).

Extract from the question:
- Current frontmatter (to preserve all fields except solved and answer_ref).
- Body: the question text.

## Step 2 - confirm the answer reference with the user

If the user did NOT provide an answer reference, infer it:

```bash
# Check for existing research/Q-{ID}.md
if [ -f "$VAULT_ROOT/research/${QUESTION_ID}.md" ]; then
  ANSWER_REF="research/${QUESTION_ID}.md"
else
  ANSWER_REF=""
fi
```

Display:
```
Question to mark solved:

**ID:** {QUESTION_ID}
**Status:** solved: no (currently open)

**Current question:**
{first 200 chars of body}

**Answer reference:**
{ANSWER_REF or "Not found; please provide the path to the answer file"}

Is this correct? [y/n]
```

If the user says no, ask: "Please provide the full path (e.g. research/Q-0001.md,
research/deep/2026-06-21-analysis.md)."

## Step 3 - update the question file

Use the helper from `scripts/question_lifecycle.py` to build the new frontmatter:

```bash
python -c "
import sys
sys.path.insert(0, os.environ.get('CODE_PATH', '.'))
from scripts.question_lifecycle import mark_question_solved
from pathlib import Path
import datetime

question_file = Path('${QUESTION_PATH}')
content = question_file.read_text()
updated = mark_question_solved(content, '${ANSWER_REF}', today=datetime.date.today().isoformat())
question_file.write_text(updated)
"
```

In the context of this skill:

1. **Acquire RBAC sanction:**
   ```bash
   export SB_SANCTIONED_SKILL=question-solve
   ```

2. **Acquire lock and write:**
   ```bash
   bash scripts/wiki-lock.sh acquire "${QUESTION_PATH}" || { sleep 2; bash scripts/wiki-lock.sh acquire "${QUESTION_PATH}"; }
   
   # Call the helper to generate updated content
   UPDATED_CONTENT=$(python -c "
   import sys
   sys.path.insert(0, os.environ.get('CODE_PATH', '.'))
   from scripts.question_lifecycle import mark_question_solved
   from pathlib import Path
   import datetime
   
   question_file = Path('${QUESTION_PATH}')
   content = question_file.read_text()
   updated = mark_question_solved(content, '${ANSWER_REF}')
   print(updated)
   ")
   
   echo "$UPDATED_CONTENT" > "${QUESTION_PATH}"
   
   bash scripts/wiki-lock.sh release "${QUESTION_PATH}"
   ```

3. **Move the file to the `solved/` subdirectory:**
   After writing the updated frontmatter, move the question file:
   ```bash
   SOLVED_DIR="$VAULT_ROOT/objective/research_question/solved"
   mkdir -p "$SOLVED_DIR"
   SOLVED_PATH="$SOLVED_DIR/$(basename ${QUESTION_PATH})"
   mv "${QUESTION_PATH}" "$SOLVED_PATH"
   ```
   All subsequent references (index.md rows, relink output) must use the new
   `solved/`-prefixed path. The `objective/index.md` node catalog row must also be
   updated to reflect the new path if it stores a path column.

4. **Clear sanction immediately after:**
   ```bash
   unset SB_SANCTIONED_SKILL
   ```

## Step 4 - update objective/index.md (locked)

Read the current `objective/index.md`. Append a row to the operation log table:

```
| solve | Q-{QUESTION_ID} | research | {DATE} | Marked solved with answer_ref: {ANSWER_REF} |
```

Acquire lock, write, release (same pattern as above).

## Step 5 - record in research memory

Optionally, update `.claude/memory/research/MEMORY.md` with a note about which questions
have been solved. This is informational only (the source of truth is the question files).

## Step 6 - report success

Display:
```
**Question marked solved:**
- Question: objective/research_question/solved/{QUESTION_ID}-{slug}.md
- Answer reference: {ANSWER_REF}
- Updated: objective/index.md operation log

The question is now closed (solved: yes) and moved to the solved/ subfolder.
```

## RBAC Notes - R4 user-proxy exception

This skill MUST set and unset `SB_SANCTIONED_SKILL` correctly:

- **Set before write:** Before any Write/Edit to `objective/research_question/`, run:
  ```bash
  export SB_SANCTIONED_SKILL=question-solve
  ```
- **Unset after write:** After the write completes (lock released), run:
  ```bash
  unset SB_SANCTIONED_SKILL
  ```

The RBAC guard (`scripts/rbac_guard.py`) checks for this variable. If it is set to
`question-solve` AND the write target is `objective/research_question/`, the write is
ALLOWED. If it is unset or set to a different value, the write is DENIED.

This is the R4 user-proxy exception: the user decides when a question is solved, so the
write is "the user's write, proxied through the research agent." All other research writes
to `objective/research_question/` remain denied.

## Boundaries

- Writes to `objective/research_question/Q-NNNN.md` (frontmatter update), then moves it
  to `objective/research_question/solved/Q-NNNN.md`. Also writes `objective/index.md`.
- Does NOT autonomously decide questions are solved; the user explicitly asserts it.
- Does NOT create new topics, proposals, or directions; those are separate skills.
- Does NOT write to `wiki/`.
- ASCII only (no em-dashes, curly quotes, or Unicode math).

## Answer reference conventions

- `research/{question_id}.md` - standard per-question answer (e.g. research/Q-0001.md).
- `research/deep/{date}-{slug}.md` - a deep research report that answers the question
  (e.g. research/deep/2026-06-21-hbm-analysis.md).
- `research/{topic_slug}.md` - a topic synthesis that addresses the question
  (e.g. research/inference-disaggregation.md).

## Trigger phrases

- `solve` or `/question-solve`
- "solve Q-" (e.g. "solve Q-0001")
- "mark Q- solved" or "marked solved Q-"
- "question answered" or "research question solved"
- "close question" or "close Q-"
