# Objective Flow - Q2 design artifact

Detailed design for the objective graph + the multi-agent research/crawl pipeline. Companion to
the plan scaffold (`C:\Users\kubap\.claude\plans\elegant-juggling-sparrow.md`). Authoritative docs:
`docs/{requirements,agents,vault-schema}.md`.

**Canonical prompt templates (committed draft): `scripts/prompts/pipeline_prompts.py`** - that module
holds the live templates (`WIKI_GAP_PROMPT`, `WIKI_SYNTHESIS_PROMPT`, `RESEARCH_ANALYSIS_PROMPT`,
`RESEARCH_SYNTHESIS_PROMPT`) + parsers; this doc is the design narrative. The wiki GAP prompt treats
each page's `## For future Claude` preamble and `## Open Questions` section as the top-priority
signal for the next web-search directions.

## Agent cooperation flow (from the cooperation graph)

```
RESEARCH AGENT:  OBJECTIVES ──> ANALYSIS & SYNTHESIS ──> Directions ─┐
                 (purpose, open research_questions, topics; reads wiki)  │
                                                                          ├─> WEB AGENT:
WIKI AGENT:      WIKI ──────────> GAP ANALYSIS ─────────> Gaps ──────────┘   DECISION
                 (entities + concepts/synthesis)                              (Directions + Gaps)
                                                                                  │
                                                                                  v
                                                          WEB CRAWL ─> Raw/staged Sources ─> NEWSLETTER
                                                                                  │
                                                                                  v
                                                          USER APPROVAL ──(Feedback for Learning)──> WEB AGENT
                                                                                  │  (improves search prompts + engine choice)
                                                                                  v
                                                          Approved Sources ─> INGEST (WIKI AGENT)
```

Two convergent lanes feed the Web agent's DECISION: **Directions** (research, objective-driven,
top-down) and **Gaps** (wiki, vault-internal, bottom-up). The Web agent decides what to crawl,
crawls, stages candidates, and the **Newsletter is the digest the user approves from**
(`meta/nightly_report/`). Approved candidates are fetched into `raw/<type>/` and ingested by the
Wiki agent; the approve/reject decisions (with reasons) are the **Feedback-for-Learning** signal the
Web agent uses to tune its prompts and engine selection (`agent-learn`).

## Objective lifecycle (clarified)

- **research_question_proposal:** an agent-synthesized question/hypothesis, raised during a `think`
  action or `research-deep`/`obj-synth`. It is PROPOSED to the user. On user approval, a new
  `objective/research_question` node is created (User-owned per the schema; agents do not self-author
  the agenda).
