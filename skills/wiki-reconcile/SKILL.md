---
name: wiki-reconcile
description: >
  Find and resolve contradictions across wiki pages so the vault never disagrees with
  itself silently. For an evolving fact (a role, status, or number that changed over
  time) APPEND to the page's bi-temporal `timeline:` - never overwrite the current value.
  For a genuine contradiction, mark a `> [!warning]` callout on the conflicting pages,
  write a conflict note under wiki/synthesis/, and log it. Adjudication (which source
  wins, evolution vs contradiction) runs via the Gemini Flash `validation` route.
  Wikilinks only. Triggers on: "reconcile", "reconcile the vault", "wiki reconcile",
  "/wiki-reconcile", "find contradictions", "fix conflicts", "resolve contradictions",
  "does the vault disagree with itself", "is this stale".
allowed-tools: Read Edit Write Glob Grep Bash
---

# wiki-reconcile: keep the vault from disagreeing with itself

Owner: **wiki**. Adapted from the OSB `obsidian-reconcile` command. The vault must never
contain two pages that disagree without knowing they disagree. Every contradiction is
either (a) resolved as an evolution via a bi-temporal `timeline:` append, or (b) flagged
explicitly as an open conflict. This skill NEVER silently overwrites a fact.

## The two outcomes (this is the whole skill)

### 1. Evolution -> APPEND to `timeline:` (never overwrite)
When a fact changed over time (a person's role, an entity's status, a number that was
updated), this is GROWTH, not a contradiction. The bi-temporal rule: keep the CURRENT
value in the page's live frontmatter/body AND append the prior value to a `timeline:`
list with the date it was true and the source that asserted it. NEVER overwrite or delete
the old value - the timeline IS the history.

```yaml
# entity/source frontmatter - timeline is append-only, newest entry last
timeline:
  - { date: 2026-01-15, fact: "role = Staff Engineer", source: "[[sources/old-talk]]" }
  - { date: 2026-06-10, fact: "role = Director of Inference", source: "[[sources/new-blog]]" }
```

The live `role:` field reflects the newest timeline entry; older entries stay in
`timeline:` verbatim. If the page has no `timeline:` yet, create it and seed it with the
prior value before appending the new one (additive, never destructive).

### 2. Genuine contradiction -> warning callout + conflict note + log
When two pages assert mutually exclusive facts and it is NOT a simple time evolution
(e.g. two sources of equal recency disagree on a hard number), do all three:

1. Add a `> [!warning]` callout to BOTH conflicting pages, each linking the other and the
   conflict note:
   ```markdown
   > [!warning] Contradiction with [[other-page]]
   > This page states X (per [[sources/A]], 2026-03); [[other-page]] states Y
   > (per [[sources/B]], 2026-03). Unresolved. See [[synthesis/Conflict - Topic]].
   ```
2. Write a conflict note `wiki/synthesis/Conflict - <Topic>.md` (synthesis folder; this
   schema has no `wiki/decisions/`). Document both sides, the evidence for each, set
   `status: seed` and `synthesis_type: analysis`, and leave it for the user to decide.
3. Append a `wiki/log.md` entry: `## [YYYY-MM-DD] reconcile | C contradictions found,
   R resolved-as-evolution, F flagged`.

## Adjudication route (which source wins / evolution vs contradiction)

The judgement call - is this an evolution or a real contradiction, and which source is
more authoritative - is delegated to the **Gemini Flash `validation` route** via the
local LiteLLM proxy (the same metered, small, paid route `wiki-cite` uses). Give the
judge: the two claims, their dates, and their source types (peer-reviewed > article >
transcript > opinion). Heuristics it applies:

- **Which is newer?** (date comparison; newer usually wins for evolving facts)
- **Which is more authoritative?** (source-type ranking above)
- **Evolution or contradiction?** Someone changing their mind across time = evolution
  (-> timeline append). Two equally-recent sources disagreeing on a fact = contradiction
  (-> warning callout + conflict note).

If the proxy is unreachable, DO NOT silently resolve: fall back to flagging the pair as a
contradiction (outcome 2) and note `adjudication: unreachable` in the conflict note, so a
human/later run decides. Never overwrite a fact on an unreachable judge.

## Steps

1. Resolve the vault and read context:
   ```bash
   eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
   ```
   Read `wiki/hot.md` -> `wiki/index.md` first. An optional argument scopes the scan
   to one topic/entity; with no argument, scan all of `wiki/`.
2. Find candidate pairs (enumerate exhaustively, do not sample):
   - claims across `wiki/concepts/` that contradict each other,
   - `wiki/entities/` roles/status that conflict with newer sources,
   - `wiki/` pages referencing an old `[[sources/X]]` when a newer source on the same
     topic exists.
   (`wiki-lint` and `agents/vault_health.py` surface stale pages and dead links; this
   skill focuses on semantic contradiction. Run wiki-lint first if you want the structural
   picture.)
3. For each candidate pair, call the `validation` judge to classify evolution vs
   contradiction and pick the authoritative source.
4. Apply outcome 1 (timeline append) or outcome 2 (warning callout + conflict note + log)
   per the classification. Both are ADDITIVE - never delete a prior value.
5. Report: resolved-as-evolution (old fact -> new fact + why), flagged contradictions
   (needs human judgement), and any pages whose `timeline:` was seeded.

## Locking (shared-target writes)

Editing a single owned page (adding a callout, appending its `timeline:`) does not require
a lock, but the conflict note's index entry and the `wiki/log.md` append DO. Apply the
canonical snippet from [`skills/references/locking.md`](../references/locking.md): acquire
`wiki/index.md` and `wiki/log.md` in sorted-path order, write, release; on rc=75 retry
once after 2s then log and skip. When you touch two conflicting pages plus shared targets
in one operation, acquire ALL their locks in sorted-path order to avoid deadlock.

## Conventions

- Follow [`skills/references/ai-first-rules.md`](../references/ai-first-rules.md) and
  [`write-rules.md`](../references/write-rules.md). ASCII only - no em-dashes, curly
  quotes, or Unicode math. Use ` - ` and `>=` / `!=`.
- Every page/person/source named uses `[[wikilinks]]`. Sources preserved verbatim with
  their `[[sources/X]]` page (and inline URL/date where the claim is external).
- Anti-fabrication: never invent a date or a source to make a contradiction look settled.
  Mark unknowns `TBD`. The append-only `timeline:` means history is never lost.
- Cost: adjudication = Gemini Flash `validation` (metered, small); the file walk is $0.
