---
name: question-promote
description: >
  Promote a research_question_proposal to a research_question. On explicit user approval,
  allocate a new Q-NNNN via `agents.objectives next-id`, read the proposal (QP-NNNN.md),
  create the corresponding research_question node with solved: no, and mark the proposal
  status: approved. Updates objective/index.md (locked). User-invoked (the user triggers +
  approves = the proxy authority). RBAC: research agent writes objective/research_question/
  ONLY when SB_SANCTIONED_SKILL=question-promote is set. Phase 2 scope: user triggers this
  skill explicitly when they approve a proposal for promotion.
  Triggers on: "promote", "question-promote", "approve proposal", "promote QP-",
  "promote research question proposal".
allowed-tools: Read Edit Write Glob Grep Bash
subagent_type: research
---

**Ownership: `research` agent.** If you are NOT the `research` subagent (e.g. the main
orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with
`subagent_type: research`, pass the user's full request, let the research agent run the
steps below, and relay its result. Do NOT run the steps yourself - running as the `research`
agent is what activates the RBAC/write-scope boundary. If you ARE the `research` agent,
proceed.

# question-promote: promote a proposal to a research question

Owner: **research**. User-invoked state transition: the user explicitly approves a
`research_question_proposal` node (QP-NNNN.md) for promotion to a full `research_question`
(Q-NNNN.md). The skill allocates the new Q-NNNN id, creates the question node with
`solved: no`, marks the proposal as `status: approved`, and updates the objective index.
All file writes respect the R4 RBAC user-proxy exception: the skill sets
`SB_SANCTIONED_SKILL=question-promote` before writing to `objective/research_question/`.

Phase 2 scope: the user explicitly invokes this skill (no autonomous promotion).

## Task start protocol (mandatory)

Before any other action:

1. **Read decisions:**
   ```bash
   eval "$(wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.vault_config env')"
   wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives decisions --json'
   ```
   Parse the JSON. Apply any decision with `scope: all` or `scope: research` as a hard
   constraint. If the scaffold is not yet applied, skip gracefully.

2. **Read memory:**
   Read `.claude/memory/research/MEMORY.md` and any referenced fact files.

## Step 0 - resolve vault and parse arguments

```bash
eval "$(wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.vault_config env')"
# $VAULT_ROOT is now set
```

The user provides the proposal id in the trigger (e.g. "promote QP-0001"). Parse it:
- Trim whitespace.
- Validate format: `QP-NNNN` (letters + hyphen + 4 digits).
- If format is invalid, ask the user: "Please provide the proposal id in the format QP-NNNN."

## Step 1 - read and validate the proposal

```bash
PROPOSAL_PATH="$VAULT_ROOT/objective/research_question_proposal/${PROPOSAL_ID}-*.md"
ls "$PROPOSAL_PATH" 2>/dev/null | head -1
```

Read the matched proposal file. Verify frontmatter fields:
- `type: research_question_proposal` present.
- `id: QP-NNNN` matches the user-provided id.
- `status: pending` (if status is already approved or rejected, inform the user: "This
  proposal is already {status}; no promotion needed." and stop).

Extract from the proposal:
- `priority`: high | medium | low (defaults to medium if missing).
- `topic`: the T-NNNN if the proposal specifies it; else empty (user may set this).
- Body: the question text (first line or full body).

## Step 2 - confirm with the user

Display:
```
Proposal to promote:

**ID:** {QP_ID}
**Status:** pending
**Priority:** {priority}
**Topic:** {topic or "(not set)"}

**Question:**
{first 200 chars of body}

Approve the promotion? [y/n]
```

If the user says no (or does not confirm), stop with "Promotion cancelled."

## Step 3 - allocate the new Q-NNNN id

```bash
NEW_ID=$(wsl.exe -- bash -lc "cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives next-id research_question")
echo "Allocated: $NEW_ID"
```

If the command fails, report the error and stop.

## Step 4 - create the question file

Import and call the helper from `scripts/question_lifecycle.py`:

```bash
python -c "
import sys
sys.path.insert(0, '/home/jpietrak/second_brain')
from scripts.question_lifecycle import proposal_to_question_frontmatter
from pathlib import Path

proposal_file = Path('${PROPOSAL_PATH}').read_text()
fm = proposal_to_question_frontmatter(proposal_file, '${NEW_ID}', topic='${TOPIC}')

# Build the frontmatter block.
fm_lines = [
    f'type: {fm[\"type\"]}',
    f'id: {fm[\"id\"]}',
    f'created: {fm[\"created\"]}',
    f'updated: {fm[\"updated\"]}',
    f'solved: {fm[\"solved\"]}',
    f'topic: {fm[\"topic\"]}',
    f'priority: {fm[\"priority\"]}',
    f'answer_ref: {fm[\"answer_ref\"]}',
]
body = open('${PROPOSAL_PATH}').read().split('---')[2].strip()
print(f'---\\n' + '\\n'.join(fm_lines) + f'\\n---\\n\\n{body}')
"
```

Let the helper compute the frontmatter and format it. Then:

1. **Acquire RBAC sanction:**
   ```bash
   export SB_SANCTIONED_SKILL=question-promote
   ```

2. **Write the question file:**
   ```bash
   QUESTION_SLUG=$(echo "${QUESTION_TEXT}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-\|-$//' | cut -c1-60)
   QUESTION_FILE="$VAULT_ROOT/objective/research_question/${NEW_ID}-${QUESTION_SLUG}.md"
   
   # Acquire lock
   bash scripts/wiki-lock.sh acquire "$QUESTION_FILE" || { sleep 2; bash scripts/wiki-lock.sh acquire "$QUESTION_FILE"; }
   
   # Write (SB_SANCTIONED_SKILL is set in environment).
   echo "${QUESTION_CONTENT}" > "$QUESTION_FILE"
   
   # Release lock
   bash scripts/wiki-lock.sh release "$QUESTION_FILE"
   
   # Unset sanction signal
   unset SB_SANCTIONED_SKILL
   ```

3. **Clear sanction immediately after the write:**
   The write is inside a subprocess or shell block. Ensure `SB_SANCTIONED_SKILL` is unset
   in the main skill environment after the locked write completes.

## Step 5 - mark the proposal approved and move it to promoted/

```bash
export SB_SANCTIONED_SKILL=question-promote
```

Use the helper to update proposal frontmatter, then move the file to the `promoted/` subfolder:

```bash
# 1. Update frontmatter in-place (set status: approved, promoted_to: Q-NNNN)
python -c "
import sys
sys.path.insert(0, '/home/jpietrak/second_brain')
from scripts.question_lifecycle import mark_proposal_approved
from pathlib import Path

proposal_file = Path('${PROPOSAL_PATH}')
content = proposal_file.read_text()
updated = mark_proposal_approved(content)
proposal_file.write_text(updated)
"

# 2. Move to promoted/ subfolder to remove it from the active proposal list
PROMOTED_DIR="$VAULT_ROOT/objective/research_question_proposal/promoted"
mkdir -p "$PROMOTED_DIR"
mv "${PROPOSAL_PATH}" "$PROMOTED_DIR/$(basename ${PROPOSAL_PATH})"

unset SB_SANCTIONED_SKILL
```

The `promoted/` subfolder keeps the proposal as a permanent record (traceable from the
new Q-NNNN via `promoted_from:` frontmatter) while removing it from the active pending
list that `agents.objectives frontier` and `obj-reconcile` scan.

## Step 6 - update objective/index.md (locked)

Read the current `objective/index.md`. Append a row to the operation log table:

```
| promote | Q-{NEW_ID} | research | {DATE} | Promoted from {QP_ID} |
```

Acquire lock, write, release (same pattern as above).

## Step 7 - relink objective nodes

After writing the new research_question node and marking the proposal approved, run relink
to generate `## Links` edges and normalize `written_by` on every objective node:

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives relink --apply'
```

This (re)generates each node's `## Links` edges and normalizes `written_by` (full
path-qualified wikilinks; no aliases).

## Step 8 - report success

Display:
```
**Promotion complete:**
- Created: objective/research_question/{NEW_ID}-{slug}.md
- Moved: {QP_ID} -> objective/research_question_proposal/promoted/{QP_ID}-{slug}.md (status: approved)
- Updated: objective/index.md operation log

The research question is now open (solved: no) and ready for synthesis.
The proposal has been archived to promoted/ and is no longer in the active pending list.
```

## RBAC Notes - R4 user-proxy exception

This skill MUST set and unset `SB_SANCTIONED_SKILL` correctly:

- **Set before write:** Before any Write/Edit to `objective/research_question/`, run:
  ```bash
  export SB_SANCTIONED_SKILL=question-promote
  ```
- **Unset after write:** After the write completes (lock released), run:
  ```bash
  unset SB_SANCTIONED_SKILL
  ```

The RBAC guard (`scripts/rbac_guard.py`) checks for this variable. If it is set to
`question-promote` AND the write target is `objective/research_question/`, the write is
ALLOWED. If it is unset or set to a different value, the write is DENIED.

This is the R4 user-proxy exception: the user triggers and approves the promotion, so the
write is "the user's write, proxied through the research agent." All other research writes
to `objective/research_question/` remain denied.

## Boundaries

- Writes to `objective/research_question/` (new Q-NNNN file), `objective/index.md`, and
  moves the promoted proposal from `objective/research_question_proposal/` to
  `objective/research_question_proposal/promoted/` (status: approved, permanent record).
- Does NOT autonomously decide which proposals to promote; the user explicitly approves
  each one.
- Does NOT create new topics or decisions; those are user-only.
- Does NOT write to `wiki/`.
- ASCII only (no em-dashes, curly quotes, or Unicode math).

## Trigger phrases

- `promote` or `/question-promote`
- "promote QP-" (e.g. "promote QP-0001")
- "promote research question proposal"
- "approve proposal"
- "activate proposal"
