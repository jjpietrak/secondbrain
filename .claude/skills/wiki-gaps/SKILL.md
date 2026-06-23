---
name: wiki-gaps
description: >
  Bottom-up gap analysis over wiki/. Harvests every page's "## Open Questions" section
  and "## For future Claude" preamble as top-priority signal, then scans the full wiki
  state to surface knowledge gaps, thin coverage, and stale claims. Writes per-gap files
  to wiki/gap/GAP-NN-<slug>.md and an overview index to wiki/gap/index.md (wiki-owned,
  readable by the research agent and the P3 web agent). Triggers on: "find gaps",
  "wiki gaps", "gap analysis", "what is missing", "what should we research next",
  "/wiki-gaps", "surface gaps", "what does the wiki not know", "open questions harvest".
allowed-tools: Read Edit Write Grep Glob Bash
---

**Ownership: `wiki` agent.** If you are NOT the `wiki` subagent (e.g. the main orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: wiki`, pass the user's full request, let the wiki agent run the steps below, and relay its result. Do NOT run the steps yourself - running as the `wiki` agent is what activates the RBAC/write-scope boundary. If you ARE the `wiki` agent, proceed.

# wiki-gaps: bottom-up gap analysis

> ## For future Claude
> This skill exists to answer: "What external knowledge would most improve this wiki?"
> It reads bottom-up - from what IS in the wiki outward - and the strongest signal is
> always what prior reasoning ALREADY flagged as uncertain. That signal lives in every
> page's "## Open Questions" section and "## For future Claude" preamble. Harvest those
> verbatim first; infer additional gaps only after exhausting that primary signal.
> The output (wiki/gap/index.md + per-gap files) feeds the research agent's obj-synth
> and the P3 web agent's DECISION block. Keep it clean, ranked, and machine-readable.
> ai-first: true

- **Owner:** wiki agent (the librarian). Reads all of wiki/, reads objective/ (READ only).
  Writes only under `wiki/gap/` (locked write - see Locking below).
- **LLM route:** gap reasoning runs on the **wiki agent's Agent-SDK credit pool**
  (`scripts/claude_agent.sh`, $0 marginal). The deterministic gather step (sections
  below) is pure Python via `scripts/wiki_gaps_gather.py`. The prompt fill and LLM
  reasoning are the wiki agent's own job.
- **Obsidian syntax:** wikilinks `[[wiki/page]]`, YAML frontmatter. ASCII only.
- **No web.** No WebSearch / WebFetch.

---

## What this skill produces

`wiki/gap/GAP-NN-<slug>.md` -- one file per knowledge gap, machine-parsed by
`web_decision.parse_gaps`. Structure per the pinned schema in `scripts/wiki_gaps_split.py`:

```
---
type: gap
id: GAP-NN
title: "..."
status: open
topics: [T-XXXX]
fillable_by: [arxiv, web]
priority: high|medium|low
shows_up_in: ["[[wiki/page]]"]
created: YYYY-MM-DD
updated: YYYY-MM-DD
written_by: wiki
---
## Missing
<missing text>

## Why
<priority justification>
```

`wiki/gap/index.md` -- overview index (type: gaps_index). Contains:
- `## Coverage Map` -- per-topic coverage summary
- `## Stale / Unverified` -- claims needing re-verification
- `## Self-contained` -- gaps solvable without external sources
- `## Open-Question Harvest (TOP PRIORITY)` -- verbatim open-question items
- `## Gap files` -- table of all gap files with priority + topics

Template for new gap files: `scripts/templates/gap_template.md` (copied to
`wiki/gap/_template.md` on first run -- see Step 5b below).

The `READY` terminator is still required in the LLM output for validation
(machine-checked by `validate_gaps_output`), but it does NOT appear in any
written file (the splitter strips it before rendering).

---

## Steps

### Step 0 - Resolve the vault root and prepare

```bash
eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
PY=".venv/bin/python"
```

### Step 1 - Gather wiki state (deterministic, via the helper)

Run the gather helper. It walks `wiki/` (skipping `_template.md`, `index.md`, `hot.md`,
`log.md`, `gaps.md`, and any `wiki/gap/` files), extracts `## For future Claude` preambles
and `## Open Questions` sections verbatim from each page, and assembles the `{wiki_state}`
block and other inputs needed by `WIKI_GAP_PROMPT`. It also reads
`objective/purpose/PURPOSE.md` (if present) for `{purpose}` and `objective/topic/` files
for `{active_topics}`.

```bash
$PY scripts/wiki_gaps_gather.py "$VAULT_ROOT" --output-json
```

The helper writes a JSON payload to stdout with keys:
- `purpose`: str (vault purpose text, or "not yet defined" if no PURPOSE.md)
- `today`: str (ISO date, e.g. "2026-06-21")
- `active_topics`: str (formatted list from objective/topic/, or "none")
- `wiki_state`: str (formatted wiki state block; each page includes its
  `## For future Claude` preamble and `## Open Questions` section verbatim where present)

If `objective/purpose/PURPOSE.md` is absent, fall back to:

```bash
$PY -m agents.vault_config purpose   # vault PURPOSE one-liner from vault.yaml
```

### Step 2 - Fill the gap prompt

From the JSON payload, fill `WIKI_GAP_PROMPT` from
`scripts/prompts/pipeline_prompts.py`:

```python
from scripts.prompts.pipeline_prompts import WIKI_GAP_PROMPT
filled = WIKI_GAP_PROMPT.format(
    purpose=payload["purpose"],
    today=payload["today"],
    active_topics=payload["active_topics"],
    wiki_state=payload["wiki_state"],
)
```

### Step 3 - Reason over the gaps (LLM call, wiki agent credit pool)

Send the filled prompt to the wiki agent model. The prompt instructs the LLM to:
1. Read the `## Open Questions` sections first (TOP-PRIORITY SIGNAL, harvested verbatim).
2. Read each `## For future Claude` preamble for staleness/uncertainty caveats.
3. Survey coverage per active topic.
4. Surface knowledge gaps, stale claims, and self-contained synthesis tasks.

The output is the structured Gaps text ending with `READY`.

### Step 4 - Parse and validate the output

Minimal validation before writing:
- The output contains `## Open-Question Harvest` (the top-priority section is present).
- The output ends with `READY` on its own line.
- No fabricated content (the LLM is instructed to cite `[[wikilinks]]` for every item).

If validation fails, log the issue and stop (do not write any gap files).

```python
from scripts.wiki_gaps_fill import validate_gaps_output
ok, msg = validate_gaps_output(gaps_text)
if not ok:
    # log and stop
    raise SystemExit(f"Gaps output validation failed: {msg}")
```

### Step 5 - Write per-gap files to wiki/gap/ (LOCKED)

The `wiki/gap/` directory is wiki-owned; lock the index file defensively because
nightly runs may trigger this skill concurrently with other wiki writes. Follow the
canonical locking snippet from `skills/references/locking.md`.

Lock the index file first, then write all gap files (they are independent; the index
is the shared coordination point):

```bash
LOCK="scripts/wiki-lock.sh"
INDEX="wiki/gap/index.md"

acquire_or_skip() {
  local path="$1"
  if bash "$LOCK" acquire "$path"; then return 0; fi
  sleep 2
  if bash "$LOCK" acquire "$path"; then return 0; fi
  echo "wiki-lock: $path still held after retry -> skipping wiki/gap write" >&2
  return 1
}

if acquire_or_skip "$INDEX"; then
  # Pipe the validated gaps_text into the splitter (apply=True writes all files).
  echo "$GAPS_TEXT" | $PY scripts/wiki_gaps_split.py \
    --vault "$VAULT_ROOT" \
    --apply \
    --stdin
  bash "$LOCK" release "$INDEX"
fi
```

The splitter:
- Creates `wiki/gap/` if it does not exist.
- Writes one `wiki/gap/GAP-NN-<slug>.md` per gap (preserves `created:` on re-runs).
- Writes `wiki/gap/index.md`.
- Does NOT delete files it did not generate.

#### Step 5b - First run / migration: drop the template

On the first run (or whenever `wiki/gap/_template.md` is absent), copy the canonical
template from the code repo:

```bash
TEMPLATE_SRC="scripts/templates/gap_template.md"
TEMPLATE_DST="$VAULT_ROOT/wiki/gap/_template.md"
if [ ! -f "$TEMPLATE_DST" ]; then
  cp "$TEMPLATE_SRC" "$TEMPLATE_DST"
fi
```

### Step 6 - Append a log entry

After writing the gap files, append a brief entry to `wiki/log.md` (locked - shared
append target):

```markdown
## [YYYY-MM-DD] wiki-gaps | N gaps surfaced, M open-question items harvested
- Report: [[wiki/gap/index]]
- Harvest count: M items from "## Open Questions" sections
- Gap count: N knowledge gaps written to wiki/gap/
```

Acquire `wiki/log.md` AFTER releasing the index lock (single target, no sorted-order
complexity needed since we do NOT hold both simultaneously).

---

## wiki/gap/ frontmatter

Per-gap files (`wiki/gap/GAP-NN-<slug>.md`):
```yaml
---
type: gap
id: GAP-NN
title: "..."
status: open
topics: [T-XXXX]
fillable_by: [arxiv]
priority: medium
shows_up_in: ["[[wiki/page]]"]
created: YYYY-MM-DD
updated: YYYY-MM-DD
written_by: wiki
---
```

Index file (`wiki/gap/index.md`):
```yaml
---
type: gaps_index
written_by: wiki
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

---

## What the gather helper excludes

`scripts/wiki_gaps_gather.py` skips:
- `wiki/_template*.md` files (templates, not knowledge)
- `wiki/index.md`, `wiki/hot.md`, `wiki/log.md`, `wiki/gaps.md` (infrastructure files)
- `wiki/gap/` subtree (avoids self-referential gap harvesting)
- Any file under `wiki/` whose `type` frontmatter field is in SKIP_TYPES

It INCLUDES all files under `wiki/entities/`, `wiki/concepts/`, `wiki/sources/`,
`wiki/synthesis/` and any other subfolders (exhaustive coverage of knowledge content).

---

## Open-Question Harvest semantics

The `## Open Questions` section of a wiki page records items that prior ingestion or
synthesis identified as uncertain, stale, unverified, or unknown. Each item is a signal
that someone (a prior Claude instance, the user, or an ingest pass) explicitly flagged
this as a gap. The harvest lifts those items verbatim (not paraphrased) into the
`## Open-Question Harvest` section of the gaps output, citing the `[[wiki/page]]` they
came from. Items from this harvest are automatically `priority: high` unless already
answered.

Similarly, the `## For future Claude` preamble of each page may contain staleness
caveats. Those caveats are also included in the harvest as `Stale / Unverified`
candidates.

---

## What not to do

- Do NOT write to `objective/`, `research/`, `meta/`, or any path outside `wiki/gap/`.
- Do NOT fetch web content (no WebSearch / WebFetch).
- Do NOT read `wiki/gap/*.md` files as input to the gather step (avoid self-reference).
- Do NOT fabricate gaps - every item in the output must cite a `[[wikilink]]` or be
  explicitly marked as "no pages reference this" after an exhaustive search.
- Do NOT skip the `READY` terminator in the LLM output - it is validated before writing.
- Do NOT write a monolithic `wiki/gaps.md` - that file is superseded by the per-gap
  structure under `wiki/gap/`.

---

## Locking reference

Follow `skills/references/locking.md` exactly. `wiki/gap/index.md` is the coordination
lock target (lock it before writing any gap files in a run; release after all writes are
done). `wiki/log.md` is a shared append target; always lock it before appending.
