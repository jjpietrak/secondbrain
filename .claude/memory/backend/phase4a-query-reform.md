# Phase 4A step-2: Query Reformulation -- DONE 2026-06-25

## What was built

### scripts/web_query.py (NEW)
Crawl-time query reformulation for Second Brain Phase 4A step 2.

**Pinned public API:**
```
reformulate(
    targets: list[dict],
    *,
    purpose: str,
    vault_root: str,
    learned: dict | None = None,
    use_llm: bool = True,
    agent: str = "web",
    max_per_engine: int = 3,
) -> list[dict]
```

Each returned target dict adds:
- `queries`           -- flat list, union of all engines (back-compat)
- `queries_by_engine` -- {engine: [q, ...]} capped at max_per_engine
- `reformulation`     -- {method: "llm"|"fallback", old_queries: [...], rationale: str}

Internal functions:
- `_wiki_context(target, vault_root) -> str`  -- reads shows_up_in pages + gap missing; ~400 char cap
- `_deterministic_queries(target, purpose) -> dict[engine, list[str]]`  -- Design A fallback
- `_parse_llm_queries(text) -> dict | None`   -- defensive fenced-JSON parser
- `_invoke_llm(prompt, agent) -> str`         -- subprocess call to claude_agent.sh
- `_try_llm_reformulate(...) -> dict | None`  -- batched prompt build + invoke
- `_cap_engines(qbe, max_per_engine)`         -- caps per-engine query lists

### scripts/prompts/pipeline_prompts.py (MODIFIED)
Added `WEB_QUERY_REFORMULATION_PROMPT` between RESEARCH_SYNTHESIS_PROMPT and parsers section.
Placeholders: {purpose}, {max_per_engine}, {engine_rules}, {target_section}, {learned_terms_blurb}
Output contract: single fenced JSON block, no prose.

### .claude/web/web-config.json (MODIFIED)
Added:
```json
"query": {"reformulate": true, "use_llm": true, "max_queries_per_engine": 3}
```

### tests/test_web_query.py (NEW)
19 hermetic tests, 0 real LLM or network calls.

## Design decisions

### LLM invocation
`_invoke_llm` calls `claude_agent.sh --agent <agent> <prompt>` via subprocess.
The script writes a cost row to the ledger and returns only `.result` text.
All tests monkeypatch `web_query._invoke_llm` directly -- no real call.

### Fallback triggers
LLM fallback (per target) happens when:
1. `use_llm=False` (explicit opt-out)
2. `_invoke_llm` returns "" (no claude_agent.sh, timeout, or subprocess error)
3. `_parse_llm_queries` returns None (any JSON/shape error)
4. LLM returns partial JSON (only some target_ids): missing targets fall back individually

### Wiki-context delta
`_wiki_context` resolves `shows_up_in` wikilinks from the origin gap frontmatter
to actual vault files (`<vault_root>/wiki/concepts/...md`), reads first 150 chars
of body (after stripping frontmatter), appends gap.missing. Capped at 400 chars.
All file reads are graceful (missing = "").

### Design-C TODO location
The TODO block sits at module level (as a standalone block comment after the imports)
AND as an inline comment at the top of `reformulate()`. Both point to the module-level block.

## Test count
1042 tests green (was 1023, +19 new).

## Integration into web_crawl.py -- DONE 2026-06-25

### Changes made
- `scripts/web_crawl.py`:
  - `_import_web_query()` lazy import added.
  - `crawl()` gains `use_reformulate: bool | None = None` param (None = follow config).
  - After `build_plan` + `agent_learn`: if `_do_reformulate` is True, calls
    `web_query.reformulate(targets, ...)` and records one `trace.add("reformulate","target",...)`
    per target; wraps in try/except -> keeps original targets on error (graceful).
  - `_harvest_target`: reads `target.get("queries_by_engine", {})` and defines `_engine_queries(key)`
    helper that returns `queries_by_engine[key]` if non-empty, else falls back to flat `queries`.
    Paper-publisher engine uses `_engine_queries(paper_engine)`, forum uses `_engine_queries("forum")`,
    research-lane fallback uses `_engine_queries("arxiv")`.
  - `main()` adds `--no-reformulate` CLI flag -> passes `use_reformulate=False` to `crawl()`.
- `scripts/web_decision.py` `render_trace_markdown`:
  - Added `## Reformulate` section (rendered just before `## Harvest`); shows a table of
    target_id | method | old_queries (truncated) | engine_summary | rationale.
- `.claude/skills/web-scrape/SKILL.md`:
  - Added step `1b. REFORMULATE` between step 1 (DECISION) and step 2 (HARVEST).

### Test additions
`tests/test_web_crawl.py`: `TestQueryReformulation` class, 12 new hermetic tests.
All 1054 tests green. 0 real LLM calls in any test.

## LIVE DEMO 2026-06-25 (real LLM, real targets) — Design B confirmed
`web_query.py --vault Inference-Disagg --json` ran the REAL claude_agent.sh path (method=llm; ~$0
subscription). Dramatic improvement over static seed_queries — vague gap titles became specific,
delta-aware, per-engine queries:
- GAP-02: old "3-tier disaggregation -- no published system or model" -> arxiv "heterogeneous
  three-tier LLM disaggregation optical prefill GPU attention LPU FFN architecture"; semantic_scholar
  a full NL question; forum keyword form. rationale: "old queries were too vague."
- GAP-03 -> targets hard bytes/sec numbers at named model scales (DeepSeek-V3, Step-3).
- GAP-05 -> sharpens on output tensor format + translation cost + RDMA path for optical->GPU.
Confirms B fixes the "weakest link" (query formulation) end-to-end. claude_agent.sh IS authed in this
env (the LLM path is live; not just fallback). Commit 1db2d70.

## Phase 4A STATUS
Step 1 (learning layer) + step 2 (query reformulation, Design B) both DONE + demoed. The web
intelligence layer (the Phase-3 "weak" part) is now: learned routing/ranking + LLM-reformulated,
delta-aware queries — both legible (DecisionTrace `## Learning briefing` + `## Reformulate`).
NEXT options: Design-C selective-escalation TODO (reformulate only low-score targets); the ranking
revision (cross-encoder / LLM-judge / evidence-fit, beyond cosine); Phase 4B (nightly + budgeting).
