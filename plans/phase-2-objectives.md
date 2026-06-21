# Phase 2 - Objective graph + Research agent + Research skills

Backend agent draft, 2026-06-21. Companion to `elegant-juggling-sparrow.md` (authoritative) and
`plans/objective-flow.md` (flow design). Authoritative docs: `docs/{requirements,agents,vault-schema,
skills-description}.md` (frozen, read-only). Honors all LOCKED decisions from the Phase 2 grill-me:
markdown-native objective store, local-only research agent, decision-nodes-at-task-start, RBAC rewrite
of OSB research paths, model routing per guideline #9, parallel waves of disjoint-file work units. ASCII
only throughout.

> **RESOLVED (grill-me 2026-06-21) - cleared to build (pending final user go). Supersedes any
> conflicting inline note.**
> - **R1:** moot - the FU2 dispatch layer is DONE and user-confirmed working; RBAC is enforced.
> - **R2:** separate `scripts/obj_init.py` (parallels `wiki_init.py`).
> - **R3:** `wiki-gaps` writes Gaps to a wiki-owned **`wiki/gaps.md`** (research + the P3 web agent
>   read it). No RBAC exception needed.
> - **R4:** **user-proxy exception** - the RBAC guard sanctions the user-invoked `question-promote`
>   and `question-solve` skills to write `objective/research_question/` (the user triggers+approves
>   them = their write, proxied); the guard still blocks ALL other research writes to
>   `research_question/`.
> - **R5 + R7:** one shared `scripts/research_synthesis.py` helper (vault-scan + prompt-fill) used by
>   BOTH `obj-synth` and `deep-synthesis` (no Wave-2 duplication; deterministic part unit-tested).
> - **R6:** research agent does a FRESH `objectives frontier` scan each task (no cached catalog).
> - **R8:** **reconcile the legacy `research/` now** (afd-simulator/, daily/, notebooklm/, youtube/ ->
>   v0.2 `research/` layout: deep/, query/, `<topic>`, `<question_id>`). Because this touches
>   hand-authored content (esp. `afd-simulator/`), do it via **dry-run-on-a-copy -> show the user the
>   diff -> apply on approval** (same pattern as wiki-init), NOT a blind migration.
> - Seeding the User-Only `objective/{purpose,topic,research_question}` nodes is a **user-involved
>   step**: backend scaffolds + proposes drafts; the user authors/approves.

---

## 1. `objective/` scaffold - typed node layout

Phase 2 scaffolds the `objective/` folder (currently absent from the live vault) with one subfolder per
node type and a `_template.md` per folder. This is a CODE-REPO + VAULT-INIT action: the scaffold logic
lives in `scripts/obj_init.py` (mirrors `scripts/wiki_init.py`) and is invoked once on the live vault.

### 1.1 Folder tree

```
objective/
  purpose/          USER-ONLY: one file per vault, fixed for vault lifespan
  topic/            USER-ONLY: confirmed research directions / sub-problems
  research_question/  USER-ONLY: well-formed questions; solved: yes|no
  decision/         USER-ONLY: agent behavioral constraints (do / don't)
  research_question_proposal/  RESEARCH-AGENT: proposed questions, awaiting user approval
  direction/        RESEARCH-AGENT: crawl/reasoning trajectories, not yet promoted to topic
  agent_todo/       RESEARCH-AGENT: self-correcting instructions, reflections
  hot               RESEARCH-AGENT: single file, last reasoning context (NOT a subfolder)
  index             RESEARCH-AGENT: single file, catalog one row per agent op (NOT a subfolder)
```

`hot` and `index` are FILES at `objective/hot.md` and `objective/index.md`, not subdirectories -
parallel to `wiki/hot.md` and `wiki/index.md`.

### 1.2 Node id scheme

| Node type                    | Id pattern         | Example                        |
|------------------------------|--------------------|-------------------------------|
| purpose                      | `purpose` (single) | `objective/purpose/PURPOSE.md` |
| topic                        | `T-NNNN` (4 digits)| `T-0001-inference-disagg.md`  |
| research_question            | `Q-NNNN`           | `Q-0001-what-is-latency-floor.md` |
| decision                     | `D-NNNN`           | `D-0001-no-web-writes.md`     |
| research_question_proposal   | `QP-NNNN`          | `QP-0001-memory-bandwidth-wall.md` |
| direction                    | `DIR-NNNN`         | `DIR-0001-measure-hbm-vs-sram.md` |
| agent_todo                   | `TODO-NNNN`        | `TODO-0001-recheck-iris-tetra.md` |

NNNN is monotonically assigned by `agents/objectives.py` at creation time (same approach as
`ingest_index.py` uses stable source ids). The file stem encodes id + short slug, e.g.
`Q-0001-what-is-latency-floor.md`. The numeric part is zero-padded to 4 digits; current maximum
is stored in `objective/index.md` frontmatter (`next_id: {type: N}`) so the scan can assign
without collisions.

