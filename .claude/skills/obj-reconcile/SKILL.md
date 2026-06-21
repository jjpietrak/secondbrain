---
name: obj-reconcile
description: >
  Find and resolve inconsistencies in the objective/ graph so it never contains stale
  directions, duplicate questions/proposals, or orphaned nodes without the user knowing.
  Detects: stale directions (serves a now-solved question or superseded by a newer
  direction), duplicate or overlapping research_question_proposal nodes, proposals that
  duplicate an existing open research_question, directions whose serves_question is
  already solved, and research_questions with no assigned topic. Resolution respects RBAC:
  research-owned nodes (direction, research_question_proposal, agent_todo) are updated
  directly (status: superseded / rejected); user-only nodes (research_question, topic,
  purpose, decision) are NEVER edited - inconsistencies touching them are written to
  objective/agent_todo/ as user-facing flags. Adjudication of ambiguous duplicates uses
  the validation Gemini-Flash route (metered, cheap). Updates objective/hot.md and appends
  to objective/index.md after each run. LOCAL-ONLY (no web).
  Triggers on: "obj-reconcile", "/obj-reconcile", "reconcile objectives",
  "reconcile the objective graph", "clean up directions", "stale directions",
  "duplicate questions", "flag stale objectives", "objective graph cleanup",
  "check objective consistency", "are there duplicate proposals".
allowed-tools: Read Edit Write Glob Grep Bash
---

