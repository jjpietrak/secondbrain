---
name: think
description: "Apply the 10-principle thinking loop (OBSERVE-OBSERVE-LISTEN-THINK-CONNECT-CONNECT-FEEL-ACCEPT-CREATE-GROW) to any non-trivial problem in the Second Brain. Walks Claude through external observation, metacognition, active listening, first-principles analysis, lateral connection, system orchestration, intuition, intellectual humility, generative output, and iterative growth. Shared thinking tool: usable by the wiki agent and the research agent. Triggers on: think this through, 10-principle review, /think, OBSERVE LISTEN THINK, deep think, systematic thinking, structured reasoning, walk this through, audit my thinking, am I thinking about this right, what am I missing."
allowed-tools: Read, Grep, Glob, Bash
---

# think: The 10-principle thinking loop

A meditation, a discipline, and a checklist. Use this skill when a problem is non-trivial
enough that disciplined thinking pays for itself: architectural decisions, post-mortems,
ambiguous user requests, vault audits, multi-source contradiction calls, "should we ingest
this?" moments, "what are we missing in this research direction?" moments.

The 10 principles are not a recipe. They are stages of attention. You move through them in
order on the first pass, then loop back to the earlier ones as new information emerges. The
discipline is in NOT skipping the awkward ones (OBSERVE-internal, ACCEPT, GROW) just because
they are uncomfortable.

This is the meta-skill that informs how the other thinking tools reason. Both `challenge` and
`connect` carry a "How to think" appendix mapping these 10 stages to their specific work.

## Scope: shared thinking tool (wiki + research)

`think` is a SHARED reasoning skill. It is default-owned by the `wiki` agent and is also
invokable by the `research` agent. It loads structure and discipline only; it does NOT write
to the vault, run mutations, or move files. There is no RBAC concern and no locking needed:
nothing is written. What you DO with the thinking (a wiki page, a research synthesis, an
ingest decision) is the next skill's job, and that next skill carries its own write rules and
locks.

---

## The 10 principles

### 1. OBSERVE (the external input)

Thinking begins with data collection. Look at the environment, the current landscape, the
patterns and gaps and opportunities, without immediately trying to solve them. Read the raw
inputs.

In practice: read the wiki page before updating it. Read `wiki/hot.md` + `wiki/index.md`
before answering a question that should be sourced. Read the raw source in `raw/` before
trusting a claim. Read every row of the ingest index before claiming a source is new. Resist
the urge to jump to a fix on the first symptom.

### 2. OBSERVE (the internal metacognition)

Now observe yourself. This is metacognition - thinking about how you are thinking. Are you
operating on assumptions? Do you have a bias toward a particular research direction? Are you
anchored on a previous synthesis? Is there a finding count you are unconsciously targeting?

In practice: write a one-paragraph "bias log" before scoring a source's relevance against the
vault PURPOSE. Note ownership bias, ship-it bias, familiarity bias, anchoring. The bias does
not go away by being noted - it gets contained.

### 3. LISTEN (active receptivity)

Observing is often analytical. Listening requires shutting down the ego to absorb external
feedback. Pay attention to user intent, prior corrections in memory, error messages, the
subtle signals that tell you what the user actually needs rather than what you think they need.

In practice: read the skill description before assuming what a skill does. Read the user's
exact phrasing before paraphrasing it back. Read the failure message before guessing the
failure mode. The user's confusion is data. A prior `objective/decision` (do/don't
instruction) is the user's voice - honor it.

### 4. THINK (critical processing)

The analytical engine. Once you have the inputs, break the problem down to first principles.
Structure the logic, map the workflow, evaluate the constraints, synthesize the raw data into
a coherent strategy.

In practice: this is the six-cut engineering kernel. Read-before-write. Name like the next
reader is hostile. Smallest unit that works. Delete more than you add. Evidence over
intuition. Failure is the spec. THINK is where rigor pays off, but it cannot start without
stages 1-3.

### 5. CONNECT (associative / lateral thinking)