### 1.3 Frontmatter per node type

All nodes carry `type`, `id`, `created`, `updated`. User-Only nodes also carry `status`; Research-Agent
nodes carry `generated_by`. Fields below are in addition to those shared fields.

**purpose** (user-only):
```yaml
type: purpose
id: purpose
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: active
vault: <vault-name>
```
Body: free-text statement of the vault mission.

**topic** (user-only):
```yaml
type: topic
id: T-NNNN
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: active  # active | paused | completed
related_questions: [Q-NNNN, ...]
```

**research_question** (user-only):
```yaml
type: research_question
id: Q-NNNN
created: YYYY-MM-DD
updated: YYYY-MM-DD
solved: "no"   # "yes" | "no" - user sets only; agents NEVER flip autonomously
topic: T-NNNN
priority: high  # high | medium | low
answer_ref: ""  # filled by question-solve: research/<question_id>.md
```

**decision** (user-only):
```yaml
type: decision
id: D-NNNN
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: active  # active | superseded | archived
scope: all      # all | wiki | research | web - which agents it constrains
```
Body: instruction in plain imperative prose. Example: "Do not write to wiki/ from the research agent."

**research_question_proposal** (research-agent-written):
```yaml
type: research_question_proposal
id: QP-NNNN
created: YYYY-MM-DD
updated: YYYY-MM-DD
generated_by: research
from_gap: <gap ref or direction id>
status: pending  # pending | approved | rejected
```
Body: the proposed research question + rationale. On user approval, `question-promote` creates a
`research_question` node and archives this file (sets `status: approved`).

**direction** (research-agent-written):
```yaml
type: direction
id: DIR-NNNN
created: YYYY-MM-DD
updated: YYYY-MM-DD
generated_by: research
serves_question: Q-NNNN
topics: [T-NNNN]
targets_gap: <gap title>
priority: high  # high | medium | low
status: open  # open | crawled | superseded
```
Body: `reasoning_pattern`, `expected_evidence`, `seed_queries`, `solves_when` - the machine-parseable
fields from `RESEARCH_SYNTHESIS_PROMPT` (verbatim field names, preserved for `parse_directions()`).

**agent_todo** (research-agent-written):
```yaml
type: agent_todo
id: TODO-NNNN
created: YYYY-MM-DD
updated: YYYY-MM-DD
generated_by: research
status: open  # open | done | superseded
```

**objective/index.md** (research-agent-written, shared append target):
```yaml
type: index
updated: YYYY-MM-DD
next_id:
  topic: 1
  research_question: 1
  decision: 1
  research_question_proposal: 1
  direction: 1
  agent_todo: 1
ai-first: true
```
Body: one-row-per-agent-op table (Operation | Node | Agent | Date | Notes). Locked on write via
`wiki-lock.sh` (same pattern as `wiki/index.md`).

**objective/hot.md** (research-agent-written, shared):
```yaml
type: hot
updated: YYYY-MM-DD
generated_by: research
```
Body: last objective-reasoning context: current focus, open threads, proposed next actions. Locked on
write.

### 1.4 RBAC user-vs-agent split (from docs/vault-schema.md)

| Node type                    | Write           | Edit            | Read            |
|------------------------------|-----------------|-----------------|-----------------|
| purpose / topic / research_question / decision | User Only | NO | Research Agent |
| research_question_proposal / direction / agent_todo / hot / index | Research Agent | YES | Research Agent |

`scripts/rbac_guard.py` is extended in Phase 2 to add the `research` role entry: research may Write to
`objective/research_question_proposal/`, `objective/direction/`, `objective/agent_todo/`, `objective/hot`,
`objective/index`, `research/`, `meta/nightly_report/`. Research may NOT Write to `objective/purpose/`,
`objective/topic/`, `objective/research_question/`, `objective/decision/`, or any `wiki/` path.

### 1.5 `_template.md` per node type (guideline #5)

Each subfolder carries a `_template.md` with the frontmatter skeleton + section headings. The
`scripts/obj_init.py` creates these during scaffold. The templates are vault-content files and thus NOT
written by the backend agent during the build - the scaffold script generates them from strings in
`obj_init.py` at vault-init time (same pattern as `wiki-init`).

---

## 2. `agents/objectives.py` - markdown-native objective store

**Owner:** backend (code). **Model:** Sonnet (new feature, no reference analog).

### 2.1 Design

Mirrors `scripts/wiki_index.py` idioms and `agents/ingest_index.py` lifecycle management:
- `scan_objectives(vault_root)` - walk `objective/*/` directories, parse frontmatter from every `.md`
  file that is NOT a `_template`, build an in-memory catalog (one dict per node).