**Ownership: `research` agent.** If you are NOT the `research` subagent (e.g. the main
orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with
`subagent_type: research`, pass the user's full request, let the research agent run the
steps below, and relay its result. Do NOT run the steps yourself - running as the `research`
agent is what activates the RBAC/write-scope boundary. If you ARE the `research` agent,
proceed.

# obj-reconcile: find and resolve inconsistencies in the objective graph

Owner: **research**. Equivalent to `wiki-reconcile` for the objective graph. The objective
graph must never contain silently stale directions, ghost directions whose parent question is
solved, or duplicate proposals that would confuse the Phase 3 crawl agent. Every
inconsistency is either resolved directly (on research-owned nodes) or flagged explicitly as
a user TODO (on user-only nodes). This skill NEVER silently overwrites or deletes.

Reasoning for ambiguous duplicate/overlap classification runs via the **`validation`
Gemini-Flash route** (metered, cheap; same as `wiki-reconcile`). If the proxy is
unreachable, fall back to flagging everything as an ambiguous TODO rather than guessing.

## Task start protocol (MANDATORY)

Before any other step, execute the two-step research agent startup:

### 1 - Read active decision nodes
```bash
eval "$(wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.vault_config env')"
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives decisions --json'
```
Parse the returned JSON. Every `active` decision with `scope: all` or `scope: research`
is a hard constraint for the rest of this task. If `agents.objectives` is not available
(scaffold not yet applied), skip with a warning and proceed.

### 2 - Read research memory
Read `.claude/memory/research/MEMORY.md` and any referenced fact files. Note current phase,
open synthesis threads, and any prior reconcile patterns.

## Step 1 - Resolve vault root and load the objective graph

```bash
eval "$(wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.vault_config env')"
```
This exports `VAULT_ROOT`. All reads/writes use `$VAULT_ROOT`.

Load the full objective graph:
```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives scan'
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives frontier --json'
```

Read all nodes directly from the filesystem for detailed body inspection:
- `$VAULT_ROOT/objective/direction/` - all DIR-NNNN files (status: open|crawled|superseded)
- `$VAULT_ROOT/objective/research_question/` - all Q-NNNN files (solved: yes|no)
- `$VAULT_ROOT/objective/research_question_proposal/` - all QP-NNNN files (status: pending|approved|rejected)
- `$VAULT_ROOT/objective/topic/` - all T-NNNN files (status: active|paused|completed)

Also read `$VAULT_ROOT/research/` with:
```bash
ls "$VAULT_ROOT/research/"*.md 2>/dev/null | grep -E 'Q-[0-9]+'
```
This identifies which research questions have an existing answer file (potential
`question-solve` candidates and input to the "serves solved question" check).

## Step 2 - Detect inconsistencies

Run all five detection passes. Collect findings into a structured list (type, node_id,
path, reason) before applying any changes.

### Pass A - Stale directions (serves a solved question)

For each `direction` with `status: open` or `status: crawled`:
- Read `serves_question` from its frontmatter.
- Look up the matching `research_question` node.
- If that question has `solved: yes` -> flag as STALE-SOLVED.
- If that question does not exist in `objective/research_question/` (orphaned reference) ->
  flag as STALE-ORPHAN.

RESOLUTION (research-owned node): set `status: superseded` on the direction (additive
frontmatter update). See Step 3.

### Pass B - Directions superseded by a newer direction on the same question

For each question Q-NNNN with two or more open directions serving it:
1. Group those directions by `targets_gap` (similar gap targets indicate potential overlap).
2. For each group with more than one direction where the gap targets substantially overlap,
   call the `validation` Gemini-Flash judge:
   - Provide both direction titles, their `targets_gap` fields, their `created` dates, and
     their `reasoning_pattern` summaries (first 200 chars).
   - Ask: "Are these two directions substantially duplicate (same gap, same approach)?
     If yes, which should be superseded (keep the more specific or more recent one)?"
3. If the judge returns YES-DUPLICATE: mark the older/less-specific direction
   `status: superseded`.
4. If the judge is unreachable: flag as AMBIGUOUS-DUPLICATE in `objective/agent_todo/`.

### Pass C - Duplicate or overlapping research_question_proposal nodes

For each pair of `research_question_proposal` nodes with `status: pending`:
1. Compare their body text (proposed question) for substantial overlap.
2. If body word-overlap > 60% (simple token intersection heuristic) OR titles are
   near-identical (Levenshtein distance < 5 chars ignoring case): call the `validation`
   judge with both proposal texts.
   - Ask: "Are these two research question proposals asking essentially the same thing?
     If yes, which one is more precisely stated?"
3. If YES-DUPLICATE: mark the less precise one `status: rejected` with
   `rejection_reason: duplicate of QP-NNNN` in the frontmatter.
4. If unreachable: write a `TODO` node flagging the pair.

### Pass D - Proposals that duplicate an existing open research_question

For each `research_question_proposal` with `status: pending`:
1. Compare its proposed question text against all open (`solved: no`) `research_question`
   bodies.
2. If word-overlap > 60% OR near-identical title: the proposal is redundant.
   - RESOLUTION (research-owned): set `status: rejected`,
     `rejection_reason: duplicates existing question Q-NNNN`.

### Pass E - Research questions with no topic

For each `research_question` with `topic` frontmatter field empty or missing:
- This is a USER-ONLY node - do NOT edit it.
- FLAG: write an `objective/agent_todo/TODO-NNNN-no-topic-Q-NNNN.md` node pointing to
  the question, suggesting the user assign a `topic: T-NNNN` field.

## Step 3 - Apply research-owned resolutions (additive only)

For each finding in Pass A, B, C, D that involves a research-owned node (direction,
research_question_proposal):

1. Read the file.
2. Update the `status` field in the frontmatter (targeted in-place edit, do NOT rewrite
   the full body):
   - STALE-SOLVED / STALE-ORPHAN / SUPERSEDED: `status: superseded`
   - DUPLICATE-PROPOSAL / REDUNDANT-PROPOSAL: `status: rejected`
3. Add a `superseded_reason:` or `rejection_reason:` field immediately after `status:` in
   the frontmatter, e.g.:
   ```yaml
   status: superseded
   superseded_reason: serves_question Q-0001 which is now solved (2026-06-21)
   ```
   Or:
   ```yaml
   status: rejected
   rejection_reason: duplicate of QP-0002 (judge confirmed 2026-06-21)
   ```
4. Update the `updated:` field to today's date.

Do NOT delete any file. Do NOT modify the `id:`, `created:`, or `generated_by:` fields.
Do NOT modify user-only nodes (purpose, topic, research_question, decision).

## Step 4 - Write agent_todo flags for user-only inconsistencies

For each finding in Pass E (no-topic questions) and for each AMBIGUOUS-DUPLICATE from
Pass B or C where the judge was unreachable:

1. Allocate a TODO-NNNN id:
   ```bash
   wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives next-id agent_todo'
   ```
2. Write `$VAULT_ROOT/objective/agent_todo/TODO-NNNN-<slug>.md`:
   ```yaml
   ---
   type: agent_todo
   id: TODO-NNNN
   created: YYYY-MM-DD
   updated: YYYY-MM-DD
   generated_by: research
   status: open
   ---
   ```
   Body (use the appropriate template below):

   **For no-topic question (Pass E):**
   ```markdown
   ## For future Claude
   This TODO was written by obj-reconcile on YYYY-MM-DD. It flags a research_question that
   has no assigned topic. The user should assign a topic: T-NNNN field to this question.

   ## Flag: research_question with no topic
   **Question:** [[objective/research_question/Q-NNNN-<slug>]]
   **Issue:** The `topic:` field is empty or absent. This question will not appear in any
   topic-scoped analysis until a topic is assigned.
   **Suggested action:** Edit `objective/research_question/Q-NNNN-<slug>.md` and set
   `topic: T-NNNN` to link it to the most appropriate active topic.
   ```

   **For ambiguous duplicate (judge unreachable):**
   ```markdown
   ## For future Claude
   This TODO was written by obj-reconcile on YYYY-MM-DD. It flags two nodes that may be
   duplicates but could not be adjudicated because the validation proxy was unreachable.
   The user or a future reconcile run should resolve this.

   ## Flag: possible duplicate (adjudication pending)
   **Node A:** [[<path-A>]]
   **Node B:** [[<path-B>]]
   **Issue:** These nodes have substantially overlapping content. The validation judge was
   unreachable. One may be redundant.
   **Suggested action:** Compare both nodes and either (a) reject the weaker one via
   obj-reconcile or (b) merge their content into the stronger one.
   ```

   **For user-facing overlap (Pass E variant - Q and Q overlap):**
   ```markdown
   ## Flag: overlapping research questions - consider merging
   **Question A:** [[objective/research_question/Q-NNNN-<slug>]]
   **Question B:** [[objective/research_question/Q-MMMM-<slug>]]
   **Issue:** These two questions substantially overlap. Consider merging them into one
   canonical question. If they are distinct, add a note in each question body explaining
   why they are separate.
   **Suggested action:** Edit the questions directly, then run obj-reconcile again to
   confirm no duplication remains.
   ```

## Step 5 - Update objective/hot.md (locked write)

Acquire the lock, update, then release:

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && bash scripts/wiki-lock.sh acquire objective/hot.md'
# ... write $VAULT_ROOT/objective/hot.md ...
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && bash scripts/wiki-lock.sh release objective/hot.md'
```

On rc=75 (lock held): retry once after 2s; if still held, log a warning to
`objective/agent_todo/TODO-NNNN-hot-update-deferred.md` and skip the hot update.

hot.md content format:
```yaml
---
type: hot
updated: YYYY-MM-DD
generated_by: research
---
```
Body: current reconcile run summary - what was stale, what was superseded, what was flagged
for the user, and any open ambiguous pairs still pending adjudication.

## Step 6 - Append to objective/index.md (locked write)

Acquire the lock, append one row to the `## Operation log` table, then release. Use the
same acquire/retry/release pattern as Step 5.

Row format:
```
| obj-reconcile | <N> superseded, <M> rejected, <P> TODOs written | research | YYYY-MM-DD | <brief note> |
```

Alternatively, run `python -m agents.objectives scan` to rebuild the index (it preserves
existing log rows).

## Locking (shared-target writes)

`objective/hot.md` and `objective/index.md` are shared append targets. Per the locking
reference (`skills/references/locking.md`), acquire in sorted-path order:
`objective/hot.md` < `objective/index.md` alphabetically.

```bash
LOCK="scripts/wiki-lock.sh"
PATHS=$(printf '%s\n' objective/hot.md objective/index.md | sort)
held=()
for p in $PATHS; do
  if bash "$LOCK" acquire "$p" || { sleep 2; bash "$LOCK" acquire "$p"; }; then
    held+=("$p")
  else
    echo "wiki-lock: $p held -> skipping hot/index update" >&2; break
  fi
done
# ... write hot.md + index.md while held ...
for p in "${held[@]}"; do bash "$LOCK" release "$p"; done
```

Run within WSL (prepend `wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && ...'`).

## Adjudication route

The `validation` Gemini-Flash route is used for two decisions:
1. Pass B: are two directions for the same question substantially duplicate?
2. Pass C: are two pending proposals asking essentially the same thing?

Call via the LiteLLM proxy at `http://localhost:4000/v1/chat/completions`, model
`validation`. Temperature 0. System prompt: "You are a concise duplicate-classification
judge. Reply with YES-DUPLICATE or NOT-DUPLICATE followed by a one-sentence reason."

If the proxy is unreachable (connection error or non-200), do NOT fail the run. Instead:
- Set adjudication result to AMBIGUOUS.
- Write a `TODO` node for the pair (Step 4 template above).
- Continue with the rest of the reconcile run.

Cost: one API call per ambiguous pair, Gemini Flash rate (sub-$0.001 per call). Log each
call to the cost tracker if available.

## RBAC boundaries

- **MAY write:**
  - `objective/direction/*.md` (status field update only - additive frontmatter)
  - `objective/research_question_proposal/*.md` (status + rejection_reason - additive)
  - `objective/agent_todo/TODO-NNNN-*.md` (new files only)
  - `objective/hot.md` (last reasoning context, locked write)
  - `objective/index.md` (operation log append, locked write)
- **MUST NOT write:**
  - `wiki/**` (any path)
  - `objective/purpose/` (user-only)
  - `objective/topic/` (user-only)
  - `objective/research_question/` (user-only; inconsistencies there go to agent_todo/)
  - `objective/decision/` (user-only)
  - `raw/**`, `meta/ingest_index*`
  - `meta/health_report/`, `meta/cost_report/`, `docs/`, `agents/`, `scripts/`

The `PreToolUse` RBAC guard (`scripts/rbac_guard.py`) enforces this allowlist. Any
denied write is a hard stop - do not attempt to work around it.

## Output report

After all steps complete, print a summary:

```
obj-reconcile complete (YYYY-MM-DD):
  Directions superseded  : N  (stale-solved: A, stale-orphan: B, duplicate: C)
  Proposals rejected     : M  (duplicate-proposal: D, redundant-vs-question: E)
  agent_todo flags written: P
    - no-topic questions  : F
    - ambiguous duplicates: G
    - overlapping RQs     : H
  hot.md updated         : yes | deferred (lock held)
  index.md updated       : yes | deferred (lock held)
  Adjudication calls     : N (validation proxy) | 0 (proxy unreachable - all flagged)
```

## Conventions

- Follow [`skills/references/ai-first-rules.md`](../references/ai-first-rules.md) and
  [`write-rules.md`](../references/write-rules.md). ASCII only - no em-dashes, curly
  quotes, or Unicode math. Use ` - ` for dashes.
- Frontmatter edits are ADDITIVE (add `superseded_reason:` / `rejection_reason:` /
  update `updated:`) - never delete fields or change `id:`, `created:`, `generated_by:`.
- Anti-fabrication: never invent a reason for supersession. Every `superseded_reason:`
  traces to a specific finding (Q-NNNN.solved=yes, or judge-confirmed duplicate of
  DIR-MMMM).
- Cost: reconcile scan + logic = research agent credit pool ($0 marginal). Adjudication
  = Gemini Flash validation (metered, sub-$0.001 per call). If budget is tight, skip
  the duplicate-classification passes (B and C) and flag everything as ambiguous TODO.
- Slug generation for agent_todo files: lowercase, hyphens, max 40 chars, ASCII.
  Example: "no-topic-Q-0003" or "ambig-dup-DIR-0002-DIR-0004".
