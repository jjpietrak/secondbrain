#!/usr/bin/env python3
"""DRAFT - critical pipeline prompt templates for Second Brain v0.2.

Modeled on .osb_reference/scripts/research/research_deep.py (GAP_PROMPT +
SYNTHESIS_PROMPT). These templates drive the two-lane objective/crawl flow
(see plans/objective-flow.md). They are committed early because they are
critical to pipeline behavior; they will be split into their owning skill
scripts during implementation.

Two lanes feed the Web agent's DECISION block:

  WIKI agent  (bottom-up):  WIKI_GAP_PROMPT      -> Gaps
              (ingest):     WIKI_SYNTHESIS_PROMPT -> wiki page updates (delta)
  RESEARCH    (top-down):   RESEARCH_ANALYSIS_PROMPT  -> per-question gaps
  agent                     RESEARCH_SYNTHESIS_PROMPT -> Directions (+ proposals)

Placeholders are filled with str.format(...). Keep field keys verbatim in the
SYNTHESIS output - parse_directions() / parse_proposals() depend on them.

NOTE (deferred): the Web agent's DECISION block - how Directions + Gaps are
combined, ranked (priority x expected-evidence fit), de-duped against the
ingest index, and turned into a crawl plan - needs its own detailed design
conversation. It is central to webcrawl performance. Do not finalize the
ranking/selection logic here; these prompts only PRODUCE the Directions + Gaps
that the DECISION block consumes. See plans/objective-flow.md "Web DECISION".

Conventions (skills/references/ai-first-rules.md): ASCII only (no em-dashes,
curly quotes, or Unicode math), [[wikilinks]] for vault paths, recency markers
+ source on every external claim, never fabricate.
"""

# ===========================================================================
# WIKI LANE
# ===========================================================================

# Wiki agent, bottom-up gap finder (skill: wiki-gaps). Produces Gaps that feed
# the Web DECISION block. The "## For future Claude" preamble and the
# "## Open Questions" section of existing wiki pages are the STRONGEST, most
# direct signal of what to crawl next and are harvested first.
WIKI_GAP_PROMPT = """You are the Wiki Agent analyzing the vault's own knowledge to surface GAPS that new external sources could fill. Bottom-up: reason from what the wiki already contains, oriented to the purpose. Never fabricate; cite every page with a [[wikilink]] and date stale claims; after an exhaustive read, say "nothing" plainly rather than guessing.

VAULT PURPOSE:
{purpose}

TODAY: {today}

ACTIVE TOPICS:
{active_topics}

CURRENT WIKI STATE (entities + concepts/synthesis + sources, retrieved for the active topics). For each page, its "## For future Claude" preamble and its "## Open Questions" section are included verbatim where present:
{wiki_state}

TOP-PRIORITY SIGNAL - read this first:
The "## For future Claude" preamble and the "## Open Questions" section of each wiki page are the STRONGEST signal of what to research next - they are where prior reasoning explicitly recorded what is uncertain, stale, unverified, or unknown. Treat EVERY unresolved item in those sections as a top-contender gap and web-search direction, ahead of any gap you infer yourself.

Output EXACTLY this structure (markdown), nothing else:

## Open-Question Harvest (TOP PRIORITY)
- [Each unresolved item lifted near-verbatim from a page's "## Open Questions" section, or a staleness/uncertainty caveat in its "## For future Claude" preamble - cite the [[wiki/page]] it came from. These rank first as crawl directions.]

## Coverage Map
- [Per active topic: "solid" | "thin" | "absent", with a representative [[wiki/...]] page]

## Knowledge Gaps
For each gap, one block:
### <short gap title>
- shows_up_in: [thin [[wiki/page]] / an entity referenced with no page / a concept with no examples / a claim with no source]
- missing: [the specific external knowledge that would close it]
- fillable_by: web | x | arxiv | github | forum
- topic: [related topic id]
- priority: high | medium | low  -  [boost to high if it also appears in the Open-Question Harvest]

## Stale / Unverified
- [wiki claim that should be re-verified - name [[wiki/file]] + the source date and why it is suspect]

## Self-contained (no external source needed)
- [gaps that are really synthesis/linking work the wiki agent should do itself, not crawl for]

End with one final line: "READY".
"""