- `build_index(vault_root, dry_run)` - render `objective/index.md` (locked write, same
  `wiki-lock.sh` pattern as `wiki_index.py`).
- `open_frontier(vault_root)` - return the open-frontier payload: all `research_question` nodes with
  `solved: no` + all `direction` nodes with `status: open`, ranked by priority (high > medium > low;
  tiebreak: `Q-` before `DIR-`, then by created date ascending so older items surface first). This
  is the structured payload that `obj-synth` and `deep-synthesis` consume.
- `next_id(vault_root, node_type)` - read the `next_id` counter from `objective/index.md` frontmatter,
  increment, write back (locked). Returns the formatted id string (`Q-NNNN`, `DIR-NNNN`, etc.).
- `read_decisions(vault_root)` - load all `objective/decision/*.md` files, return list of decision
  dicts sorted by `scope` (all first). Called at task start by the research agent.

**CLI verbs** (run as `python -m agents.objectives`):
```
scan          - reconcile objective/ nodes to objective/index.md
frontier      - emit open-frontier payload as JSON
decisions     - list active decision nodes (scope + body)
status        - counts per node type + per solved/unsolved for research_question
```
`--vault <name>` passthrough, `--json` flag, `--dry-run` flag.

**Vault-root resolution:** identical to `wiki_index.py` (env `VAULT_ROOT`/`VAULT_PATH` > subprocess
`agents.vault_config path`). Never hard-coded.

**Locking:** `objective/index.md` and `objective/hot.md` are shared append targets; acquire
`wiki-lock.sh` before writing them (same as `wiki/index.md`).

### 2.2 Hermetic test

`tests/test_objectives.py`:
- Creates a temp vault with a synthetic `objective/` tree (2 research_questions, 1 solved; 2 directions
  open + 1 crawled; 1 decision; 1 proposal pending).
- `scan` builds `objective/index.md` - asserts row count and header.
- `frontier` returns only open research_questions and open directions, ranked by priority.
- `read_decisions` returns only `status: active` decisions.
- `next_id` increments correctly and is safe when called twice (idempotent counter write).
- `decisions` CLI verb prints to stdout without error.

---

## 3. `scripts/obj_init.py` - scaffold + reconcile (mirrors `wiki_init.py`)

**Owner:** backend (code). **Model:** Sonnet.

Creates the `objective/` folder structure and templates on a new vault; is idempotent on an existing
vault (only creates what is missing, never overwrites existing nodes). Called by the `wiki-init` skill
after the wiki scaffold step, OR independently via `python scripts/obj_init.py --apply`.

Actions:
1. Create `objective/{purpose,topic,research_question,decision,research_question_proposal,direction,
   agent_todo}/` folders if absent.
2. Write `_template.md` into each subfolder if absent (from hardcoded strings in the script).
3. Create `objective/index.md` with seed frontmatter (`next_id` all = 1) if absent.
4. Create `objective/hot.md` with seed frontmatter if absent.
5. Print a dry-run diff if `--dry-run` (no writes); apply if `--apply`.

**Hermetic test:** `tests/test_obj_init.py` - temp dir, run `--apply`, assert all folders + templates
created; run again (idempotent check): no files overwritten, no errors.

---

## 4. Research agent definition - `.claude/agents/research.md`

**Owner:** backend (code). **Model:** Sonnet (new agent definition).

### 4.1 Agent definition file structure

Follows the same header format as `.claude/agents/wiki.md`:
```yaml
---
name: research
description: >
  Research Agent for Second Brain v0.2. Operates the objective graph (objective/ area),
  synthesizes research reports (research/), proposes new research questions. LOCAL-ONLY in
  Phase 2 (no WebSearch/WebFetch). Use for: obj-query, obj-synth, obj-reconcile, deep-synthesis,
  question-solve, question-promote. Does NOT edit wiki/ or user-only objective nodes.
tools: Read, Edit, Write, Grep, Glob, Bash
---
```

**FU2 dispatch header:** same pattern as wiki.md (sets `SB_AGENT_ROLE=research` on startup so the
RBAC guard can enforce write boundaries).

### 4.2 Role

1. Maintain the `objective/` graph: `research_question_proposal`, `direction`, `agent_todo`, `hot`,
   `index` (research-writable per RBAC).
2. Synthesize `research/` outputs: deep reports, topic state-of-knowledge, per-question answers.
3. Read `wiki/` + `objective/` for reasoning; write ONLY `objective/` (agent area) + `research/` +
   `meta/nightly_report/`. NEVER write `wiki/`.
