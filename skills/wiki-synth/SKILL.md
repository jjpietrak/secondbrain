---
name: wiki-synth
description: >
  Automatic synthesis. Scans the wiki for cross-source patterns that no page names yet
  and writes a synthesis page to wiki/synthesis/ (synthesis_type: comparison | analysis |
  literature-review). Finds concepts that recur across 2+ unrelated sources, entities
  that co-occur without a connection page, concepts that evolved 3+ times, and orphan
  pages whose ideas should be linked. Synthesis reasoning runs on the wiki agent credit
  pool. Wikilinks only. Triggers on: "synthesize", "wiki synth", "/wiki-synth",
  "auto-synthesis", "find patterns", "find unnamed patterns", "make a synthesis note",
  "what connects these", "literature review of the vault".
allowed-tools: Read Edit Write Glob Grep Bash
---

# wiki-synth: write the patterns the vault has not named yet

Owner: **wiki**. Adapted from the OSB `obsidian-synthesize` command. The vault should
generate its own insights, not only when asked. This skill finds cross-source patterns
and writes a `wiki/synthesis/` page for each, then links it back from the pages it draws
on. Synthesis REASONING (deciding the pattern is real and worth a page) runs on the
**wiki agent credit pool** via the Agent SDK ($0 marginal) - not a paid route.

## Where it differs from the OSB original (adaptations)

- Output path is `wiki/synthesis/` (this schema has no `wiki/concepts/Synthesis - X.md`
  convention and no `wiki/comparisons/`).
- Frontmatter follows the merged `wiki/synthesis/_template.md`: `type: synthesis`,
  `synthesis_type: comparison | analysis | literature-review`, `subjects`, `dimensions`,
  `verdict`, `status`, `related`, `sources`. Carry `ai-first: true` and the
  `## For future Claude` preamble.
- Citations are `[[wikilinks]]` ONLY (no markdown `[text](path)` links). Sources list the
  `[[sources/X]]` summary pages (and the `[[raw/...]]` files they point at where useful).
- No daily-note update and no Dataview/canvas (not in this schema).

## What it scans for

Read `wiki/hot/hot.md` -> `wiki/index.md` -> the recent `wiki/log.md` tail first (context
discipline), then look for:

1. **Cross-source recurrence** - a concept appearing in 2+ UNRELATED sources (e.g. a
   paper AND a transcript AND an article). Recently-ingested `raw/` items are prime
   candidates; cross-check against existing `wiki/sources/` pages.
2. **Entity convergence** - two `wiki/entities/` that co-occur across multiple pages but
   have no connection page explaining the relationship.
3. **Concept evolution** - a `wiki/concepts/` page updated 3+ times; write a timeline of
   how the thinking changed (this overlaps the bi-temporal rule - see wiki-reconcile).
4. **Orphan rescue** - `wiki/` pages with no inbound links whose ideas SHOULD link to
   existing pages; create the missing links and explain why.

## Choosing the synthesis_type

| `synthesis_type` | Use when |
|------------------|----------|
| `comparison` | weighing 2+ subjects across dimensions (use the template's Comparison table + Verdict) |
| `analysis` | one phenomenon examined in depth from several sources |
| `literature-review` | surveying what a body of sources collectively says about a topic |

## Steps

1. Resolve the vault and read context:
   ```bash
   eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
   ```
   Read `wiki/hot/hot.md`, `wiki/index.md`, and the last ~20 `wiki/log.md` entries.
2. Enumerate candidates exhaustively (every `wiki/**/*.md`, recent `raw/`), do not
   sample. For each candidate pattern, decide on the wiki credit pool whether it is a
   genuine, unnamed pattern (anti-fabrication: a thin coincidence is not a synthesis).
3. For each accepted pattern, create `wiki/synthesis/<slug>.md` FROM the template:
   - Fill the frontmatter (`synthesis_type`, `subjects` as `[[wikilinks]]`, `dimensions`,
     `verdict`, `status: seed`, `sources` as `[[sources/X]]`).
   - Write the `## For future Claude` preamble (what pattern, which pages, what it means,
     suggested action). Body: the comparison table / analysis / review, with every claim
     carrying a recency marker and its `[[sources/X]]` citation.
4. **Link back**: add a `[[<synthesis page>]]` wikilink from each source page the
   synthesis draws on (the propagation rule - never create a synthesis in isolation).
5. Update the shared targets under a lock (see Locking): add the page to `wiki/index.md`
   and append a `wiki/log.md` entry `## [YYYY-MM-DD] synth | N pages created, M orphans
   rescued`.
6. Report: pages created (with their `synthesis_type`), orphans rescued, connections
   found.

## Locking (shared-target writes)

The new synthesis page itself is a single owned file (no lock needed). But step 4/5 touch
the SHARED append targets `wiki/index.md`, `wiki/log.md`, and the source pages you link
back from. Apply the canonical snippet from
[`skills/references/locking.md`](../references/locking.md): acquire in sorted-path order,
write, release; on rc=75 retry once after 2s then log and skip. Example for the index/log
pair:

```bash
LOCK="scripts/wiki-lock.sh"
PATHS=$(printf '%s\n' wiki/index.md wiki/log.md | sort)
held=()
for p in $PATHS; do
  if bash "$LOCK" acquire "$p" || { sleep 2; bash "$LOCK" acquire "$p"; }; then
    held+=("$p")
  else
    echo "wiki-lock: $p held -> skipping synth index/log update" >&2; break
  fi
done
# ... write index.md + log.md while held ...
for p in "${held[@]}"; do bash "$LOCK" release "$p"; done
```

## Conventions

- Follow [`skills/references/ai-first-rules.md`](../references/ai-first-rules.md) and
  [`write-rules.md`](../references/write-rules.md). ASCII only - no em-dashes, curly
  quotes, or Unicode math. Use ` - ` for dashes and `>=` / `!=` for operators.
- Anti-fabrication: never invent a pattern, a source, or a date. Search exhaustively
  before claiming a connection page is absent. Mark inferences with a confidence level.
- Cost: synthesis reasoning = wiki agent credit pool ($0 marginal). No paid route.