# Wiki agent, ingest-time delta synthesis (skills: wiki-ingest / wiki-synth).
# Combines newly approved/ingested sources with the wiki baseline and emits the
# exact page updates. Its "## New Open Questions" output feeds back into pages'
# "## Open Questions" sections (which WIKI_GAP_PROMPT then harvests next round)
# and becomes candidate research_question_proposals for the research agent.
WIKI_SYNTHESIS_PROMPT = """You are the Wiki Agent synthesizing newly approved/ingested source(s) into the living wiki. Future-Claude reads the wiki to reason over years of knowledge, so be specific, structured, and cite everything. Produce a DELTA vs the current wiki, then the exact page updates.

VAULT PURPOSE:
{purpose}

TODAY: {today}

CURRENT WIKI BASELINE (what the wiki already holds on this material):
{wiki_baseline}

NEW SOURCE(S) BEING INGESTED:
{new_sources}

CRITICAL FORMAT RULES - DO NOT DEVIATE:
- Output ONLY the sections below, in this order, with these exact headers. Markdown bullets, no narrative paragraphs.
- Every external claim carries a recency marker (date) AND a source citation; link the [[raw/...]] source and any affected [[wiki/...]] page by exact path.
- Be ruthless about contradictions - flagging them is the most valuable output.
- Folder discipline (schema): entities = concrete (people, tools, companies, projects); concepts = abstract (ideas, frameworks, theories, methods); synthesis = comparisons / analyses / literature reviews.
- Never fabricate. Unknowns go to "## New Open Questions".

## What's New Since Wiki Baseline
- [specific new fact from the source, with recency marker + [[raw/source]]]

## What's Confirmed
- [existing wiki claim the source agrees with - cite [[wiki/page]]]

## Contradictions / Updates Needed
- [where the source contradicts a wiki note - name [[wiki/file]] and the specific contradiction]

## Recommended Wiki Updates
- [Specific instruction: create or UPDATE [[wiki/entities|concepts|synthesis/...]] with <fact>; each must name an exact page path or describe a new page + its correct folder]

## New Open Questions
- [What the source raises but does not answer. These become "## Open Questions" entries on the affected pages AND candidate research_question_proposals for the research agent.]

End with one final line: "READY".
"""


# ===========================================================================
# RESEARCH LANE
# ===========================================================================

# Research agent, top-down per-question analysis (skills: obj-query / research-deep).
# Reasons over the OBJECTIVES (purpose, open questions, topics), reading the wiki
# and reconciling against the Wiki agent's Gaps. Emits NO queries - synthesis does that.
RESEARCH_ANALYSIS_PROMPT = """You are the Research Agent analyzing the open research agenda against what the vault KNOWS, before any new web research. You reason over OBJECTIVES (purpose, questions, topics), not keywords. Never fabricate; cite vault claims with [[wikilinks]] and date external facts.

VAULT PURPOSE (the fixed mission):
{purpose}

TODAY: {today}

OPEN / UNANSWERED RESEARCH QUESTIONS (solved=no; each serves the purpose):
{open_questions}

ACTIVE TOPICS (each open question relates to one or more):
{active_topics}

RELEVANT WIKI KNOWLEDGE (retrieved for these questions/topics; page "## For future Claude" + "## Open Questions" sections included where present):
{wiki_baseline}

WIKI-AGENT GAPS (bottom-up gaps + Open-Question Harvest already surfaced - reconcile against, do not duplicate):
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


# Research agent, top-down synthesis into Directions (skill: obj-synth).
# Turns purpose + open questions + gaps into objective/direction nodes
# (reasoning patterns, not keywords) and proposes new research_questions for a
# gap that fits no current question. parse_directions/parse_proposals consume it.
RESEARCH_SYNTHESIS_PROMPT = """You are the Research Agent synthesizing RESEARCH DIRECTIONS that will drive the Web Search Agent's next crawl. A direction is a REASONING PATTERN - an angle/hypothesis about WHERE the answer lives - not a bag of keywords. Each must advance a specific open research question in service of the purpose and close a specific gap. Never fabricate; reference vault files with [[wikilinks]].

VAULT PURPOSE:
{purpose}

TODAY: {today}

OPEN RESEARCH QUESTIONS (with per-question gaps from analysis):
{open_questions}

ACTIVE TOPICS:
{active_topics}

GAP ANALYSIS (research per-question analysis + wiki-agent gaps, converged; Open-Question Harvest items rank first):
{gap_analysis}

ALREADY-OPEN DIRECTIONS (do NOT duplicate; extend or skip):
{existing_directions}

Produce 3-7 directions. Output ONLY the blocks below, each in EXACTLY this format (machine-parsed - keep field keys verbatim):

### DIRECTION: <short imperative title>
- serves_question: <research_question id(s)>
- topics: <related topic id(s)>
- targets_gap: <the specific gap this closes; prefer gaps from the Open-Question Harvest>
- reasoning_pattern: <1-2 sentences: the hypothesis for WHERE the evidence lives and WHY looking there advances the question. The objective-driven angle, not keywords.>
- expected_evidence: <the KIND of source/finding that would actually advance the question, so the Web agent can rank candidates - e.g. "a benchmarked measurement on real hardware, not a survey", "a primary arXiv paper", "a vendor datasheet", "practitioner discourse">
- seed_queries: <2-4 concrete starting queries, each tagged: web | x | arxiv | github | forum>
- solves_when: <what a found answer must contain for the served question to be marked SOLVED>
- priority: high | medium | low  -  <justification, ranked by how much it advances the highest-value open question>

Rules:
- Every direction traces to an OPEN question + a real gap. No direction that only re-confirms answered knowledge.
- Prefer directions that close the highest-priority / thinnest-coverage gaps and the Open-Question Harvest items.
- reasoning_pattern must reason over the objective, never just restate keywords.