4. Read `objective/decision/*` at every task start and honor them as hard constraints.
5. Propose `research_question_proposal` nodes when synthesis reveals a gap no current question covers.
6. On user command, run `question-promote` and `question-solve`.
7. Seed `memory/research/MEMORY.md` at first run.

### 4.3 Decision-node-read-at-start mechanism

Every research agent task begins with:
```bash
python -m agents.objectives decisions --json
```
The output is a structured list of active `objective/decision/*.md` nodes. The agent parses them and
applies them as hard constraints for the rest of the task. This is explicit in the agent definition's
`## Task start protocol` section.

### 4.4 Memory seed

`.claude/memory/research/MEMORY.md` created at scaffold time with:
- Project context pointer (vault name, branch, plan file path).
- Phase 2 what-was-built block (populated after Phase 2 completes).
- Slots for: research-question-catalog, direction-registry, key synthesis decisions.

---

## 5. Phase 2 skills

Each skill is a `SKILL.md` under `skills/<name>/SKILL.md`. Owner, source, model, test listed per unit.
The skills are grouped by the parallel wave they ship in (see Section 7).

### 5.1 `obj-query` (Wave 2)

**Owner:** research. **Source:** new. **Model:** Sonnet.
Reads `objective/` (via `agents.objectives frontier`) + retrieves relevant `wiki/` pages (via
`scripts/retrieve.py`), and synthesizes a concise objective-oriented answer. Does NOT write to wiki/.
Optionally files the query to `research/query/YYYY-MM-DD-<slug>.md`.

SKILL.md sections: Task / Tools / Steps / Output format / Locking (take lock on `research/query/`
target before writing; take lock on `objective/hot.md` when updating context).

**Test:** mock vault with 2 open research_questions + 3 wiki pages; run skill procedure; assert
`research/query/` file created with correct frontmatter + [[wikilink]] citations.

### 5.2 `obj-synth` (Wave 2 - depends on obj-query patterns)

**Owner:** research. **Source:** new. **Model:** Sonnet (uses `RESEARCH_ANALYSIS_PROMPT` +
`RESEARCH_SYNTHESIS_PROMPT` from `scripts/prompts/pipeline_prompts.py`).

Two-step procedure:
1. **Analysis step** - fill `RESEARCH_ANALYSIS_PROMPT` with: purpose (read from
   `objective/purpose/PURPOSE.md`), open research_questions (from `objectives frontier`), active topics
   (from `objective/topic/`), wiki baseline (from `retrieve.py`), wiki-gaps (from `wiki-gaps` output if
   available or "none").
2. **Synthesis step** - feed the analysis into `RESEARCH_SYNTHESIS_PROMPT`; parse output with
   `parse_directions()` + `parse_proposals()` from `pipeline_prompts.py`.
3. **Write outputs:**
   - Each parsed direction -> one `objective/direction/DIR-NNNN-<slug>.md` (via `objectives.next_id`).
   - Each parsed proposal -> one `objective/research_question_proposal/QP-NNNN-<slug>.md`.
   - Update `objective/hot.md` with summary of this synthesis run.
   - Append one row to `objective/index.md` (locked write).

RBAC: research writes to `objective/direction/`, `objective/research_question_proposal/`,
`objective/hot.md`, `objective/index.md` only. Never writes wiki/ or user-only objective nodes.

**Test:** fixture with purpose + 2 open questions + wiki pages; run skill procedure; assert direction +
proposal files created with correct frontmatter; assert `parse_directions()` returns non-empty list.

### 5.3 `obj-reconcile` (Wave 3 - after obj-synth)

**Owner:** research. **Source:** new. **Model:** Sonnet.

Reviews existing `objective/direction/` nodes for staleness or supersession by newer directions.
Compares against current `objective/research_question/` (solved status). Marks superseded directions
`status: superseded`. Updates `objective/hot.md`. Logs to `objective/index.md`.

Purpose: prevents the direction set from growing unboundedly; equivalent to `wiki-reconcile` for the
objective graph.

**Test:** fixture with 3 directions (1 serving a now-solved Q, 1 duplicate, 1 valid); run skill;
assert the Q-solved one gets `status: superseded` and the duplicate gets `status: superseded`.

### 5.4 `deep-synthesis` (Wave 2)

**Owner:** research. **Source:** ADAPT from OSB `research_deep.py` (local vault-scan + GAP_PROMPT +
SYNTHESIS_PROMPT kept; web-pull + Perplexity dropped). **Model:** Sonnet (substantial adaptation).

