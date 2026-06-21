---
name: wiki-gaps
description: >
  Bottom-up gap analysis over wiki/. Harvests every page's "## Open Questions" section
  and "## For future Claude" preamble as top-priority signal, then scans the full wiki
  state to surface knowledge gaps, thin coverage, and stale claims. Writes a structured
  Gaps report to wiki/gaps.md (wiki-owned, readable by the research agent and the P3 web
  agent). Triggers on: "find gaps", "wiki gaps", "gap analysis", "what is missing",
  "what should we research next", "/wiki-gaps", "surface gaps", "what does the wiki
  not know", "open questions harvest".
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
> The output (wiki/gaps.md) feeds the research agent's obj-synth and the P3 web agent's
> DECISION block. Keep it clean, ranked, and machine-readable. ai-first: true

- **Owner:** wiki agent (the librarian). Reads all of wiki/, reads objective/ (READ only).
  Writes only `wiki/gaps.md` (locked write - see Locking below).
- **LLM route:** gap reasoning runs on the **wiki agent's Agent-SDK credit pool**
  (`scripts/claude_agent.sh`, $0 marginal). The deterministic gather step (sections
  below) is pure Python via `scripts/wiki_gaps_gather.py`. The prompt fill and LLM
  reasoning are the wiki agent's own job.
- **Obsidian syntax:** wikilinks `[[wiki/page]]`, YAML frontmatter. ASCII only.
- **No web.** No WebSearch / WebFetch.

---

## What this skill produces

`wiki/gaps.md` - a living Gaps report. Structure per `WIKI_GAP_PROMPT` output (see
`scripts/prompts/pipeline_prompts.py`):

```
## Open-Question Harvest (TOP PRIORITY)
## Coverage Map
## Knowledge Gaps
### <gap title>
...
## Stale / Unverified
## Self-contained (no external source needed)
READY
```

The file ends with the `READY` terminator (machine-checked by the test and by downstream
consumers). It is date-stamped in its frontmatter and appended-to (not overwritten) by
`wiki_gaps_gather.py` via the locking snippet.

---

## Steps

### Step 0 - Resolve the vault root and prepare

```bash
eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
PY=".venv/bin/python"
```

### Step 1 - Gather wiki state (deterministic, via the helper)

Run the gather helper. It walks `wiki/` (skipping `_template.md`, `index.md`, `hot.md`,
`log.md`, `gaps.md`), extracts `## For future Claude` preambles and `## Open Questions`
sections verbatim from each page, and assembles the `{wiki_state}` block and other
inputs needed by `WIKI_GAP_PROMPT`. It also reads `objective/purpose/PURPOSE.md` (if
present) for `{purpose}` and `objective/topic/` files for `{active_topics}`.

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

If validation fails, log the issue and stop (do not write a broken gaps file).

### Step 5 - Write to wiki/gaps.md (LOCKED)

`wiki/gaps.md` is wiki-owned; it is written by this skill only, but lock it defensively
because nightly runs may trigger it concurrently with other wiki writes. Follow the
canonical locking snippet from `skills/references/locking.md`:

```bash
LOCK="scripts/wiki-lock.sh"
TARGET="wiki/gaps.md"

acquire_or_skip() {
  local path="$1"
  if bash "$LOCK" acquire "$path"; then return 0; fi
  sleep 2
  if bash "$LOCK" acquire "$path"; then return 0; fi
  echo "wiki-lock: $path still held after retry -> skipping wiki/gaps.md write" >&2
  return 1
}

if acquire_or_skip "$TARGET"; then
  # Write (overwrite - gaps.md is regenerated each run, not appended).
  cat > "$VAULT_ROOT/$TARGET" <<'GAPSEOF'
<frontmatter + gaps content>
GAPSEOF
  bash "$LOCK" release "$TARGET"
fi
```

The write is a FULL OVERWRITE of `wiki/gaps.md` (not an append) - each run regenerates
a fresh gaps report. The frontmatter carries `updated: YYYY-MM-DD` so readers know when
it was last run.

### Step 6 - Append a log entry

After writing `wiki/gaps.md`, append a brief entry to `wiki/log.md` (locked - this is
a shared append target):

```markdown
## [YYYY-MM-DD] wiki-gaps | N gaps surfaced, M open-question items harvested
- Report: [[wiki/gaps]]
- Harvest count: M items from "## Open Questions" sections
- Gap count: N knowledge gaps identified
```

Acquire `wiki/log.md` AFTER releasing `wiki/gaps.md` (single target, no sorted-order
complexity needed here since we do NOT hold both simultaneously).

---

## wiki/gaps.md frontmatter

```yaml
---
type: gaps_report
generated_by: wiki
updated: YYYY-MM-DD
vault: <vault name>
ai-first: true
---
```

Body: the structured output of `WIKI_GAP_PROMPT` verbatim (starting with
`## Open-Question Harvest`, ending with `READY`).

---

## What the gather helper excludes

`scripts/wiki_gaps_gather.py` skips:
- `wiki/_template*.md` files (templates, not knowledge)
- `wiki/index.md`, `wiki/hot.md`, `wiki/log.md`, `wiki/gaps.md` (infrastructure files)
- Any file under `wiki/` whose `type` frontmatter field is `index`, `hot`, `log`, or
  `gaps_report` (defensive - avoids self-referential gap harvesting)

It INCLUDES all files under `wiki/entities/`, `wiki/concepts/`, `wiki/sources/`,
`wiki/synthesis/` and any other subfolders (exhaustive coverage of knowledge content).

---

## Open-Question Harvest semantics

The `## Open Questions` section of a wiki page records items that prior ingestion or
synthesis identified as uncertain, stale, unverified, or unknown. Each item is a signal
that someone (a prior Claude instance, the user, or an ingest pass) explicitly flagged
this as a gap. The harvest lifts those items verbatim (not paraphrased) into the
`## Open-Question Harvest` section of the gaps report, citing the `[[wiki/page]]` they
came from. Items from this harvest are automatically `priority: high` unless already
answered (cross-checked against the rest of the wiki page content).

Similarly, the `## For future Claude` preamble of each page may contain staleness
caveats (`"the benchmark data here may be outdated"`, `"this entity's status was
unclear as of YYYY-MM"`). Those caveats are also included in the harvest as
`Stale / Unverified` candidates.

---

## What not to do

- Do NOT write to `objective/`, `research/`, `meta/`, or any path outside `wiki/`.
- Do NOT fetch web content (no WebSearch / WebFetch).
- Do NOT read `wiki/gaps.md` itself as input to the gather step (avoid self-reference).
- Do NOT fabricate gaps - every item in the output must cite a `[[wikilink]]` or be
  explicitly marked as "no pages reference this" after an exhaustive search.
- Do NOT append to `wiki/gaps.md` - each run is a full overwrite (the report is
  regenerated fresh).
- Do NOT skip the `READY` terminator - downstream consumers check for it.

---

## Locking reference

Follow `skills/references/locking.md` exactly. `wiki/gaps.md` is a single write target
(not an append target) but still lock it to protect against concurrent nightly runs.
`wiki/log.md` is a shared append target; always lock it before appending.
