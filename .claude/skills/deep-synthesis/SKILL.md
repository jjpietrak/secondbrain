---
name: deep-synthesis
description: >
  Deep local reasoning across wiki/ + objective/ to produce research/ reports, per-question
  answers, and topic state-of-knowledge syntheses. Gathers vault context via
  scripts/research_synthesis.gather_local_context, fills RESEARCH_ANALYSIS_PROMPT then
  RESEARCH_SYNTHESIS_PROMPT, writes the report to research/ (routing: --question Q-NNNN ->
  research/Q-NNNN.md; --topic T-NNNN -> research/T-NNNN.md; --deep "topic text" ->
  research/deep/YYYY-MM-DD-<slug>.md), and emits objective/direction/ + proposal nodes from
  synthesis output. Surfaces "Already Answerable" recommendations to the user.
  LOCAL-ONLY: no web, no wiki/ writes.
  Triggers on: "deep synthesis", "research deep", "/deep-synthesis", "synthesize research",
  "deep dive on", "analyze question", "research question", "what does the vault say about",
  "full research report", "deep-synthesis --question", "deep-synthesis --topic",
  "deep-synthesis --deep".
allowed-tools: Read Edit Write Glob Grep Bash
---

**Ownership: `research` agent.** If you are NOT the `research` subagent (e.g. the main
orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with
`subagent_type: research`, pass the user's full request, let the research agent run the steps
below, and relay its result. Do NOT run the steps yourself - running as the `research` agent is
what activates the RBAC/write-scope boundary. If you ARE the `research` agent, proceed.

# deep-synthesis: deep local reasoning into research/ reports

Owner: **research**. Adapted from OSB `research_deep.py` vault-scan + gap/synthesis prompt
pattern (KEEP) with web/Perplexity/external pulls DROPPED entirely. All reasoning runs on the
**research agent credit pool** via the Agent SDK ($0 marginal). No paid route is used.

## Task start protocol (MUST execute first)

Before any other step, execute the two-step research agent task start:

1. **Read decisions:**
   ```bash
   eval "$(wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.vault_config env')"
   wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives decisions --json'
   ```
   Parse the JSON list. Apply every returned `active` decision as a hard constraint. A decision
   with `scope: all` or `scope: research` binds you. If `agents.objectives` is not yet available
   (scaffold not yet applied), skip with a warning and continue.

2. **Read memory:**
   Read `.claude/memory/research/MEMORY.md` and any referenced fact files. Note the current phase,
   open research threads, and any prior synthesis decisions.

## Scope arguments

The skill accepts one of three mutually exclusive scope arguments (user passes them in the
trigger phrase, e.g. "deep synthesis on Q-0001" or "deep-synthesis --question Q-0001"):

| Argument | Output path | Use case |
|----------|-------------|----------|
| `--question Q-NNNN` | `research/Q-NNNN.md` | Full per-question answer |
| `--topic T-NNNN` | `research/T-NNNN.md` | Topic state-of-knowledge |
| `--deep "free text"` | `research/deep/YYYY-MM-DD-<slug>.md` | Open-ended deep report |

When no argument is given, ask the user: "Please specify a scope: --question Q-NNNN, --topic
T-NNNN, or --deep 'topic text'."

## Step 0 - resolve the vault

```bash
eval "$(wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.vault_config env')"
# $VAULT_ROOT is now set
```

## Step 1 - read decisions and memory

Execute the task start protocol above. Record any binding decisions for application in steps
below.

## Step 2 - load the objective frontier

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives frontier --json'
```

Parse the JSON. Extract:
- `research_questions`: list of open (solved=no) research question nodes.
- `directions`: list of open direction nodes.

Also read `$VAULT_ROOT/objective/purpose/PURPOSE.md` to get the vault purpose text.
Read active topics from `$VAULT_ROOT/objective/topic/`.

Format the open questions as:
```
- Q-NNNN [priority]: <question text from filename slug or body first line>
```

Format active topics as:
```
- T-NNNN: <topic slug from filename>
```

If the scope is `--question Q-NNNN`, filter the open questions to only that question for the
prompt fill (but keep all topics). If the vault has no objective/ folder yet, proceed with
empty open_questions and note "(objective/ not yet initialized)" as a warning.

## Step 3 - gather local context

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
import sys, json
sys.path.insert(0, \".\")
from scripts.research_synthesis import gather_local_context, excerpts_to_wiki_baseline
# Pass the scope text as the topic_or_question
hits = gather_local_context(\"<SCOPE TEXT>\", wiki_root=\"$VAULT_ROOT\")
baseline = excerpts_to_wiki_baseline(hits)
print(baseline)
"'
```

Where `<SCOPE TEXT>` is:
- For `--question Q-NNNN`: the question id + text from the objective node body.
- For `--topic T-NNNN`: the topic id + slug text.
- For `--deep "free text"`: the free text verbatim.

Also pass the objective frontier nodes as `objective_nodes` argument so research question and
direction nodes that match the query also surface as context:

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
import sys, json
sys.path.insert(0, \".\")
from agents.objectives import open_frontier, _resolve_vault_root
from scripts.research_synthesis import gather_local_context, excerpts_to_wiki_baseline
from pathlib import Path

vault_root = Path(\"$VAULT_ROOT\")
frontier = open_frontier(vault_root)
all_nodes = frontier[\"research_questions\"] + frontier[\"directions\"]

hits = gather_local_context(
    \"<SCOPE TEXT>\",
    wiki_root=vault_root,
    objective_nodes=all_nodes,
)
baseline = excerpts_to_wiki_baseline(hits)
print(baseline)
"'
```

If the retrieval pipeline is provisioned, PREFER it over the raw keyword scan for the wiki
pages:

```bash
# feature-detect provisioned pipeline
if [ -f "$VAULT_ROOT/.vault-meta/bm25/index.json" ] && [ -d "$VAULT_ROOT/.vault-meta/chunks" ]; then
  wsl.exe -- bash -lc "cd /home/jpietrak/second_brain && .venv/bin/python scripts/retrieve.py \"<SCOPE TEXT>\" --top 8"
fi
```

If `retrieve.py` exits 10 (not provisioned), fall back to `gather_local_context` without error.

## Step 4 - fill and run RESEARCH_ANALYSIS_PROMPT

Prepare the filled prompt:

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
import sys
sys.path.insert(0, \".\")
from scripts.research_synthesis import fill_analysis_prompt
prompt = fill_analysis_prompt(
    purpose=\"<PURPOSE TEXT>\",
    today=\"<TODAY YYYY-MM-DD>\",
    open_questions=\"<FORMATTED OPEN QUESTIONS>\",
    active_topics=\"<FORMATTED ACTIVE TOPICS>\",
    wiki_baseline=\"<WIKI BASELINE FROM STEP 3>\",
    wiki_gaps=\"(none)\",
)
print(prompt)
"'
```

Send the filled prompt to yourself (reasoning on the research agent credit pool). Your response
IS the gap analysis. Parse it:
- Extract the `## Already Answerable` section items.
- Extract the `## Per-Question Gap Assessment` blocks.
- Keep the full analysis text as `<GAP_ANALYSIS>` for Step 5.

Surface the "Already Answerable" items using the helper:

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
import sys
sys.path.insert(0, \".\")
from scripts.deep_synth_helper import format_already_answerable_surface
items = [<LIST OF ALREADY ANSWERABLE ITEMS FROM ANALYSIS>]
surface = format_already_answerable_surface(items)
if surface:
    print(surface)
"'
```

Print the surface text to the user if non-empty.

## Step 5 - fill and run RESEARCH_SYNTHESIS_PROMPT

Format existing open directions for the prompt (to avoid duplicating them):

```bash
existing_directions = "\n".join(
    f"- {d['id']}: {d.get('stem', d['path'])} (serves: {d.get('serves_question', '')}, "
    f"status: {d.get('status', '')})"
    for d in frontier["directions"]
) or "(none)"
```

Prepare and run:

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
import sys
sys.path.insert(0, \".\")
from scripts.research_synthesis import fill_synthesis_prompt
prompt = fill_synthesis_prompt(
    purpose=\"<PURPOSE TEXT>\",
    today=\"<TODAY YYYY-MM-DD>\",
    open_questions=\"<FORMATTED OPEN QUESTIONS>\",
    active_topics=\"<FORMATTED ACTIVE TOPICS>\",
    gap_analysis=\"<GAP_ANALYSIS FROM STEP 4>\",
    existing_directions=\"<EXISTING DIRECTIONS>\",
)
print(prompt)
"'
```

Send the filled synthesis prompt to yourself. Parse the response:
- `parse_directions(synthesis_text)` -> list of direction dicts.
- `parse_proposals(synthesis_text)` -> list of proposal dicts.
- Extract `## Already Answerable` items from synthesis output (may differ from analysis pass).

## Step 6 - write the research/ report

Resolve the output path:

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
import sys
sys.path.insert(0, \".\")
from scripts.deep_synth_helper import resolve_output_path
from pathlib import Path

scope = {\"question\": \"<Q-NNNN>\"}  # or {\"topic\": ...} or {\"deep\": ...}
out = resolve_output_path(Path(\"$VAULT_ROOT\"), scope, today=\"<TODAY>\")
print(out)
"'
```

Create parent directories and write the report. File structure:

```
<frontmatter from format_research_report_frontmatter(scope, today)>

## For future Claude
<Brief context: scope, what was synthesized, date, what remains open.>

## Summary
<2-4 bullets: main findings, with [[wikilinks]] to source pages.>

## Key Findings
<Each finding: one bullet, claim, recency marker if from a dated source, [[wiki/...]] citation.>

## Per-Question Gap Assessment
<Paste the gap blocks from the analysis step for the scoped question(s).>

## Gaps Remaining
<What the vault cannot yet answer. Specific, not vague.>

## Directions Emitted
<List the DIR-NNNN ids written in step 7, with their titles.>

## Already Answerable Recommendations
<The already_answerable list from analysis + synthesis passes (de-duped). If none, say "none".>
```

Locking: the output path is an OWNED FILE (not a shared target) - no lock needed.

```bash
mkdir -p "$(dirname <OUTPUT_PATH>)"
# Write the file
```

## Step 7 - write objective/direction/ nodes

For each direction returned by `parse_directions()`:

1. Get the next DIR id:
   ```bash
   wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives next-id direction --vault-root "$VAULT_ROOT"'
   ```

2. Format and write the direction node:
   ```bash
   wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
   import sys
   sys.path.insert(0, \".\")
   from scripts.deep_synth_helper import format_direction_frontmatter
   fm = format_direction_frontmatter(
       \"<DIR-NNNN>\", \"<TODAY>\",
       <fields_dict>,
       question_id=\"<Q-NNNN if question-scoped>\",
   )
   print(fm)
   "' > /tmp/dir_fm.txt
   ```

3. Build the filename: `<DIR-NNNN>-<slugify(direction_title)>.md`
   ```bash
   wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
   import sys; sys.path.insert(0, \".\")
   from scripts.deep_synth_helper import slugify
   print(slugify(\"<DIRECTION_TITLE>\"))
   "'
   ```

4. Write to `$VAULT_ROOT/objective/direction/<DIR-NNNN>-<slug>.md`:
   ```
   <frontmatter>

   ## Reasoning pattern
   <reasoning_pattern field>

   ## Expected evidence
   <expected_evidence field>

   ## Seed queries
   <seed_queries field>

   ## Solves when
   <solves_when field>
   ```

## Step 8 - write objective/research_question_proposal/ nodes

For each proposal returned by `parse_proposals()`:

1. Get the next QP id:
   ```bash
   wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives next-id research_question_proposal --vault-root "$VAULT_ROOT"'
   ```

2. Format and write the proposal node:
   ```bash
   wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -c "
   import sys
   sys.path.insert(0, \".\")
   from scripts.deep_synth_helper import format_proposal_frontmatter
   fm = format_proposal_frontmatter(\"<QP-NNNN>\", \"<TODAY>\", <proposal_dict>)
   print(fm)
   "'
   ```

3. Filename: `<QP-NNNN>-<slugify(proposal_text[:50])>.md`

4. Write to `$VAULT_ROOT/objective/research_question_proposal/<QP-NNNN>-<slug>.md`:
   ```
   <frontmatter>

   ## Proposed question
   <proposal field>

   ## Rationale
   <rationale field>

   ## From gap
   <from_gap field>
   ```

## Step 9 - update objective/hot.md (locked write)

Append a summary of this synthesis run to `$VAULT_ROOT/objective/hot.md`:

```bash
# Acquire lock
bash "$CODE_PATH/scripts/wiki-lock.sh" acquire "objective/hot.md" || { sleep 2; bash "$CODE_PATH/scripts/wiki-lock.sh" acquire "objective/hot.md"; }

# Read current content, append summary block, write back
# Summary block:
# ### deep-synthesis run YYYY-MM-DD
# - Scope: <scope>
# - Research report: <output path>
# - Directions emitted: <DIR ids>
# - Proposals emitted: <QP ids>
# - Already answerable: <list or "none">

bash "$CODE_PATH/scripts/wiki-lock.sh" release "objective/hot.md"
```

On rc=75 (lock held after both tries): log a warning and skip the hot.md update. Write an
`objective/agent_todo/TODO-NNNN-update-hot.md` note instead.

## Step 10 - append to objective/index.md (locked write)

Apply the locking snippet from `skills/references/locking.md` on `objective/index.md`:

```bash
LOCK="$CODE_PATH/scripts/wiki-lock.sh"
if bash "$LOCK" acquire "objective/index.md" || { sleep 2; bash "$LOCK" acquire "objective/index.md"; }; then
  # Append operation log row to the ## Operation log section:
  # | deep-synthesis | <scope> | research | <TODAY> | report: <path>, dirs: <DIR ids>, props: <QP ids> |
  bash "$LOCK" release "objective/index.md"
else
  echo "index.md lock held -> skipping index append" >&2
fi
```

## Output summary (print to user)

After all writes:
```
deep-synthesis complete.
Report:          <output path>
Directions:      <DIR-NNNN list or "none">
Proposals:       <QP-NNNN list or "none">
Already answerable: <list or "none">
```

## RBAC write boundaries (do NOT cross)

This skill writes ONLY:
- `research/` (all sub-paths: deep/, Q-NNNN.md, T-NNNN.md, etc.)
- `objective/direction/`
- `objective/research_question_proposal/`
- `objective/hot.md` (locked)
- `objective/index.md` (locked, append only)

This skill MUST NOT write:
- `wiki/**` (read-only for this agent)
- `objective/purpose/` (user-only)
- `objective/topic/` (user-only)
- `objective/research_question/` (user-only; no R4 sanction in this skill)
- `objective/decision/` (user-only)
- `meta/health_report/`, `meta/cost_report/` (backend agent)
- `docs/**` (frozen)
- Code repo internals (`agents/`, `scripts/`, `config/`, `skills/`)

## Locking (shared-target writes)

The research/ output file is an OWNED file - no lock needed. Shared append targets
(`objective/hot.md`, `objective/index.md`) each require a Layer-2 lock. Apply the
canonical snippet from `skills/references/locking.md`: acquire in sorted-path order, write,
release; on rc=75 retry once after 2s, then skip and log a warning.

```bash
CODE_PATH="${CODE_PATH:-/home/jpietrak/second_brain}"
LOCK="$CODE_PATH/scripts/wiki-lock.sh"
PATHS=$(printf '%s\n' "objective/hot.md" "objective/index.md" | sort)
held=()
for p in $PATHS; do
  if bash "$LOCK" acquire "$p" || { sleep 2; bash "$LOCK" acquire "$p"; }; then
    held+=("$p")
  else
    echo "wiki-lock: $p held -> skipping deep-synthesis shared-target update" >&2
    break
  fi
done
# ... write shared targets while held ...
for p in "${held[@]}"; do bash "$LOCK" release "$p"; done
```

## Conventions

- Follow `skills/references/ai-first-rules.md` and `skills/references/write-rules.md`. ASCII
  only - no em-dashes, curly quotes, or Unicode math. Use ` - ` for dashes.
- Every claim in the report cites a `[[wiki/...]]` or `[[objective/...]]` wikilink. No bare
  training-data assertions on domain-specific facts.
- Anti-fabrication: if the vault cannot answer a question well, say so plainly in `## Gaps
  Remaining`. Never invent citations, dates, or measurements.
- No web access. No WebSearch / WebFetch. The scope is what is already in the vault.
- Cost: all synthesis runs on the research agent credit pool via Agent SDK ($0 marginal).
  No paid route is used by this skill.

## Step 11 - relink objective nodes

After all objective nodes have been written (steps 7 and 8), run relink to generate
`## Links` edges and normalize `written_by` on every objective node:

```bash
wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.objectives relink --apply'
```

This (re)generates each node's `## Links` edges and normalizes `written_by` (full
path-qualified wikilinks; no aliases).

## OSB adaptation notes (for maintainers)

- KEPT: `vault_scan()` pattern - now `gather_local_context()` in `scripts/research_synthesis.py`
  (keyword-scored; prefer `retrieve.py` when provisioned).
- KEPT: GAP_PROMPT / SYNTHESIS_PROMPT logic - now `RESEARCH_ANALYSIS_PROMPT` +
  `RESEARCH_SYNTHESIS_PROMPT` in `scripts/prompts/pipeline_prompts.py`.
- DROPPED: Perplexity calls, `free_sources()`, `load_baseline()` web fallbacks, X/Grok pulls.
- ADAPTED: Output path from OSB `Research/Deep/` -> `research/deep/` (our schema).
- ADAPTED: RBAC - OSB `research_deep` wrote to `wiki/`; this skill NEVER writes `wiki/`.
- ADAPTED: Shared helper `scripts/research_synthesis.py` used by BOTH this skill and `obj-synth`
  (R5+R7 resolution from plans/phase-2-objectives.md RESOLVED block).