This is the main local deep-reasoning skill. It combines the two pipeline prompts
(`RESEARCH_ANALYSIS_PROMPT` + `RESEARCH_SYNTHESIS_PROMPT`) with a vault-first scan
(mirroring OSB's `vault_scan()`) to produce a `research/` report or topic synthesis.

Procedure:
1. Read `objective/decision/*` (hard constraints).
2. Resolve topic/question scope from argument (`--topic <T-NNNN>` or `--question <Q-NNNN>`).
3. Scan `wiki/` using `retrieve.py` for pages relevant to the scope.
4. Fill and run `RESEARCH_ANALYSIS_PROMPT` -> per-question gap assessment.
5. Fill and run `RESEARCH_SYNTHESIS_PROMPT` -> directions + proposals.
6. Write output to one of:
   - `research/deep/YYYY-MM-DD-<topic>.md` (topic-scoped deep report).
   - `research/<question_id>.md` (question-answer report, when `--question` is given).
   - `research/<topic_slug>.md` (ongoing topic state-of-knowledge synthesis).
7. Parse and write `objective/direction/` nodes + `objective/research_question_proposal/` nodes from
   synthesis output (same as `obj-synth` step 3).
8. Update `objective/hot.md` + append to `objective/index.md`.

RBAC: writes only to `research/`, `objective/direction/`, `objective/research_question_proposal/`,
`objective/hot.md`, `objective/index.md`. Never writes wiki/.

**OSB adaptation notes:**
- KEEP: `vault_scan()` pattern (keyword-scored scan over wiki pages; adapt to use `retrieve.py`
  instead of the raw keyword scan for better relevance).
- KEEP: GAP_PROMPT / SYNTHESIS_PROMPT logic (replaced by `pipeline_prompts.py` equivalents already
  committed).
- DROP: Perplexity calls, web-pull steps, `free_sources()`, `load_baseline()` web fallbacks.
- ADAPT: output path from `Research/Deep/` -> `research/deep/` (our schema).
- ADAPT: RBAC - OSB `research_deep` wrote to `wiki/`; our version NEVER writes wiki/.

**Test:** fixture with purpose + 1 open Q-0001 + 3 wiki pages; run `--question Q-0001`; assert
`research/Q-0001.md` created; assert direction + proposal files created; assert no wiki/ writes.

### 5.5 `wiki-gaps` (Wave 2 - wiki-owned)

**Owner:** wiki. **Source:** new (uses `WIKI_GAP_PROMPT` from `scripts/prompts/pipeline_prompts.py`).
**Model:** Sonnet.

Bottom-up gap analysis over `wiki/`. Procedure:
1. Read `objective/purpose/PURPOSE.md` + `objective/topic/` (READ only - RBAC allows research-area
   reads from wiki agent too).
2. Retrieve wiki pages relevant to active topics via `scripts/retrieve.py`.
3. Fill `WIKI_GAP_PROMPT` with purpose + topics + wiki state (include `## For future Claude` and
   `## Open Questions` sections verbatim per the prompt spec).
4. Run prompt; parse output into a structured Gaps object.
5. Write to `research/query/gaps-YYYY-MM-DD.md` (gaps are query-adjacent outputs; wiki agent has read
   rights on `research/query/` per the schema but only research writes there - see risk R3).
   ALTERNATIVE: write to `meta/nightly_report/gaps-YYYY-MM-DD.md` since that is where proposed
   research inputs land.

NOTE: `wiki-gaps` write destination needs user confirmation (see Section 8, risk R3).

**Test:** fixture with 2 wiki pages containing `## Open Questions`; run skill; assert gap output
contains items from those sections ranked first; assert READY terminator present.

### 5.6 `question-promote` (Wave 3)

**Owner:** research. **Source:** new. **Model:** Haiku (simple state transition, near-verbatim from
the spec).

Procedure:
1. Accept `--proposal QP-NNNN` argument.
2. Read `objective/research_question_proposal/QP-NNNN-*.md`.
3. Assign next Q-NNNN id via `objectives.next_id`.
4. Create `objective/research_question/Q-NNNN-<slug>.md` with frontmatter: `solved: no`, `topic` from
   proposal's `from_gap`, `priority: medium` (default; user adjusts).
5. Update the proposal file: set `status: approved`, add `promoted_to: Q-NNNN`.
6. Append to `objective/index.md` (locked).
7. Print confirmation: "Promoted QP-NNNN -> Q-NNNN: <question text>".

NOTE: Creates a USER-ONLY node type (`research_question`). This is the ONLY point where the research
agent legitimately writes to `objective/research_question/`. The RBAC guard exception for this verb
must be explicit in `rbac_guard.py` (a special `question-promote` permission, or a narrow path
exception). See risk R5.

**Test:** fixture with proposal file; run question-promote; assert Q-0001 file created with `solved:
no`; assert proposal file updated to `status: approved`.

### 5.7 `question-solve` (Wave 3)

**Owner:** research. **Source:** new. **Model:** Haiku (simple state transition).

Procedure:
1. Accept `--question Q-NNNN` argument.
2. Read `objective/research_question/Q-NNNN-*.md` - assert `solved: no` (fail if already yes).
3. Expect `research/<question_id>.md` to exist (the answer file; created by `deep-synthesis`
   `--question Q-NNNN`). If not, warn and prompt user.
4. Update the research_question file: set `solved: yes`, `answer_ref: research/Q-NNNN.md`,
   `updated: YYYY-MM-DD`.
5. Update `objective/hot.md` (append solved note).
6. Append to `objective/index.md` (locked).
7. Update research agent memory: append `Q-NNNN SOLVED on YYYY-MM-DD -> research/Q-NNNN.md`.

NOTE: `question-solve` writes to a USER-ONLY node type. Same RBAC exception needed as
`question-promote`. See risk R5.

**Test:** fixture with Q-0001 (solved: no) + `research/Q-0001.md`; run question-solve; assert Q-0001
frontmatter has `solved: yes` + `answer_ref` set.

### 5.8 Answer-pass (part of `obj-reconcile` or `obj-synth`)

After `deep-synthesis` or `obj-synth`, the `RESEARCH_ANALYSIS_PROMPT` already emits an
"## Already Answerable" section. The research agent reads this and surfaces the list to the user
(printed to console + appended to `objective/hot.md`) as: "The vault can already answer these
questions - consider running `question-solve Q-NNNN`."

This is NOT an autonomous solve action. It is a recommendation pass that the user acts on. No separate
skill needed; it is built into the `obj-synth` and `deep-synthesis` SKILL.md procedures.

---

## 6. `research/` layout + templates

The `research/` folder already has partial content in the live vault (afd-simulator/, daily/,
notebooklm/, youtube/ - all pre-v0.2 legacy). Phase 2 adds the schema-specified subfolders +
templates without touching legacy content.

### 6.1 Folder layout (schema-specified)

```
research/
  deep/               deep research reports (one file per topic+date)
  query/              saved Q&A history + gaps output
  <topic_slug>.md     ongoing topic state-of-knowledge synthesis (flat file at research/)
  <question_id>.md    final answer to a stated research_question (Q-NNNN.md)
  _template_deep.md   template for deep/ reports
  _template_query.md  template for query/ history entries
```

Legacy subdirs (afd-simulator/, daily/, notebooklm/, youtube/) are left in place by `obj_init.py`
(additive-only). A future reconcile step (optional) can move them under the schema layout.

### 6.2 Templates

**`research/deep/_template.md`:**
```yaml
type: research_report
generated_by: research
topic: T-NNNN
serves_question: Q-NNNN
created: YYYY-MM-DD
status: draft  # draft | final
```
Body sections: `## Summary`, `## Key Findings` (each cite [[wiki/...]] or raw), `## Gaps Remaining`,
`## Directions Emitted`, `## Already Answerable Recommendations`.

**`research/query/_template.md`:**
```yaml
type: query_record
generated_by: research
query: ""
created: YYYY-MM-DD
```
Body: the question, the synthesized answer with [[wikilinks]], open items.

**`research/<question_id>.md` (no separate template - generated by `deep-synthesis --question`):**
Frontmatter: `type: question_answer`, `id: Q-NNNN`, `solved_on: YYYY-MM-DD`. Body: answer with full
[[wikilink]] citations.

---

## 7. Seeding `objective/` from the live Inference-Disagg vault

The `objective/purpose/`, `objective/topic/`, and `objective/research_question/` nodes are USER-ONLY.
The backend SCAFFOLDS the folder + template structure and PROPOSES initial draft content for the user
to review and author-approve. This is a user-involved step.

### 7.1 Seeding process

1. **Scaffold** (automated, safe): `python scripts/obj_init.py --apply` creates all folders +
   templates + empty `objective/index.md` + `objective/hot.md`. No knowledge content.
2. **Purpose draft** (backend proposes, user authors): Extract the vault PURPOSE from
   `config/vaults/Inference-Disagg/vault.yaml`. Generate a draft `objective/purpose/PURPOSE.md` in the
   code repo's scratchpad for user review. The user copies + edits it into the vault directly (they own
   the `purpose/` folder). This is explicitly flagged as a USER ACTION in the demo script.
