---
name: obj-query
description: >
  Query and reason over the objective graph. Reads the open frontier
  (unsolved research_questions + open directions) via agents.objectives,
  retrieves relevant wiki pages, and synthesizes a concise, cited answer
  to agenda questions such as "what open high-priority questions touch
  optical accelerators?", "which questions are answerable now?", or "what
  directions are open for topic T-0006?". Returns the answer INLINE in
  chat (no filing in Phase 2). Read-only over objective/, research/, and
  wiki/. No web access. Higher-effort reasoning (research agent credit pool).
  Triggers on: "obj-query", "/obj-query", "what open questions",
  "which questions are answerable", "what directions are open",
  "open frontier", "objective graph query", "query the objectives",
  "what research questions touch", "open high-priority questions",
  "which objectives relate to", "agenda query".
allowed-tools: Read Glob Grep Bash
---

**Ownership: `research` agent.** If you are NOT the `research` subagent (e.g. the main
orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with
`subagent_type: research`, pass the user's full request, let the research agent run the
steps below, and relay its result. Do NOT run the steps yourself - running as the `research`
agent is what activates the RBAC/write-scope boundary. If you ARE the `research` agent,
proceed.

# obj-query: query and reason over the objective graph

Owner: **research**. The objective graph records the research agenda: open questions,
active directions, decisions, and the vault PURPOSE. This skill reads that graph - augmented
by relevant wiki pages - and synthesizes a targeted answer to an agenda question. All
reasoning runs on the **research agent credit pool** (no extra metered cost). No web access.
ASCII only.

Phase-2 scope: the answer is returned INLINE in chat. Filing query history to
`research/query/` is an optional extension - see Step 6 below (disabled unless the user
explicitly requests filing).

## Step 0 - task start protocol (mandatory)

Every research task begins with two steps. Skip gracefully with a warning if the scaffold
is not yet applied.

### 0a - read active decisions
```bash
eval "$(wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m agents.vault_config env')"
# Or inside WSL:
python -m agents.vault_config env   # exports VAULT, VAULT_ROOT
python -m agents.objectives decisions --json
```
Parse the JSON list. Treat every `scope: all` or `scope: research` decision as a hard
constraint for the rest of this task. A decision that says "no web" means you do not
attempt any WebSearch / WebFetch (those tools are not available in Phase 2 anyway).

### 0b - read research memory
Read `.claude/memory/research/MEMORY.md` and any referenced fact files. Note current
phase, open synthesis threads, and any prior query patterns that applied here.

## Step 1 - resolve vault root
```bash
eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
```
All reads are filesystem reads (Read, Glob, Grep) under `$VAULT_ROOT`. No hard-coded paths.

## Step 2 - classify the query

Identify the query type from the user's request:

| Query type | Keywords / shape | Primary data source |
|------------|-----------------|---------------------|
| Open questions by topic | "open questions about T-NNNN", "what questions touch <topic>" | objective frontier - research_questions |
| High-priority open questions | "high priority open questions", "what should we research next" | objective frontier - research_questions priority:high |
| Answerable now | "answerable now", "which can we answer with current wiki" | frontier + wiki retrieval intersection |
| Open directions by question | "open directions for Q-NNNN", "what directions exist for <question>" | objective frontier - directions by serves_question |
| Open directions by topic | "what directions are open for topic T-NNNN" | frontier - directions by topics field |
| Full frontier dump | "show the open frontier", "what is open" | full frontier payload |
| Decision audit | "what constraints apply", "active decisions" | decisions only |
| Purpose query | "what is the vault purpose", "what is this vault about" | objective/purpose/PURPOSE.md |

For ambiguous queries, default to the full frontier dump and let the synthesis step apply
judgment.

## Step 3 - read the objective frontier

```bash
python -m agents.objectives frontier --json
```

Parse the JSON payload:
- `research_questions`: list of unsolved Q-NNNN nodes (sorted high > medium > low priority,
  then by created ascending).
- `directions`: list of open DIR-NNNN nodes (same sort order).
- `combined_ranked`: merged list in priority order.

For purpose and decision queries, run the appropriate verb instead:
```bash
python -m agents.objectives decisions --json   # for decision audit queries
```
For purpose: Read `$VAULT_ROOT/objective/purpose/PURPOSE.md` directly.

If `objective/` does not exist yet (scaffold not applied), say:
"The objective/ graph is not yet scaffolded. Run `python scripts/obj_init.py --apply` first."
and stop gracefully.

## Step 4 - retrieve relevant wiki pages (standard and deep modes only)

Skip this step for pure frontier-dump and decision-audit queries (no wiki needed).

For queries that require cross-referencing the wiki (e.g. "which questions are answerable
now?", "what wiki content covers T-0001?"):

### Preferred: retrieval pipeline (when provisioned)
```bash
if [ -f "$VAULT_ROOT/.vault-meta/bm25/index.json" ] && [ -d "$VAULT_ROOT/.vault-meta/chunks" ]; then
  python scripts/retrieve.py "<user query verbatim>" --top 5
fi
```
Output JSON: `candidates` array with `absolute_path`, `snippet`, `bm25_score`, `rerank_score`.
Read the top candidate pages and note which wiki concepts are covered.

### Graceful fallback (required)
If `retrieve.py` exits 10 (not provisioned), or any pipeline step errors, fall back:
1. Read `$VAULT_ROOT/wiki/hot.md` (recent focus context).
2. Read `$VAULT_ROOT/wiki/index.md` - scan descriptions for pages matching the query topic.
3. Read the 3-5 most relevant pages identified from the index.
Follow wikilinks to depth-1 for key entities; no deeper.

### Reading research outputs
For "answerable now" queries, also scan `$VAULT_ROOT/research/` for any existing answer
files (`research/Q-NNNN.md`) matching the open questions:
```bash
# Check which open questions already have a research answer file
ls "$VAULT_ROOT/research/"*.md 2>/dev/null | grep -E 'Q-[0-9]+'
```
An open research_question that already has a `research/Q-NNNN.md` file is a candidate for
`question-solve` - surface it to the user.

## Step 5 - synthesize and answer

Reason over the objective data + wiki evidence. Apply vault PURPOSE as a relevance filter:
```bash
python -m agents.vault_config purpose   # returns the vault PURPOSE string
```
Weight answers toward the vault's primary mission. Surface only what is relevant to the
vault scope.

### Answer format

Return the answer inline in chat. Structure it as:

```
## Objective Query: <brief restatement of the question>

**Frontier snapshot** (as of YYYY-MM-DD):
- Open research questions: N total (N_high high, N_med medium, N_low low priority)
- Open directions: N total

<Main answer body - varies by query type; see templates below>

**Confidence:** <high|medium|low> - based on <what data you read>
**Gaps:** <anything the objective graph cannot answer about this query>
**Suggested next actions:** <e.g. "Run obj-synth to generate directions for Q-0002",
  "Consider question-solve Q-0001 - research/Q-0001.md exists",
  "Run deep-synthesis --question Q-0003 to answer this question">
```

### Answer templates by query type

**Open questions by topic (T-NNNN filter):**
List each matching Q-NNNN with: priority, created date, `solved: no/yes`, and the question
text (from the file body). Cite with `[[objective/research_question/Q-NNNN-<slug>]]`.

**Answerable now:**
For each open Q-NNNN: check if wiki pages retrieved cover its subject matter with enough
depth to warrant an answer. Classify as:
- ANSWERABLE: wiki has N relevant pages; `research/Q-NNNN.md` exists (or could be written
  now by `deep-synthesis --question Q-NNNN`).
- PARTIALLY: wiki has some coverage but gaps remain.
- NOT YET: wiki has no substantive coverage; needs new ingest first.

**Open directions for Q-NNNN or T-NNNN:**
List each matching DIR-NNNN with: priority, status, `serves_question`, `targets_gap` field.
Cite with `[[objective/direction/DIR-NNNN-<slug>]]`. If no matching directions exist, say so
and suggest running `obj-synth` to generate directions.

**Full frontier dump:**
Emit the ranked combined_frontier list grouped by type. Include id, priority, solved/status,
and the first line of the body.

**Decision audit:**
List each active D-NNNN with scope and the body (truncated to 120 chars). Flag any decision
that the current query might be testing.

### Citation rules
- Cite objective nodes with their vault-relative path: `[[objective/research_question/Q-0001-slug]]`.
- Cite wiki pages with wikilink notation: `[[wiki/concepts/Foo]]`.
- Cite research outputs: `[[research/Q-0001]]`.
- NEVER fabricate objective node ids, question text, or wiki content. If you cannot find a
  node by its id, say so explicitly.
- Anti-fabrication: read the actual files before claiming what a node says. Do not rely on
  memory for objective node body content.

## Step 6 - optional filing (user-requested only)

In Phase 2, query history is NOT automatically filed. If the user explicitly asks to save
the query result ("save this query", "file this answer", "log to research/query/"), then:

1. Acquire a lock on the target file:
   ```bash
   LOCK="scripts/wiki-lock.sh"
   TARGET="research/query/$(date +%Y-%m-%d)-<slug>.md"
   bash "$LOCK" acquire "$TARGET" || { sleep 2; bash "$LOCK" acquire "$TARGET"; }
   ```
2. Write to `$VAULT_ROOT/research/query/YYYY-MM-DD-<slug>.md` with frontmatter:
   ```yaml
   ---
   type: query_record
   generated_by: research
   query: "<verbatim user question>"
   created: YYYY-MM-DD
   ---
   ```
   Body: the inline answer from Step 5, with all wikilinks preserved.
3. Release the lock:
   ```bash
   bash "$LOCK" release "$TARGET"
   ```
4. Report: "Filed to research/query/YYYY-MM-DD-<slug>.md".

RBAC: `research/query/` is research-writable. No RBAC exception needed.

## Boundaries

- Read-only over `objective/`, `wiki/`, `research/`. No writes except the optional filing
  in Step 6.
- Does NOT write to `wiki/`, `objective/purpose/`, `objective/topic/`,
  `objective/research_question/`, or `objective/decision/`.
- Does NOT call `python -m agents.objectives next_id` (no node creation).
- Does NOT run the synthesis pipeline (`RESEARCH_ANALYSIS_PROMPT`, `RESEARCH_SYNTHESIS_PROMPT`).
  Use `obj-synth` or `deep-synthesis` for that.
- No web access (no WebSearch / WebFetch tools available).
- ASCII only - no em-dashes, curly quotes, or Unicode math.

## Token discipline

frontier JSON (small) -> stop if it fully answers. wiki retrieval (top-5 pages ~300 each)
-> stop once answer is clear. Full wiki enumeration only for deep "answerable now" queries.

## Trigger phrases

- `/obj-query` or `obj-query:`
- "what open questions" / "what open high-priority questions"
- "which questions are answerable now" / "which can we answer"
- "what directions are open for" / "open directions"
- "open frontier" / "show the frontier"
- "objective graph query" / "query the objectives"
- "what research questions touch" / "which objectives relate to"
- "what should we research next" / "what is open for topic"
- "active decisions" (when asking about objective graph constraints)
