---
name: obj-synth
description: >
  Objective graph synthesis. Reads the open research frontier (unsolved research_questions
  + open directions) and relevant wiki context, then runs the two-lane research pipeline
  (RESEARCH_ANALYSIS_PROMPT + RESEARCH_SYNTHESIS_PROMPT) to emit new objective/direction/
  and objective/research_question_proposal/ nodes. Updates objective/hot.md and appends to
  objective/index.md. LOCAL-ONLY (no web). The emitted directions are Phase 3's crawl inputs.
  Triggers on: "synthesize objectives", "update directions", "obj-synth", "/obj-synth",
  "generate research directions", "update the objective graph", "what should we research next",
  "synthesize the research frontier", "produce crawl directions".
allowed-tools: Read Edit Write Glob Grep Bash
---

**Ownership: `research` agent.** If you are NOT the `research` subagent (e.g. the main
orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with
`subagent_type: research`, pass the user's full request, let the research agent run the
steps below, and relay its result. Do NOT run the steps yourself - running as the `research`
agent is what activates the RBAC/write-scope boundary. If you ARE the `research` agent,
proceed.

# obj-synth: synthesize the objective graph into research directions

Owner: **research**. This skill reads the open research frontier (unsolved
`research_question` nodes + existing open `direction` nodes), gathers relevant wiki
context, and runs the two-step research pipeline to produce:

- New `objective/direction/DIR-NNNN-<slug>.md` nodes (crawl/reasoning trajectories for
  the Web agent in Phase 3).
- New `objective/research_question_proposal/QP-NNNN-<slug>.md` nodes (gaps that serve
  the vault purpose but fit no current open question; awaiting user approval).
- An update to `objective/hot.md` (last reasoning context).
- An operation row appended to `objective/index.md`.

Synthesis reasoning runs on the **research agent credit pool** ($0 marginal). No web
access. No writes to `wiki/` or user-only objective nodes.

## Task start protocol (MANDATORY)

Before any other step, run the two required research agent startup actions:

```bash
# 1. Read active decision nodes (hard constraints for this task)
wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.objectives decisions --json'
```

Parse the JSON. Every decision with `scope: all` or `scope: research` is a hard constraint
you must not violate. Typical hard constraints include: no web writes, no wiki writes,
local-vault-only reasoning.

```bash
# 2. Read research memory
# Read .claude/memory/research/MEMORY.md and any referenced fact files.
```

Note open threads, prior synthesis decisions, and the current phase state.

## Step 1 - Resolve vault root

```bash
eval "$(wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.vault_config env')"
```

This exports `VAULT_ROOT`. All subsequent reads/writes use `$VAULT_ROOT`.

## Step 2 - Load the objective frontier

```bash
wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.objectives frontier --json'
```

This returns a JSON object with:
- `research_questions`: list of unsolved research_question nodes (ranked by priority)
- `directions`: list of open direction nodes (ranked by priority)
- `combined_ranked`: combined priority-ranked list

Also read `objective/purpose/PURPOSE.md` for the vault purpose statement. Read
`objective/topic/*.md` for active topics. These are the inputs to the pipeline.

If `objective/` is not yet scaffolded (exit non-zero from the frontier command), run:
```bash
wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python scripts/obj_init.py --apply'
```
then retry.

## Step 3 - Gather wiki context

Use `scripts/research_synthesis.py` to keyword-score the wiki for pages relevant to the
open questions and active topics:

```bash
wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python scripts/obj_synth_helper.py \
  --gather-context "<question_text_and_topics_combined>" \
  --vault-root "$VAULT_ROOT" \
  --frontier-json /tmp/frontier.json'
```

Alternatively, call `gather_local_context` and `excerpts_to_wiki_baseline` from
`scripts/research_synthesis.py` directly from a Python helper if you have the frontier
JSON available. The result is a `{wiki_baseline}` block ready for the prompts.

If `scripts/retrieve.py` is provisioned (`.vault-meta/bm25/index.json` exists), prefer
running `retrieve.py` for each open question to get higher-quality candidates:

```bash
if [ -f "$VAULT_ROOT/.vault-meta/bm25/index.json" ]; then
  wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python scripts/retrieve.py \
    "<question text>" --top 5 --vault-root "$VAULT_ROOT"'
fi
```

Also read `wiki/hot.md` and `wiki/index.md` for top-of-mind context and type/page counts.

If `research/` already has a `gaps-*.md` file from a recent `wiki-gaps` run, read it and
pass its content as the `{wiki_gaps}` parameter. Otherwise use `"(none)"`.

## Step 4 - Fill and run RESEARCH_ANALYSIS_PROMPT

Format the gathered inputs:
- `{purpose}`: body of `objective/purpose/PURPOSE.md`
- `{today}`: today's date (YYYY-MM-DD)
- `{open_questions}`: formatted list from the frontier JSON, one per line:
  `Q-NNNN: <question text> [priority: high/medium/low]`
- `{active_topics}`: list from `objective/topic/*.md`, one per line:
  `T-NNNN: <topic title> [status: active/paused]`
- `{wiki_baseline}`: output of `excerpts_to_wiki_baseline()` from step 3
- `{wiki_gaps}`: gaps content from a recent wiki-gaps run, or `"(none)"`

Use `fill_analysis_prompt()` from `scripts/research_synthesis.py`:

```python
from scripts.research_synthesis import fill_analysis_prompt
prompt = fill_analysis_prompt(purpose, today, open_questions, active_topics,
                               wiki_baseline, wiki_gaps)
```

Reason over this prompt using the research agent credit pool. The output is the
`gap_analysis` block (per-question gaps, cross-cutting gaps, already-answerable list).

## Step 5 - Fill and run RESEARCH_SYNTHESIS_PROMPT

Format the synthesis inputs:
- `{purpose}`: same as above
- `{today}`: same as above
- `{open_questions}`: same as above (now with per-question gap status from step 4)
- `{active_topics}`: same as above
- `{gap_analysis}`: the full output from step 4 (RESEARCH_ANALYSIS_PROMPT output)
- `{existing_directions}`: read existing open `direction` nodes from the frontier JSON;
  format as: `DIR-NNNN: <title> [serves: Q-NNNN]` (one per line). Use `"(none)"` if
  no open directions yet.

Use `fill_synthesis_prompt()` from `scripts/research_synthesis.py`:

```python
from scripts.research_synthesis import fill_synthesis_prompt, parse_directions, parse_proposals
prompt = fill_synthesis_prompt(purpose, today, open_questions, active_topics,
                                gap_analysis, existing_directions)
```

Reason over this prompt. The output contains:
- One or more `### DIRECTION: <title>` blocks
- A `## Proposed New Research Questions` section
- A `## Already Answerable` section

## Step 6 - Parse and write direction nodes

Call `parse_directions(synthesis_output)` to extract direction dicts. For each direction:

1. Allocate an id:
   ```bash
   wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.objectives next-id direction'
   ```
   This returns `DIR-NNNN` and increments the counter in `objective/index.md`.

2. Derive a slug from the direction title (lowercase, hyphens, max 40 chars, ASCII only).

3. Write `$VAULT_ROOT/objective/direction/DIR-NNNN-<slug>.md` with this exact frontmatter
   (fields verbatim - `parse_directions()` and Phase 3 crawl depend on them):

   ```yaml
   ---
   type: direction
   id: DIR-NNNN
   created: YYYY-MM-DD
   updated: YYYY-MM-DD
   generated_by: research
   serves_question: <value from direction.fields.serves_question>
   topics: <value from direction.fields.topics>
   targets_gap: <value from direction.fields.targets_gap>
   priority: <value from direction.fields.priority | split " - " [0] | strip>
   status: open
   ---
   ```

   Body (under `## For future Claude` preamble and then each field as a section):

   ```markdown
   ## For future Claude
   This direction was generated by obj-synth on YYYY-MM-DD. It traces to the gap
   identified in the research analysis and is intended as input to the Phase 3 web crawl.
   Status: open. Do not modify the frontmatter fields - they are machine-parsed.

   ## reasoning_pattern
   <value from direction.fields.reasoning_pattern>

   ## expected_evidence
   <value from direction.fields.expected_evidence>

   ## seed_queries
   <value from direction.fields.seed_queries>

   ## solves_when
   <value from direction.fields.solves_when>
   ```

   NOTE: do NOT take a lock on individual direction files (each is a new, owned file).
   The id allocation (next-id call) locks `objective/index.md` internally.

## Step 7 - Parse and write proposal nodes

Call `parse_proposals(synthesis_output)` to extract proposal dicts. For each proposal
where `parts.get("proposal")` is non-empty and the text does not duplicate an existing
open `research_question`:

1. Allocate an id:
   ```bash
   wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.objectives next-id research_question_proposal'
   ```
   This returns `QP-NNNN`.

2. Derive a slug from the proposal question text (lowercase, hyphens, max 40 chars, ASCII).

3. Write `$VAULT_ROOT/objective/research_question_proposal/QP-NNNN-<slug>.md`:

   ```yaml
   ---
   type: research_question_proposal
   id: QP-NNNN
   created: YYYY-MM-DD
   updated: YYYY-MM-DD
   generated_by: research
   from_gap: <value from proposal.from_gap or "synthesized by obj-synth">
   status: pending
   ---
   ```

   Body:

   ```markdown
   ## For future Claude
   This proposal was generated by obj-synth on YYYY-MM-DD from a gap that fits no current
   open research question. It is PENDING user approval. On approval, run question-promote
   QP-NNNN to create a Q-NNNN research_question node.

   ## Proposed question
   <value from proposal.proposal>

   ## Rationale
   <value from proposal.rationale or derived from context>

   ## From gap
   <value from proposal.from_gap>
   ```

## Step 8 - Surface "Already Answerable" recommendations

The synthesis output's `## Already Answerable` section may list question ids the vault
can already answer. Extract this list and surface it to the user at the end of the run:

```
Already answerable (consider running question-solve):
  - Q-NNNN: <question text>
  ...
```

Also append this note to `objective/hot.md` (see step 9).

## Step 9 - Update objective/hot.md (locked write)

Acquire the lock, update the file, then release:

```bash
LOCK="wsl.exe -- bash -lc 'cd "$CODE_PATH" && bash scripts/wiki-lock.sh acquire objective/hot.md'"
RELEASE="wsl.exe -- bash -lc 'cd "$CODE_PATH" && bash scripts/wiki-lock.sh release objective/hot.md'"
```

Or from within WSL:
```bash
wsl.exe -- bash -lc 'cd "$CODE_PATH" && bash scripts/wiki-lock.sh acquire objective/hot.md'
# ... write $VAULT_ROOT/objective/hot.md ...
wsl.exe -- bash -lc 'cd "$CODE_PATH" && bash scripts/wiki-lock.sh release objective/hot.md'
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
Body: current focus (what obj-synth just reasoned over), open threads (direction ids +
their targets), and "Already Answerable" recommendations.

## Step 10 - Append to objective/index.md (locked write)

Acquire the lock on `objective/index.md`, append one row to the `## Operation log`
table, then release. The row format:

```
| obj-synth | DIR-NNNN..DIR-MMMM (N dirs), QP-NNNN..QP-MMMM (M proposals) | research | YYYY-MM-DD | <brief note> |
```

Use the same acquire/retry/release pattern as step 9.

Alternatively, run `python -m agents.objectives scan` to rebuild the index from scratch
(it preserves existing log rows via the regex extraction in `build_index()`).

## Locking (shared-target writes)

`objective/hot.md` and `objective/index.md` are shared append targets. Apply the locking
pattern from [`skills/references/locking.md`](../references/locking.md). Acquire in
sorted-path order when writing both at once (`objective/hot.md` < `objective/index.md`
alphabetically):

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

Run these commands within WSL (prepend `wsl.exe -- bash -lc 'cd "$CODE_PATH" && ...'`).

## RBAC boundaries

- **MAY write:** `objective/direction/`, `objective/research_question_proposal/`,
  `objective/hot.md`, `objective/index.md`.
- **MUST NOT write:** `wiki/` (any path), `objective/purpose/`, `objective/topic/`,
  `objective/research_question/`, `objective/decision/`, `raw/`, `meta/ingest_index*`,
  `meta/health_report/`, `meta/cost_report/`, `docs/`, `agents/`, `scripts/`.
- The `PreToolUse` RBAC guard (`scripts/rbac_guard.py`) enforces this allowlist. Treat
  any denied write as a hard stop - do not attempt to work around it.

## Output report

After all steps complete, print a summary:

```
obj-synth complete (YYYY-MM-DD):
  Directions written   : N (DIR-NNNN .. DIR-MMMM)
  Proposals written    : M (QP-NNNN .. QP-MMMM)
  Already answerable   : [Q-NNNN, ...]  -- consider question-solve
  hot.md updated       : yes | deferred (lock held)
  index.md updated     : yes | deferred (lock held)
```

## Step 11 - relink objective nodes

After all objective nodes have been written, run relink to generate `## Links` edges and
normalize `written_by` on every objective node:

```bash
wsl.exe -- bash -lc 'cd "$CODE_PATH" && .venv/bin/python -m agents.objectives relink --apply'
```

This (re)generates each node's `## Links` edges and normalizes `written_by` (full
path-qualified wikilinks; no aliases).

## Conventions

- Follow [`skills/references/ai-first-rules.md`](../references/ai-first-rules.md) and
  [`write-rules.md`](../references/write-rules.md). ASCII only - no em-dashes, curly
  quotes, or Unicode math. Use ` - ` for dashes.
- Frontmatter field keys must be VERBATIM (serves_question, topics, targets_gap,
  reasoning_pattern, expected_evidence, seed_queries, solves_when, priority) - these
  are parsed by `parse_directions()` and consumed by the Phase 3 web agent.
- Anti-fabrication: never invent a gap, direction, or proposal. Every direction traces
  to an open question + a specific wiki gap (cite the [[wiki/page]] or direction id).
- Cost: all reasoning = research agent credit pool ($0 marginal). No paid route.
- Slug generation: lowercase, replace spaces/special chars with hyphens, strip leading/
  trailing hyphens, max 40 chars, ASCII only. Example: "what drives hbm bandwidth wall"
  -> "what-drives-hbm-bandwidth-wall".