3. **Topic drafts** (backend proposes): Scan `wiki/hot.md` TBDs + `wiki/index.md` type splits +
   wiki page themes. Generate 3-5 candidate `T-NNNN` topic drafts in the scratchpad. User reviews,
   edits, and places them into `objective/topic/`.
4. **Research question drafts** (backend proposes): From `wiki/hot.md` open threads + `wiki-gaps`
   output, propose 3-5 candidate `Q-NNNN` research questions. User reviews and places approved ones.
5. **Decision nodes** (user creates directly): One seed example provided as a template in the
   instructions. Example: "Do not fetch web content from the research agent (no WebSearch/WebFetch)."

### 7.2 Why the seeding is user-involved

The plan is explicit: User-Only nodes define the research AGENDA. Agents must not autonomously set
their own agenda. Backend scaffolds and SUGGESTS; the user approves or authors. This is a hard RBAC
line, not a convenience.

---

## 8. Dependency graph + parallel waves

### Wave 1 - Foundational plumbing (no vault knowledge writes)

Disjoint files. All Sonnet.

| Unit | Files | Model |
|------|-------|-------|
| W1a: `scripts/obj_init.py` + `tests/test_obj_init.py` | scripts/obj_init.py, tests/test_obj_init.py | Sonnet |
| W1b: `agents/objectives.py` + `tests/test_objectives.py` | agents/objectives.py, tests/test_objectives.py | Sonnet |
| W1c: `scripts/rbac_guard.py` extend (add `research` role) + extend test | scripts/rbac_guard.py, tests/test_rbac_guard.py | Haiku (targeted extension, near-verbatim pattern from wiki role) |
| W1d: `.claude/agents/research.md` + `memory/research/MEMORY.md` seed | .claude/agents/research.md, .claude/memory/research/MEMORY.md | Sonnet |