- **SOLVED is the user's call.** Each `objective/research_question` carries a `solved: yes|no`
  attribute. An agent never flips it autonomously; it may *recommend* "answerable now" (see the
  ANALYSIS prompt's "Already Answerable" output). When the user decides a question is solved, the
  `question-solve [question]` skill (research-owned) records it: set `solved: yes`, link the final
  answer in `research/<question_id>`, and note it in the research agent's memory.
- **Directions** (`objective/direction`) are research-written, do not require approval (they are the
  agent's working trajectory), and drive the crawl. A direction may also surface a
  research_question_proposal when it reveals a gap no current question covers.

## RBAC delta

Research agent now has **READ** rights on `wiki/` (user-extended) - used for `think` actions and the
per-question gap assessment. Write rights unchanged: research writes `objective/` (agent area),
`research/`, `meta/nightly_report/`; never `wiki/`.

---

## Prompt 1 - WIKI agent: GAP ANALYSIS -> Gaps

Bottom-up. The Wiki agent knows the vault best; it surfaces knowledge gaps that external sources
could fill, oriented to the purpose. Distinct from `wiki-lint` (structural health) - this is about
missing/thin/stale KNOWLEDGE. Owner skill: `wiki-gaps`. The `{wiki_state}` baseline is gathered via
`wiki-retrieve` (relevance), not a naive keyword scan.

```python
GAP_ANALYSIS_PROMPT = """You are the Wiki Agent analyzing the vault's own knowledge to surface GAPS that new external sources could fill. Bottom-up: reason from what the wiki contains, oriented to the purpose. Never fabricate; cite every page with a [[wikilink]] and date stale claims. After an exhaustive read, say "nothing" plainly rather than guessing.

VAULT PURPOSE:
{purpose}

TODAY: {today}

ACTIVE TOPICS (the purpose's confirmed sub-areas):
{active_topics}

CURRENT WIKI STATE (entities + concepts/synthesis + sources, retrieved for the active topics):
{wiki_state}

Output EXACTLY this structure (markdown), nothing else:

## Coverage Map
- [Per active topic: one line on how well the wiki covers it - "solid", "thin", "absent" - with a representative [[wiki/...]] page]

## Knowledge Gaps
For each gap, one block:
### <short gap title>
- shows_up_in: [where the gap is visible - a thin/[[wiki/page]], an entity referenced but with no page, a concept with no examples, a claim with no source]
- missing: [the specific external knowledge that would close it]
- fillable_by: web | x | arxiv | github | forum  -  [the source type most likely to have it]
- topic: [related topic id]

## Stale / Unverified
- [wiki claim that should be re-verified - name [[wiki/file]] + the source date and why it is suspect]

## Self-contained (no external source needed)
- [gaps that are really synthesis/linking work the wiki agent should do itself, not crawl for]

End with one final line: "READY".
"""
```

## Prompt 2 - RESEARCH agent: ANALYSIS (per-question gap assessment)

Top-down. Reasons over the OBJECTIVES (purpose, open questions, topics), reading the wiki and
consuming the Wiki agent's Gaps. This is the "ANALYSIS" half of ANALYSIS & SYNTHESIS.

```python
ANALYSIS_PROMPT = """You are the Research Agent analyzing the open research agenda against what the vault KNOWS, before any new web research. You reason over OBJECTIVES (purpose, questions, topics), not keywords. Never fabricate; cite vault claims with [[wikilinks]] and date external facts.

VAULT PURPOSE (the fixed mission):
{purpose}

TODAY: {today}

OPEN / UNANSWERED RESEARCH QUESTIONS (solved=no; each serves the purpose):
{open_questions}

ACTIVE TOPICS (each open question relates to one or more):
{active_topics}

RELEVANT WIKI KNOWLEDGE (retrieved for these questions/topics):
{wiki_baseline}

WIKI-AGENT GAPS (bottom-up gaps already surfaced - reconcile against, do not duplicate):
{wiki_gaps}

Output EXACTLY this structure (markdown), nothing else:

## Purpose Alignment
[2-3 sentences: restate the purpose and how the open questions serve it. Flag any off-purpose question.]

## Per-Question Gap Assessment
For EACH open research question, one block:
### <question_id> - <question text>
- Vault establishes: [what the wiki already answers, cite [[wiki/...]]; "nothing" if absent after an exhaustive read]
- Gap: [the specific missing knowledge blocking a SOLVED answer]
- Stale / contradicted: [vault claims to re-verify - [[wiki/file]] + date; "none found" if so]
- Status: open | partial | blocked  -  [one-line justification]
- Related topics: [topic ids]

## Cross-Cutting Gaps
- [gaps spanning multiple questions/topics; reconcile with the WIKI-AGENT GAPS above]

## Already Answerable (recommend to user)
- [open question ids the vault can already answer -> candidate for the user to mark SOLVED via question-solve; "none" otherwise]

End with one final line: "READY".
"""
```

## Prompt 3 - RESEARCH agent: SYNTHESIS -> Directions (+ proposed questions)

Diverging funnel. Turns purpose + open questions + gaps into Directions (reasoning patterns, not
keywords) and, when a gap fits no current question, proposes a new research_question for user
approval. Output is machine-parsed into `objective/direction` nodes.

```python
SYNTHESIS_PROMPT = """You are the Research Agent synthesizing RESEARCH DIRECTIONS that will drive the Web Search Agent's next crawl. A direction is a REASONING PATTERN - an angle/hypothesis about WHERE the answer lives - not a bag of keywords. Each must advance a specific open research question in service of the purpose and close a specific gap. Never fabricate; reference vault files with [[wikilinks]].

VAULT PURPOSE:
{purpose}

TODAY: {today}

OPEN RESEARCH QUESTIONS (with per-question gaps from analysis):
{open_questions}

ACTIVE TOPICS:
{active_topics}

GAP ANALYSIS (research per-question analysis + wiki-agent gaps, converged):
{gap_analysis}

ALREADY-OPEN DIRECTIONS (do NOT duplicate; extend or skip):
{existing_directions}

Produce 3-7 directions. Output ONLY the blocks below, each in EXACTLY this format (machine-parsed - keep field keys verbatim):

### DIRECTION: <short imperative title>
- serves_question: <research_question id(s)>
- topics: <related topic id(s)>
- targets_gap: <the specific gap this closes>
- reasoning_pattern: <1-2 sentences: the hypothesis for WHERE the evidence lives and WHY looking there advances the question. The objective-driven angle, not keywords.>
- expected_evidence: <the KIND of source/finding that would actually advance the question, so the Web agent can rank candidates - e.g. "a benchmarked measurement on real hardware, not a survey", "a primary arXiv paper", "a vendor datasheet", "practitioner discourse">
- seed_queries: <2-4 concrete starting queries, each tagged: web | x | arxiv | github | forum>
- solves_when: <what a found answer must contain for the served question to be marked SOLVED>
- priority: high | medium | low  -  <justification, ranked by how much it advances the highest-value open question>

Rules:
- Every direction traces to an OPEN question + a real gap. No direction that only re-confirms answered knowledge.
- Prefer directions that close the highest-priority / thinnest-coverage gaps.
- reasoning_pattern must reason over the objective, never just restate keywords.

## Proposed New Research Questions
For each gap that fits NO current open question but clearly serves the purpose, propose one (the user
must approve before it becomes an objective/research_question):
- proposal: <well-formed question> | rationale: <why it serves the purpose> | from_gap: <gap ref>
- [or "none"]

## Already Answerable
- [open question ids the vault can already answer (candidate for question-solve), or "none"]

End with one final line: "READY".
"""
```

## Parsing + integration

```python
def parse_directions(synth_text: str) -> list[dict]:
    """Each '### DIRECTION:' block -> one objective/direction node. seed_queries +
    expected_evidence are what web-scrape/web-rank consume to rank crawl candidates."""
    ...

def parse_proposals(synth_text: str) -> list[dict]:
    """'## Proposed New Research Questions' lines -> proposals surfaced to the user;
    on approval each becomes an objective/research_question (solved: no)."""
    ...
```

- **Run by:** Research agent. `wiki-gaps` (Prompt 1) is Wiki-owned. `obj-synth` / `research-deep`
  run Prompts 2+3.
- **Web DECISION** consumes `objective/direction` (Directions) + the Wiki agent's Gaps, ranks by
  priority x evidence-fit, crawls, stages candidates into `meta/nightly_report/`.

> **DEFERRED - dedicated design conversation needed.** The Web agent's DECISION block (how
> Directions + Gaps are combined, ranked by priority x expected-evidence fit, de-duped against the
> ingest index, budget-bounded, and turned into an actual crawl plan + engine choice) is central to
> webcrawl performance and is NOT yet designed. The prompts above only PRODUCE the Directions + Gaps
> it consumes. Design the DECISION block in its own session before building Phase 3.
- **Newsletter** renders `meta/nightly_report/` as the approval digest; **User Approval** promotes
  approved candidates to `raw/<type>/` (Wiki ingests) and is the **Feedback-for-Learning** signal.
- **question-solve [question]** (research skill): user-triggered; sets `solved: yes`, links
  `research/<question_id>`, records it in research memory.