## Proposed New Research Questions
For each gap that fits NO current open question but clearly serves the purpose, propose one (the user must approve before it becomes an objective/research_question):
- proposal: <well-formed question> | rationale: <why it serves the purpose> | from_gap: <gap ref>
- [or "none"]

## Already Answerable
- [open question ids the vault can already answer (candidate for question-solve), or "none"]

End with one final line: "READY".
"""


# ===========================================================================
# WEB AGENT -- QUERY REFORMULATION (Phase 4A step 2)
# ===========================================================================

# Web agent, crawl-time query reformulation (scripts/web_query.py).
# Takes the current crawl-plan targets (each carrying origin gap/direction ids,
# expected_evidence, wiki-context delta, and old seed queries) and returns
# per-engine, PURPOSE-aligned query variants.
#
# Placeholders (filled by web_query._try_llm_reformulate):
#   {purpose}              -- vault PURPOSE (<=300 chars)
#   {max_per_engine}       -- integer cap on queries per engine
#   {engine_rules}         -- per-engine phrasing rules block (built inline)
#   {target_section}       -- one block per target (see format below)
#   {learned_terms_blurb}  -- learned reject keywords to avoid (or "(none)")
#
# Output contract (machine-parsed by web_query._parse_llm_queries):
#   ONLY a single fenced JSON block, no preamble, no prose.
#   Each target key maps to:
#     "queries_by_engine": {engine: [str, ...]}  (only engines in fillable_by)
#     "rationale": str                            (one line)
WEB_QUERY_REFORMULATION_PROMPT = """You are the Second Brain Web Agent reformulating search queries for an upcoming crawl.

VAULT PURPOSE (the fixed mission -- every query must serve this):
{purpose}

GOAL: For each target below, produce <= {max_per_engine} search queries PER ENGINE that target the DELTA -- what the vault still needs to know, NOT what it already contains. Queries must be engine-appropriate and PURPOSE-relevant.

{engine_rules}

TARGETS TO REFORMULATE:
{target_section}

LEARNED SIGNAL (use this to improve query precision):
{learned_terms_blurb}

RULES:
1. Use the wiki_context_delta to understand what is ALREADY KNOWN -- do NOT query for what the vault already has.
2. Use expected_evidence to understand the KIND of source needed -- shape queries to find that kind.
3. Use old_seed_queries only as a baseline to improve on; do not repeat them verbatim unless they are already ideal.
4. Only include engines listed in the target's fillable_by. Omit engines not in fillable_by entirely.
5. arxiv queries: precise technical noun phrases (2-6 tokens), no question words.
6. semantic_scholar / web queries: well-formed questions or natural-language phrases.
7. forum queries: 2-4 keyword tokens, no question framing.
8. Each query <= 120 characters.
9. Return ONLY the fenced JSON block below. No preamble, no explanation, no trailing text.

```json
{{
  "<target_id>": {{
    "queries_by_engine": {{
      "arxiv": ["<query>"],
      "semantic_scholar": ["<query>"],
      "web": ["<query>"],
      "forum": ["<query>"]
    }},
    "rationale": "<one line>"
  }}
}}
```
"""


# ===========================================================================
# PARSERS (draft) - turn SYNTHESIS output into structured records
# ===========================================================================

def parse_directions(synth_text: str) -> list[dict]:
    """Split RESEARCH_SYNTHESIS_PROMPT output into direction dicts.

    Each '### DIRECTION:' block becomes one objective/direction node. The
    seed_queries + expected_evidence fields are what web-scrape / web-rank
    consume to rank crawl candidates. Stops at the '## Proposed New Research
    Questions' section.
    """
    directions: list[dict] = []
    current: dict | None = None
    for raw in synth_text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):  # left the DIRECTION blocks
            if current:
                directions.append(current)
                current = None
            continue
        if line.startswith("### DIRECTION:"):
            if current:
                directions.append(current)
            current = {"title": line.split(":", 1)[1].strip(), "fields": {}}
            continue
        if current is not None:
            stripped = line.strip().lstrip("-").strip()
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip().lower().replace(" ", "_")
                if key in {
                    "serves_question", "topics", "targets_gap", "reasoning_pattern",
                    "expected_evidence", "seed_queries", "solves_when", "priority",
                }:
                    current["fields"][key] = val.strip()
    if current:
        directions.append(current)
    return directions


def parse_proposals(synth_text: str) -> list[dict]:
    """Extract '## Proposed New Research Questions' lines as proposal dicts.

    Each becomes a research_question_proposal surfaced to the user; on approval
    it is created as an objective/research_question (solved: no).
    """
    proposals: list[dict] = []
    in_section = False
    for raw in synth_text.splitlines():
        line = raw.strip()
        if line.startswith("## Proposed New Research Questions"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.lstrip("-").strip().lower().startswith("proposal:"):
            parts = {p.split(":", 1)[0].strip().lower(): p.split(":", 1)[1].strip()
                     for p in line.lstrip("-").strip().split("|") if ":" in p}
            if parts.get("proposal"):
                proposals.append(parts)
    return proposals
