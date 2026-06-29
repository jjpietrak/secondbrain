---
name: challenge
description: "Red-team a current idea, plan, or assumption against the vault's own history - find contradictions, past reversed decisions, and flawed premises, then push back using the user's own prior words. Searches wiki/ (concepts, entities, synthesis, sources), objective/decision, and wiki/log.md for counter-evidence. Shared thinking tool: usable by the wiki agent and the research agent. Triggers on: challenge this, grill me on this, red team my idea, stress test this, am I wrong about this, push back on me, devil's advocate, what does my own history say."
allowed-tools: Read, Grep, Glob, Bash
---

**Shared thinking tool (`wiki` + `research`): dispatch to the subagent owning the task (`subagent_type: wiki` for vault-knowledge work, `research` for objective/research work); if already running as one of them, proceed.**

# challenge: red-team an idea against your own vault history

Pressure-test a claim, plan, or assumption against the vault's accumulated record. The point is
NOT to be agreeable. Find where the vault already disagrees with the current position - past
decisions that were reversed, failures, risks the user flagged, sources whose conclusions cut
the other way - and push back with the user's own prior words.

Adapted from the reference `obsidian-challenge` command. Differences for this vault: no
`daily/` note exists to log to (the red-team returns inline, or is filed to `wiki/synthesis/`
on request); the user's standing do/don't instructions live in `objective/decision`; citations
are `[[wikilinks]]` ONLY (no markdown `[text](path)` links).

## Scope: shared thinking tool (wiki + research)