Great ideas rarely happen in a vacuum; they happen at intersections. Take two seemingly
unrelated concepts and link them. Retrieval architecture x LLM compaction. Co-packaged optics
x attention/FFN disaggregation. The "Aha" moment is finding the hidden relationship between
distinct variables.

In practice: when auditing a wiki page, ask "does this contradiction pattern exist in adjacent
pages?" When designing a schema change, ask "what other interface is this isomorphic to?" This
is the stage the `connect` skill operationalizes across vault domains.

### 6. CONNECT (system orchestration)

The second CONNECT is about execution. Moving from an isolated idea to an integrated system.
How do these individual notes, sources, and agents plug into one another to create a coherent
whole? This is the principle of building the wiring.

In practice: when shipping a new wiki page, audit how it integrates with the ingest index, the
locks on shared append targets (`wiki/index.md`, `wiki/log.md`, `wiki/hot.md`), backlinks, and
the retrieval pipeline. The page that is correct in isolation but breaks the index is not a
working page.

### 7. FEEL (intuition + the human element)

Pure logic is brittle without judgment. Factor in the human element. A wiki note that future-
Claude cannot retrieve fails FEEL even if every fact is true. Trust hard-earned intuition when
the data is ambiguous.

In practice: a note with no `## For future Claude` preamble fails FEEL. A research direction
that is technically defensible but ignores the vault PURPOSE fails FEEL. If your intuition says
"this contradiction is not really a contradiction, it is a bi-temporal change over time", trust
it and check the `timeline:`.

### 8. ACCEPT (intellectual humility)

No plan survives first contact with reality. Embrace constraints. Acknowledge when a
hypothesis fails. Let go of sunk cost.

In practice: tier findings honestly. If a source is weakly relevant, say so; do not inflate its
relevance to justify the ingest. If a synthesis is speculative, mark `confidence: speculation`,
not `high`. ACCEPT is the firewall against sycophancy.

### 9. CREATE (generative output)

Analysis paralysis is the enemy of progress. At some point, stop strategizing and produce. Write
the wiki page. File the synthesis. Mark the ingest decision. Ship the audit report.

In practice: an audit that never gets written is worse than a B+ audit that ships. CREATE is the
answer when the prior stages have given you enough. Hand off to the skill that does the write
(wiki-save, wiki-synth, connect, etc.) which carries the write rules and locks.

### 10. GROW (the iterative loop)

Thinking is a feedback loop. Take what you built, see how it performs, use the lessons to upgrade
for the next cycle.

In practice: every non-trivial cycle ends with a GROW step. What worked? What to improve? Where
should this lesson live so future-me does not re-derive it - a wiki page, the agent memory, or
`wiki/hot.md`?

---

## When to invoke

Invoke `think` when:

- You are about to make a non-trivial decision (a schema change, a contradiction call, choosing
  between retrieval strategies, whether a source meets the vault PURPOSE bar).
- You are auditing the vault and need a methodology spine.
- The user's request is ambiguous and you need to listen harder before responding.
- You hit a surprising result and need OBSERVE-internal before adjusting.
- You are about to call something done and need ACCEPT (anti-sycophancy) before the verdict.
- A post-mortem after something went sideways.
- Closing out a session and need a GROW step before saving.

Do NOT invoke `think` for:

- Single-line typo fixes (just fix it).
- Trivial lookups (just answer).
- Cases where you have already moved through the 10 stages implicitly (do not ceremonially
  re-do it).

The framework's value scales with problem novelty and irreversibility. For a one-line fix that
is easily reverted, the loop is dead weight. For an irreversible vault write or a
release-blocking audit, skipping any stage loses calibration.

---

## How to use

```
think <problem statement>
```

Walk through the 10 stages in order. For each, answer the prompt questions below. Stage outputs
feed into stage 9 (CREATE), which produces a recommendation or hands off to a writing skill.

Stages 1, 4, 9 are usually short. Stages 2, 7, 8, 10 are where most people skip. Watch yourself
there.

---

## Stage-by-stage prompts

