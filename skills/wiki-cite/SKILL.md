---
name: wiki-cite
description: >
  Make every wiki claim cite its source and light-check that the cited source
  actually supports it. Ensures each external claim links its [[sources/X]] summary
  page (wikilinks only), then runs a LIGHT fact-check (Gemini Flash via the
  validation route) asking whether the cited raw source supports the claim.
  Supported claims keep their link; unsupported or unclear claims get a > [!gap]
  callout and are routed to wiki-reconcile. NOT the heavy grounding benchmark
  (that is Phase 6). Triggers on: "cite this", "add citations", "/wiki-cite",
  "check citations", "fact-check the wiki", "are these claims sourced", "ground
  the claims", "verify citations".
allowed-tools: Read Edit Grep Glob Bash
---

**Ownership: `wiki` agent.** If you are NOT the `wiki` subagent (e.g. the main orchestrator or another agent loaded this skill), DISPATCH it: call the Task tool with `subagent_type: wiki`, pass the user's full request, let the wiki agent run the steps below, and relay its result. Do NOT run the steps yourself - running as the `wiki` agent is what activates the RBAC/write-scope boundary. If you ARE the `wiki` agent, proceed.

# wiki-cite: cite + light fact-check wiki claims

Owner: **wiki**. Two jobs on a wiki page:

1. **Citation completeness** - every external/factual claim links its source page as a
   `[[sources/X]]` wikilink (per `references/ai-first-rules.md` Rules 5-6). Wikilinks
   ONLY; never markdown `[text](path)` links.
2. **Light fact-check** - for each cited claim, confirm the cited raw source actually
   supports it, using the `validation` role (Gemini Flash, via the LOCAL LiteLLM proxy).
   This is a cheap sanity gate, NOT the heavy grounding benchmark (Phase 6).

This skill follows `references/ai-first-rules.md` and `references/write-rules.md`. ASCII
only (no em-dashes, curly quotes, or Unicode math - see write-rules anti-patterns).

## When to run
- After `wiki-ingest` or `wiki-save` writes/updates a page (claims fresh, sources fresh).
- On demand when the user asks to verify or ground a page's claims.

## Workflow

### Step 0 - resolve the vault (never hard-code a path)
```bash
eval "$(python -m agents.vault_config env)"   # exports VAULT, VAULT_ROOT
```

### Step 1 - find claims and their cited sources
Read the target page. For each factual claim (a sentence asserting an external fact -
a number, a date, a named result, a quoted finding):
- Identify the `[[sources/X]]` wikilink that backs it. The convention: a claim cites the
  `wiki/sources/X.md` summary page, which in turn lists the immutable `[[raw/<type>/...]]`
  file(s) under its `sources:` frontmatter.
- If a claim has NO source link -> it is a citation GAP (Step 4, gap of type
  `missing-citation`). Do not invent a source.

### Step 2 - resolve each claim to source text
For a cited claim, load the underlying raw source text:
- Read `wiki/sources/X.md`; follow its `sources:` frontmatter to the `raw/<type>/...`
  file(s). The raw file is the ground truth (immutable).
- If only the summary page exists (no raw file yet), fact-check against the summary text
  and mark `confidence: medium`.

### Step 3 - light fact-check via the validation route
Run the helper once per claim. It does the source-excerpt match LOCALLY ($0) and only the
judge call hits the `validation` role:
```bash
python scripts/wiki_cite_check.py \
  --claim "<the exact claim sentence>" \
  --source-file "$VAULT_ROOT/raw/papers/<file>.md" \
  --page "wiki/concepts/<Page>.md"
```
Output is JSON: `verdict` (supported|unsupported|unclear), `route` (keep|gap|unreachable),
`confidence`, `excerpt` (what the judge saw), `reason`.

Routing:
| verdict | route | action |
|---------|-------|--------|
| supported | keep | leave the `[[sources/X]]` link as-is |
| unsupported | gap | add a `> [!gap]` callout, route to wiki-reconcile |
| unclear | gap | add a `> [!gap]` callout (lower confidence), route to wiki-reconcile |
| (judge unreachable) | unreachable | do NOT silently pass - flag for re-run; the helper degraded to a local heuristic, treat `unclear`/`unsupported` as a gap |

### Step 4 - write the gap callout (Edit, not rewrite)
For any `gap`-routed claim, inject a callout immediately AFTER the claim line. Do not
rewrite the page; targeted section injection only (write-rules: Section Injection):
```markdown
> [!gap] Unsupported citation (wiki-cite, YYYY-MM-DD)
> Claim: "<the claim>"
> Cited source: [[sources/X]] does not support this (verdict: <unsupported|unclear>).
> Routed to wiki-reconcile for adjudication.
```
For a `missing-citation` gap:
```markdown
> [!gap] Missing citation (wiki-cite, YYYY-MM-DD)
> Claim: "<the claim>" has no [[sources/X]] link. Find a source or mark the claim
> as inference (confidence: speculation) per ai-first-rules Rule 7.
```
Then hand the page to `wiki-reconcile` (it adjudicates contradictions / unsupported
claims and appends to the page timeline without overwriting).

### Step 5 - report
Summarize: claims checked, supported, flagged (gap), missing-citation. Point at the page.
Never assert "all claims are sourced" without having checked each one (ai-first-rules:
anti-fabrication / search-completeness).

## LLM routing (validation = Gemini Flash, the only paid route here)
- The excerpt match and routing logic are LOCAL and free (`scripts/wiki_cite_check.py`).
- The judge call goes to the LOCAL LiteLLM proxy at `http://localhost:${LITELLM_PORT:-4000}`
  with `{"model": "validation", ...}`; the proxy routes to Gemini Flash
  (`config/litellm.yaml` `validation` role) and auto-logs cost via
  `config/cost_callback.py`. The script reads `LITELLM_MASTER_KEY` from the env or `.env`;
  it never hard-codes the key.
- The ONLY network egress is to the local proxy. If the proxy is unreachable the helper
  degrades to a conservative local heuristic and marks `route=unreachable` (it never
  silently passes a claim). Keep the batch small - this is the metered route.

## Locking (shared-target writes)
wiki-cite edits a single page in place (the page is the contended target, not a global
append file). Take the Layer-2 per-note lock before the Edit, release after:
```bash
bash scripts/wiki-lock.sh acquire "wiki/concepts/<Page>.md" || { sleep 2; \
  bash scripts/wiki-lock.sh acquire "wiki/concepts/<Page>.md" || { echo "locked, skip"; exit 75; }; }
# ... Edit the page (inject gap callouts) ...
bash scripts/wiki-lock.sh release "wiki/concepts/<Page>.md"
```
If the same run also appends to a shared index/log target, lock those in sorted-path order.

## Boundaries
- Wiki-owned. Edits `wiki/` pages (claim callouts) only. Reads `raw/` and `wiki/sources/`.
- Does NOT fetch sources, browse the web, or write `objective/`, `research/`, or
  `meta/{health,cost}_report/`.
- Light check only. Heavy grounding (per-claim benchmark over the whole vault) is Phase 6.
