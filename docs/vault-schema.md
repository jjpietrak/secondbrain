# Vault Schema

This is the authoritative v0.2 vault schema for Second Brain: the folder structure and per-agent permission matrix, merged with frontmatter, naming, bi-temporal, and Dataview conventions carried over from the reference schema.

## Folder structure & agent permissions

The matrix below is authoritative for v0.2. Permission values are preserved verbatim.

### OBJECTIVE


| FOLDER NAME                          | Description                                                                                                                                                                                                                                                            | Agent Edit Rights | Write Rights   | Read Rights    |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | -------------- | -------------- |
| objective/purpose                    | Vault's purpose defines primary research direction & most cricial mission objective. It remains fixed and uchanged for vault's lifespan. It represents the problem we're solving.                                                                                      | NO                | User Only      | Research Agent |
| objetive/topic                       | Confirmed research direction. Top-level concept that plays crucial role in completing vault's purpose and can be treated as sub-problem of the main problem.                                                                                                           | NO                | User Only      | Research Agent |
| objective/research_question          | Well formed research question that research agents are solving. If it has a definite and documented answer it can be considered SOLVED.                                                                                                                                | NO                | User Only      | Research Agent |
| objective/decision                   | User Only defined decision for the agent to adhere to while performin automated actions. Can include positive (do) and negative (don't) instructions. Should be translated directly to agent skills.                                                                   | NO                | User Only      | Research Agent |
| objective/research_question_proposal | Agent's research question synthesised in ingest, query or research task. It can be proposed as supplementary problem to solve or hypothesis to test using high effort AI work or needs User Only input or revision and is Out of Scope for the current running action. | YES               | Research Agent | Research Agent |
| objective/direction                  | Research trajectory that hasn't yet been promoted to research topic. It can represent reasoning pattern that drives web-crawl and reasoning agent to fill up a knowledge gap in the vault's domain.                                                                    | YES               | Research Agent | Research Agent |
| objective/agent_todo                 | Agent self-ordered todo, self-reflection, correcting instruction or slight change of behavior to reinforce next research or answer to query.                                                                                                                           | YES               | Research Agent | Research Agent |
| objective/hot                        | last reasoning over objectives context, current focus, blind spots, proposed new research directions, open work threads (next sessions)                                                                                                                                | YES               | Research Agent | Research Agent |
| objective/index                      | master catalog, one row per agent operation                                                                                                                                                                                                                            | YES               | Research Agent | Research Agent |


### RESEARCH


| FOLDER NAME     | Description                                                              | Agent Edit Rights | Write Rights   | Read Rights    |
| --------------- | ------------------------------------------------------------------------ | ----------------- | -------------- | -------------- |
| research/deep/  | deep research reports                                                    | YES               | Research Agent | Research Agent |
| research/query/ | saved queries history Q&A                                                | YES               | Research Agent | Research Agent |
| research/       | major & ongoing research topic synthesis, current state of our knowledge | YES               | Research Agent | Research Agent |
| research/       | Final summary answering stated research question.                        | YES               | Research Agent | Research Agent |


### META


| FOLDER NAME          | Description                                                                      | Agent Edit Rights | Write Rights   | Read Rights   |
| -------------------- | -------------------------------------------------------------------------------- | ----------------- | -------------- | ------------- |
| meta/health_report/  | health check of the vault, most edited pages, category statistics, tag statistic | YES               | Backend Agent  | Backend Agent |
| meta/cost_report/    | cost & token control                                                             | YES               | Backend Agent  | Backend Agent |
| meta/ingest_index/   | structured json tracking ingested/deleted/todo raw sources with keys             | YES               | Wiki Agent     | Wiki Agent    |
| meta/nightly_report/ | proposed new sources for User Only approval                                      | YES               | Research Agent / Web Agent | Wiki Agent    |


### RAW


| FOLDER NAME      | Description                    | Agent Edit Rights | Write Rights            | Read Rights |
| ---------------- | ------------------------------ | ----------------- | ----------------------- | ----------- |
| raw/papers/      | arxiv                          | Needs Approval    | Wiki Agent              | User Only   |
| raw/articles/    | blog, newsletter               | Needs Approval    | Wiki Agent              | User Only   |
| raw/transcripts/ | YT                             | Needs Approval    | Wiki Agent              | User Only   |
| raw/notes/       | Personal Notes                 | Needs Approval    | Wiki Agent              | User Only   |
| raw/opinions/    | X, Reddit                      | Needs Approval    | Wiki Agent              | User Only   |
| raw/assets/      | images, attachment folder path | Needs Approval    | Wiki Agent              | User Only   |
| raw/code/        | GitHub Repos                   | Needs Approval    | Wiki Agent / Code Agent | User Only   |
| raw/notebooklm/  | notebook lm api & synced files | Needs Approval    | nlm sync action         | User Only   |


### WIKI


| FOLDER NAME     | Description                                                                                                                  | Agent Edit Rights | Write Rights | Read Rights                 |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------ | --------------------------- |
| wiki/entities/  | people, tools, companies, projects                                                                                           | YES               | Wiki Agent   | Wiki Agent & Research Agent |
| wiki/concepts/  | ideas, frameworks, theories, methods                                                                                         | YES               | Wiki Agent   | Wiki Agent & Research Agent |
| wiki/synthesis/ | comparisons, deep analyses short summary, literature reviews                                                                 | YES               | Wiki Agent   | Wiki Agent & Research Agent |
| wiki/sources/   | one structured summary per ingested raw source                                                                               | YES               | Wiki Agent   | Wiki Agent & Research Agent |
| wiki/gap/       | one file per knowledge GAP (`GAP-NN-<slug>.md`, gap template) + `index.md` overview (coverage map / stale / self-contained / open-question harvest); bottom-up gap analysis by `wiki-gaps`, machine-read by the web agent DECISION | YES | Wiki Agent | Wiki Agent & Research Agent & Web Agent |
| wiki/index.md   | master catalog, one row per agent operation                                                                                  | YES               | Wiki Agent   | Wiki Agent & Research Agent |
| wiki/hot.md     | last ingest session context, current focus, blind spots, proposed new research directions, open work threads (next sessions) | YES               | Wiki Agent   | Wiki Agent & Research Agent |


## Backend audit & version-control capabilities (v0.2)

These backend-owned operations span the whole vault and are not folder-scoped content writes:

- **Read-all for auditing.** The `backend` agent reads ALL markdown vault areas (`wiki/`, `objective/`, `research/`, `meta/`) READ-ONLY to run health/stats audits (`vault-health`, `wiki-health`, `wiki-stats`). It still WRITES only `meta/health_report/` + `meta/cost_report/` (per the matrix above). `vault-health` is the all-area superset of the wiki-only `wiki-health` (`--area` scopes it).
- **Version control.** `vault-push` performs `git pull --rebase --autostash` + `add -A` + commit + push over the entire vault git repository. This is a version-control action, NOT content editing — it never creates or modifies note content. Cross-host lease coordination (Layer 1, `vault_lease.sh`) is wired in Phase 4.

## Frontmatter schemas

The schemas below are carried over from the reference schema. Where a schema references folders that do not exist in the v0.2 matrix above, an inline note flags the divergence; the schema content is preserved as-is.

```yaml
---
date: 2026-03-24
tags:
  - entity
  - person       # or: company, tool
role: "Senior Engineer"        # current role
company: "[[Acme Corp]]"       # current company
last_interaction: 2026-03-24
timeline:                       # bi-temporal facts - never delete, only append
  - fact: "CTO at Acme Corp"
    from: 2024-01-01            # event time: when the fact was true
    until: 2026-04-07
    learned: 2026-02-23         # transaction time: when the vault learned it
  - fact: "Architect at Acme Corp"
    from: 2026-04-07
    until: present
    learned: 2026-04-07
    source: "[[2026-04-07]]"    # where the vault learned it from
---
```

### Concept Note

```yaml
---
date: 2026-03-24
tags:
  - concept
status: active   # active | graduated | archived
related_projects: []
---
```

### Source Note (raw/)

```yaml
---
date: 2026-03-24
tags:
  - source
source_type: article   # article | transcript | pdf | video
source_url: "https://..."
content_hash: ""       # for drift detection
---
```

### Project Note

> NOTE: folder differs from v0.2 matrix. The reference assumes `wiki/projects/`, which does not exist in the v0.2 matrix.

```yaml
---
date: 2026-03-24
tags:
  - project
status: active   # active | planning | completed | archived | on-hold
job: "[[Acme Corp]]"   # or Personal, [[Company Name]]
timeline:                # bi-temporal facts - status changes over time
  - fact: "status: planning"
    from: 2026-03-01
    until: 2026-03-15
    learned: 2026-03-01
  - fact: "status: active"
    from: 2026-03-15
    until: present
    learned: 2026-03-15
---
```

### Daily Note

> NOTE: folder differs from v0.2 matrix. The reference assumes `wiki/daily/`, which does not exist in the v0.2 matrix.

```yaml
---
date: 2026-03-24
tags:
  - daily
mood: 4          # 1-5 scale
energy: 3        # 1-5 scale
---
```

### Task Note

> NOTE: folder differs from v0.2 matrix. The reference assumes `wiki/tasks/`, which does not exist in the v0.2 matrix.

```yaml
---
date: 2026-03-24
tags:
  - task
status: in-progress   # in-progress | done | waiting | cancelled
project: "[[Project Name]]"
job: "[[Company]]"    # or Personal
requested_by: "[[Person Name]]"
due: 2026-03-28
---
```

### Dev Log

> NOTE: folder differs from v0.2 matrix. The reference assumes `wiki/logs/` (Dev Logs), which does not exist in the v0.2 matrix.

```yaml
---
date: 2026-03-24
tags:
  - devlog
project: "[[Project Name]]"
job: "[[Company]]"
---
```

### Decision Record (ADR)

> NOTE: folder differs from v0.2 matrix. The reference assumes `wiki/decisions/`, which does not exist in the v0.2 matrix. (The v0.2 matrix has `objective/decision` with a different meaning - User-defined agent instructions.)

```yaml
---
date: 2026-03-24
tags:
  - decision-record
status: accepted   # accepted | superseded | deprecated
---
```

### Kanban Board

> NOTE: folder differs from v0.2 matrix. The reference assumes a `boards/` folder, which does not exist in the v0.2 matrix.

```yaml
---
kanban-plugin: board
---
```

### Goal

> NOTE: folder differs from v0.2 matrix. The reference has no dedicated goal folder in the v0.2 matrix.

```yaml
---
date: 2026-01-01
tags:
  - goal
category: "career"   # career | health | financial | personal | relationship
status: active       # active | completed | paused | abandoned
progress: 35         # 0-100 integer
target_date: 2026-12-31
---
```

## Naming conventions

Carried over from the reference schema.


| Type           | Pattern                        | Example                                  |
| -------------- | ------------------------------ | ---------------------------------------- |
| Daily note     | `YYYY-MM-DD.md`                | `2026-03-24.md`                          |
| Dev log        | `YYYY-MM-DD - Description.md`  | `2026-03-24 - API Gateway Debug.md`      |
| Entity         | Full name (flat)               | `Jane Smith.md`, `Acme Corp.md`          |
| Concept        | Descriptive title              | `LLM-Wiki Pattern.md`                    |
| Project        | Proper name                    | `My Project Name.md`                     |
| Source         | `YYYY-MM-DD - Source Title.md` | `2026-04-06 - Karpathy LLM Wiki.md`      |
| Decision       | `ADR-YYYY-MM-DD - Title.md`    | `ADR-2026-04-06 - Wiki Style Default.md` |
| Archive prefix | `_archived_`                   | `_archived_Old Project.md`               |


> NOTE: Daily note, Dev log, Project, and Decision naming patterns refer to note types whose reference folders (`wiki/daily/`, `wiki/logs/`, `wiki/projects/`, `wiki/decisions/`) differ from the v0.2 matrix.

## Bi-temporal facts

Carried over from the reference schema.

**Bi-temporal facts rule:** never overwrite a role, company, status, or location. Add a new entry to `timeline:` with:

- `from` / `until` - **event time**: when the fact was true in reality
- `learned` - **transaction time**: when the vault first recorded this fact
- `source` (optional) - where the vault learned it from (daily note, ingested source, etc.)

The `role:` and `company:` top-level fields always reflect the CURRENT state. The `timeline:` preserves full history.

This enables:

- Historical queries ("who was CTO in January?")
- Reflective thinking ("you believed X on Tuesday, but after ingesting Y on Wednesday, your understanding shifted to Z")
- Smart reconciliation (different roles at different times = not a contradiction)
- Audit trail (when did the vault learn each fact, and from what source?)

## Dataview query patterns

Carried over from the reference schema. Folder paths in the queries reflect the reference schema and may differ from the v0.2 matrix (see inline notes).

### All active projects

> NOTE: folder differs from v0.2 matrix. `wiki/projects` does not exist in the v0.2 matrix.

```dataview
TABLE status, job FROM "wiki/projects"
WHERE contains(tags, "project") AND status = "active"
SORT file.name ASC
```

### Recent daily notes

> NOTE: folder differs from v0.2 matrix. `wiki/daily` does not exist in the v0.2 matrix.

```dataview
TABLE date, mood, energy FROM "wiki/daily"
SORT date DESC
LIMIT 7
```

### All entities (people, companies, tools)

```dataview
TABLE role, company, last_interaction FROM "wiki/entities"
WHERE contains(tags, "entity")
SORT last_interaction DESC
```

### Recent sources ingested

```dataview
TABLE source_type, source_url FROM "raw"
SORT date DESC
LIMIT 10
```

## Open notes / to confirm with user

> The following ambiguities were surfaced during the merge but NOT resolved. Please confirm.
>
> 1. `**wiki/concepts/` vs `wiki/entities/` look swapped.** In the v0.2 matrix, `wiki/concepts/` is described as "people, tools, companies, projects" (concrete things) and `wiki/entities/` as "ideas, frameworks, theories, methods" (abstract things). This is the reverse of the usual convention (entities = concrete, concepts = abstract) and of the reference schema (entities = "People, companies, tools"; concepts = "Ideas, frameworks, methodologies"). Descriptions left unchanged - confirm intended mapping.
> 2. `**objetive/topic` typo.** The folder name in the OBJECTIVE area reads `objetive/topic` (all other rows use `objective/`). Likely should be `objective/topic`. Folder name left unchanged per instructions - confirm.
> 4. **v0.2 folders absent from the reference schema.** Conversely, the v0.2 matrix introduces folders with no reference counterpart: the entire `objective/` area (purpose, topic, research_question, decision, research_question_proposal, direction, agent_todo, hot, index), `research/` (deep, query, , ), `meta/` (health_report, cost_report, ingest_index, nightly_report), `raw/papers/`, `raw/articles/`, `raw/transcripts/`, `raw/notes/`, `raw/opinions/`, `raw/assets/`, `raw/code/`, `raw/notebooklm/`, `wiki/synthesis/`, `wiki/sources/`, `wiki/index.md`, `wiki/hot.md`. No reference frontmatter exists for these new note types - confirm whether schemas should be authored.
> 5. `**objective/decision` vs reference ADR `decisions/`.** The v0.2 `objective/decision` ("User Only defined decision for the agent to adhere to") is semantically different from the reference's `wiki/decisions/` ADRs. The ADR frontmatter was kept under its own note type with a divergence note; confirm whether ADRs belong in v0.2 at all.