Wave 1 gate: `tests/test_obj_init.py` + `tests/test_objectives.py` + `tests/test_rbac_guard.py` all
pass.

### Wave 2 - Core skills (depend on Wave 1 plumbing)

Disjoint skill files. Each skill owns only its `SKILL.md`.

| Unit | Files | Model |
|------|-------|-------|
| W2a: `skills/deep-synthesis/SKILL.md` | skills/deep-synthesis/SKILL.md | Sonnet |
| W2b: `skills/obj-synth/SKILL.md` | skills/obj-synth/SKILL.md | Sonnet |
| W2c: `skills/obj-query/SKILL.md` | skills/obj-query/SKILL.md | Sonnet |
| W2d: `skills/wiki-gaps/SKILL.md` | skills/wiki-gaps/SKILL.md | Sonnet |

Wave 2 gate: each SKILL.md is internally consistent with the objective/ frontmatter spec and the
pipeline prompt field names.

### Wave 3 - State-transition skills + reconcile (depend on Wave 1 + Wave 2)

| Unit | Files | Model |
|------|-------|-------|
| W3a: `skills/question-promote/SKILL.md` | skills/question-promote/SKILL.md | Haiku |
| W3b: `skills/question-solve/SKILL.md` | skills/question-solve/SKILL.md | Haiku |
| W3c: `skills/obj-reconcile/SKILL.md` | skills/obj-reconcile/SKILL.md | Sonnet |

Wave 3 gate: all three SKILL.md files consistent with node id scheme and frontmatter fields.

### Wave 4 - Integration + vault scaffold apply (depends on all prior waves)

| Unit | Files | Model |
|------|-------|-------|
| W4a: Apply `obj_init.py --apply` to live Inference-Disagg vault | vault files only | N/A (script run) |
| W4b: User seed step - propose purpose/topic/question drafts | user review + vault writes | User action |
| W4c: Integration test + live demo | tests/ + demo script | Sonnet orchestrator |

Orchestrator (Opus) integrates all wave outputs, runs the full test suite, commits once per wave at a
clean boundary.

---

## 9. Acceptance: functional tests + live demo

### 9.1 Functional tests (hermetic)

All tests run via `wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && .venv/bin/python -m pytest
tests/test_obj_init.py tests/test_objectives.py tests/test_rbac_guard.py -v'`. No network, no vault
writes, `mktemp` sandbox.

Target: all green before the live vault scaffold step.

### 9.2 Live demo over Inference-Disagg vault

The demo exercises the full Phase 2 flow in order:

1. **Scaffold:** `python scripts/obj_init.py --apply` -> `objective/` folder tree created + templates
   + index + hot. Print folder list, confirm.
2. **User seeds** 1-2 topic nodes (T-0001) + 1-2 research question nodes (Q-0001, Q-0002) directly in
   the vault (user-authored; demo uses the drafted content from the seed step).
3. **`obj-synth`:** Research agent reads objective frontier (Q-0001, Q-0002) + retrieves wiki pages ->
   runs ANALYSIS + SYNTHESIS prompts -> writes DIR-0001, DIR-0002 directions + optionally QP-0001
   proposal -> updates hot.md + index.md.
4. **`deep-synthesis --question Q-0001`:** Research agent scans wiki for Q-0001 topic, runs full
   pipeline -> writes `research/Q-0001.md` with [[wikilink]] citations + emits directions.
5. **`question-promote QP-0001`:** On user approval -> Q-0003 research_question created with
   `solved: no`.