### 1. OBSERVE (external)
- What are the raw inputs? (Wiki pages? Raw sources? Ingest index? User intent? Logs?)
- What have I read in full vs. skimmed vs. assumed?
- What is the state right now? (hot.md, index.md, recent ingests, open work threads)
- What surprises me, before I start interpreting?

### 2. OBSERVE (internal)
- What am I biased toward here? (Ownership, ship-it, novelty, anchoring, familiarity)
- What outcome am I unconsciously hoping for? Why?
- If a fresh-context reviewer joined now, what would they question that I take for granted?
- Is my confidence calibrated to the evidence I actually have?

### 3. LISTEN
- What did the user actually ask for? (Quote verbatim.)
- What signals are in the noise? (Word choice, what they did NOT say, prior corrections, any
  `objective/decision` do/don't instruction.)
- What does the vault PURPOSE say about whether this matters?
- Whose voice is missing from this decision?

### 4. THINK
- What are the first principles in play? (Constraints, invariants, blast radius.)
- Apply the six-cut kernel: read-before-write, hostile naming, smallest unit,
  delete-more-than-you-add, evidence-over-intuition, failure-is-the-spec.
- What alternatives have I NOT considered?
- What is the cheapest experiment that would prove me wrong?

### 5. CONNECT (lateral)
- Where else does this pattern show up in the vault?
- What unrelated domain solved a structurally similar problem? (This is where `connect` helps.)
- If this bug exists in this page, does it exist in three neighbors?
- What metaphor unlocks the user's intuition for this?

### 6. CONNECT (system)
- How does this plug into the existing wiring? (Ingest index, locks, backlinks, retrieval.)
- Does anything need updating downstream/upstream/across? (index.md, log.md, hot.md.)
- What new failure modes does integration create that the isolated note does not have?
- Is the documentation about integration up to date?

### 7. FEEL
- How does this land for future-Claude retrieval? (Preamble, frontmatter, wikilinks.)
- What state is the user in when they hit this? (Exploring? Time-pressured?)
- Does my intuition say "something is off" even when the data says "we are good"?
- Does my intuition say "we are good" even when I cannot articulate why?

### 8. ACCEPT
- What is the honest tier of this finding / verdict? (No inflation.)
- What constraint am I being asked to soften that I should not?
- What sunk cost am I protecting that I should release?
- If this were someone else's work, would I be more critical?

### 9. CREATE
- What is the smallest artifact that ships the decision?
- Which skill does the actual write? (Hand off; it carries the write rules and locks.)
- Are the inputs sufficient, or do I need to loop back to an earlier stage?
- Ship it.

### 10. GROW
- What worked well this cycle?
- What would I do differently next time?
- What inputs feed the next cycle?
- Where should this lesson be stored so future-me does not re-derive it? (Wiki page? Agent
  memory? `wiki/hot.md`?)

---

## Anti-patterns

- **Skipping OBSERVE-internal.** Going straight from external observation to THINK without
  auditing your own biases produces confident wrong answers.
- **Skipping ACCEPT.** Padding a relevance score, hedging a contradiction verdict, calling a
  speculative synthesis "high confidence". Anti-sycophancy dies the moment ACCEPT is optional.
- **Skipping GROW.** Producing the artifact and moving on. Next cycle starts at the same
  baseline; nothing compounds.
- **Analysis paralysis at THINK.** Looping inside stage 4 forever, never reaching CREATE. The
  framework is a sequence, not a stopping rule.
- **Ceremony.** Writing all 10 stages for a one-line fix. Cost should scale with stakes.

---

## Composition with other skills

- **`challenge`**: an OBSERVE-internal + ACCEPT amplifier. It red-teams a position against the
  vault's own history so the bias does not announce itself unchecked.
- **`connect`**: the CONNECT-lateral stage operationalized across vault domains.
- **`wiki-save` / `wiki-synth`**: after GROW, save the insight worth not re-deriving. The note
  IS the GROW artifact. These carry the write rules and locks.
- **`wiki-lint` / backend `wiki-health`**: periodic vault audits are themselves a GROW step at
  the system level.

The 10 principles are a meditation. Without the stance, the framework becomes ceremony. With the
stance, every cycle compounds.