`challenge` is a SHARED reasoning skill, default-owned by the `wiki` agent and also invokable by
the `research` agent. It is read-heavy: it searches and reasons over the vault and returns the
red-team inline. It writes ONLY if the user asks to file the analysis (see "Optionally filing
the red-team"). When it writes, it writes to `wiki/synthesis/` (wiki agent's RBAC); the research
agent invoking this skill must hand a file-write back to the wiki agent rather than writing into
`wiki/` itself. Reading the whole vault is in scope for both agents (wiki and research both have
read rights across `wiki/`).

---

## Procedure

1. **Load orientation.** Read `objective/purpose` (the vault PURPOSE - the standing relevance
   filter) and `wiki/hot.md` (current focus, open threads, blind spots) so the red-team is
   grounded in what this vault is actually for. Also read any relevant `objective/decision`
   entries - those are the user's standing do/don't instructions and are prime counter-evidence.

2. **Identify the position.** Pin down the user's current claim, plan, or assumption - from the
   argument if given, otherwise inferred from recent conversation. State it back in one
   sentence and extract its key premises. Quote the user verbatim where you can.

3. **Search the vault for counter-evidence.** Search exhaustively (do NOT sample). Cover, at a
   minimum:
   - `objective/decision` - standing do/don't instructions that contradict the plan.
   - `wiki/synthesis/` - prior analyses / comparisons whose conclusions cut against the position.
   - `wiki/concepts/` and `wiki/entities/` - notes where a `timeline:` shows the user once held
     the opposite view, or a status that was later reversed (bi-temporal: a reversal over time
     is real counter-evidence; a fact that was simply superseded by a later event is NOT - read
     the `timeline:` carefully).
   - `wiki/sources/` and `raw/` - ingested sources whose findings contradict the claim, with the
     source URL preserved verbatim.
   - `wiki/log.md` - past ingest/work sessions where this topic was flagged as risky or dropped.
   If retrieval is provisioned, use `wiki-retrieve` to widen the search before the legacy
   grep/list scan; fall back to grep/Glob if it is not.

4. **Synthesize a structured Red Team.** Return:
   - **Your position** - the claim restated clearly.
   - **Counter-evidence from your vault** - cite specific `[[Page]]` notes, dates, and direct
     quotes. Every cited fact carries its recency marker `(as of YYYY-MM, source URL)` and, for
     bi-temporal facts, the relevant `timeline:` window.
   - **Blind spots** - what the user may be ignoring based on their own recorded history.
   - **Verdict** - is this position consistent with past experience, or does the vault suggest
     caution? Be honest; mark inference vs. stated counter-evidence with a confidence level.

5. **Honesty on absence.** If you find NOTHING contradictory, say so plainly - but only after an
   exhaustive search by every plausible name, alias, and folder. False absence ("nothing in the
   vault disagrees") when something does is the most common failure mode. Never fabricate a
   counter-quote, a date, or a `[[Page]]` that does not exist.

Do not be agreeable. The entire value is the pushback. If the vault genuinely supports the
position, say that too - calibration cuts both ways.

---

## Optionally filing the red-team

By default the analysis is returned inline. If the user asks to keep it, file it to
`wiki/synthesis/` as a `type: challenge` note. It MUST follow `references/ai-first-rules.md`
and `references/write-rules.md`:

- `## For future Claude` preamble; `ai-first: true`; `type: challenge`; `date: YYYY-MM-DD`; the
  type in `tags:`; `sources:` listing the `[[vault notes]]` that informed it.
- Every person/project/concept/source referenced as a `[[wikilink]]` (mandatory; no
  `[text](path)`). Recency markers + verbatim source URLs on every external claim. Confidence
  levels on inferences.
- ASCII only: ` - ` for dashes, straight quotes, `>=`/`!=` for math. No em-dash, curly quotes,
  or Unicode math (caught by the write-time validator).

### Locking shared append targets

Filing a synthesis note also touches the SHARED append targets `wiki/index.md`, `wiki/log.md`,
and `wiki/hot.md`. Every write to a shared target MUST take a per-note lock first (Layer-2,
`scripts/wiki-lock.sh`). Acquire in sorted-path order across all files you will touch, write,
then release. On `rc=75` (lock held), retry once after 2s, then log and skip the optional file
rather than block:

```sh
# Resolve VAULT_ROOT via: python -m agents.vault_config path
for f in "$VAULT_ROOT/wiki/index.md" "$VAULT_ROOT/wiki/log.md" "$VAULT_ROOT/wiki/hot.md"; do
  scripts/wiki-lock.sh acquire "$f" || { sleep 2; scripts/wiki-lock.sh acquire "$f" || exit 75; }
done
# ... write the synthesis note + append the index/log/hot rows ...
for f in "$VAULT_ROOT/wiki/index.md" "$VAULT_ROOT/wiki/log.md" "$VAULT_ROOT/wiki/hot.md"; do
  scripts/wiki-lock.sh release "$f"
done
```

The synthesis note file itself is new (no concurrent writer) and does not need a lock; the
shared index/log/hot appends do.

---

## How to think (10-principle mapping)

`challenge` is essentially OBSERVE-internal (principle 2) and ACCEPT (principle 8) turned into a
procedure: it surfaces the bias the user cannot see by confronting them with their own record.

- **OBSERVE (external):** read PURPOSE, hot.md, and the candidate counter-evidence in full.
- **OBSERVE (internal):** name your own bias toward agreeing with the user before you search.
- **LISTEN:** quote the user's position and their prior `objective/decision` verbatim.
- **THINK:** distinguish a real reversal from a bi-temporal supersession (timeline windows).
- **ACCEPT:** state the honest verdict even when it is "the vault supports you" or "I found
  nothing" - no inflation in either direction.

---

## Anti-patterns

- Being agreeable. The skill exists to disagree where the record supports disagreement.
- Claiming "nothing in the vault contradicts this" from a single query - search exhaustively
  first (anti-fabrication / search-completeness hard rules).
- Treating a bi-temporal supersession (a fact true then, changed later) as a contradiction.
- Fabricating a quote, date, or `[[Page]]` to strengthen the red-team.
- Writing markdown `[text](path)` links instead of `[[wikilinks]]`.
- Filing the note without taking the shared-target locks.