6. **`question-solve Q-0001`:** User confirms Q-0001 is solved -> `solved: yes` + `answer_ref` set.
7. **`wiki-gaps`:** Wiki agent runs gap analysis -> gaps output written to output destination (TBD per
   risk R3).
8. **RBAC verification:** attempt a research-agent write to `wiki/` -> RBAC guard denies.
9. Print final `python -m agents.objectives status` + frontier (should show Q-0002 still open,
   Q-0001 solved, 2 directions open).

Expected: all steps complete without errors; `research/Q-0001.md` exists with citations; RBAC deny
fires on the wiki/ write attempt; objectives status shows correct counts.

---

## 10. Open risks and decisions to grill before building

### R1 - Phase 1 follow-ups still pending
The plan lists 4 follow-ups (stale CLI keywords, skills-run-as-subagent dispatch, index.md quality,
ledger completeness). Their status is unclear. Confirm which are done vs deferred before Phase 2
starts. If the dispatch layer (follow-up #2) is not built, research skills will run on the main agent
and RBAC will not be enforced. This is a significant gap.

### R2 - `scripts/obj_init.py` vs extending `scripts/wiki_init.py`
Two design options:
  (A) Separate `obj_init.py` script (parallel to `wiki_init.py`) - cleaner ownership, objective
      scaffold is independently runnable.
  (B) Extend `wiki_init.py` to add an `--init-objectives` flag - single entry point for vault init.
Option A is proposed here. If the user wants a unified init, this changes the file map for Wave 1.

### R3 - `wiki-gaps` write destination
`wiki-gaps` produces a Gaps report. Two candidate write locations:
  (A) `research/query/gaps-YYYY-MM-DD.md` - query-adjacent, research-readable.
  (B) `meta/nightly_report/gaps-YYYY-MM-DD.md` - proposed research input, user-approvable.
The vault-schema.md write-rights for `research/query/` show "Research Agent" as writer. If wiki-gaps
(wiki-owned) writes there, it needs a RBAC exception. Option B avoids the exception. Decision needed.

### R4 - `question-promote` and `question-solve` writing USER-ONLY nodes
These skills need to write to `objective/research_question/` (user-only per RBAC). Three options:
  (A) Narrow RBAC exception: research role ALLOWED to Write `objective/research_question/` ONLY via
      the `question-promote` / `question-solve` skill verbs (skill-aware RBAC).
  (B) `question-promote` writes to a staging area; user manually copies to `objective/research_question/`.
  (C) The skills produce a DRAFT file in `objective/research_question_proposal/` with a different
      `status: promote-ready` and the user moves it.
Option A is cleanest operationally but adds skill-awareness to the RBAC guard (medium complexity).
Option C is the most RBAC-pure but adds friction. Decision needed.

### R5 - `deep-synthesis` as SKILL.md vs a Python script
The deep-synthesis procedure is moderately long (8 steps, two prompt fills, file writes). Two options:
  (A) Pure `SKILL.md` (research agent runs all steps as tool calls) - consistent with Phase 1 skills.
  (B) `scripts/research_synthesis.py` helper (like `scripts/wiki_cite_check.py`) wraps the vault
      scan + prompt fill logic; SKILL.md calls the script - less agent context usage.
Option A is proposed here (consistent with Phase 1 pattern). Option B would be more efficient for
large vaults. Confirm preference.

### R6 - Research agent memory for direction/question catalog
Should `memory/research/` track all open research_questions + directions as a persistent cache, or
should the agent always scan `objective/` fresh at task start? Trade-offs: cached memory is faster
but can drift from vault state; fresh scan is always accurate. Recommendation: fresh scan at task
start (via `objectives frontier`), memory records HIGH-LEVEL decisions and synthesis patterns (not
a full node catalog mirror). Confirm.

### R7 - `obj-synth` vs `deep-synthesis` scope overlap
`obj-synth` (step 5.2) and `deep-synthesis` (step 5.4) both run the two-prompt pipeline and both
emit directions + proposals. Difference: `obj-synth` is a lightweight objective-graph management
skill (always runs, updates the direction set); `deep-synthesis` is a full topic/question report
writer (runs on demand for a specific scope). They share the prompt fill logic. In the code, both
SKILL.md files will call the same pipeline_prompts.py; the risk is duplication. Should the prompt-fill
logic be extracted into a shared `scripts/research_synthesis.py` helper used by both? Confirm before
building to avoid refactoring Wave 2.

### R8 - research/ legacy content reconcile
The live vault has `research/{afd-simulator,daily,notebooklm,youtube}/` from v0.1. `obj_init.py`
leaves them untouched (additive-only). The wiki-init pattern for legacy content is to leave it and
let the lint skill flag it. Confirm this is acceptable for Phase 2, or require a research/ reconcile
step that moves legacy content to schema paths.
