# Phase 0 (quick) + Phase 1 (Wiki agent + skills) - build-ready action plan

Backend agent draft, 2026-06-19. Companion to `elegant-juggling-sparrow.md`. Authoritative docs:
`docs/{requirements,agents,vault-schema,skills-description}.md` (frozen, read-only). This plan honors
all LOCKED decisions: quick-P0-first, schema-reconcile-dry-run-on-copy, wiki-cite = citation + light
fact-check, wikilinks-only, "entities" = only `wiki/entities/`, port CO wiki-ingest MINUS DragonScale,
wiki-retrieve = BM25 + ollama rerank local-only-by-default, LLM routing (embeddings/rerank->Ollama
bulk; ingest/synthesis->wiki agent credit pool; cross-checks->Gemini Flash validation), live demo over
Inference-Disagg. ASCII only.

> RESOLVED (grill-me 2026-06-19) - all section-8 risks settled; cleared to build (pending final go).
> This block SUPERSEDES any inline "DECISION / confirm / pending / OR reversed" markers below.
> 1. **concepts<->entities: NO conflict.** The matrix (doc lines 67-68) is conventional/correct
>    (`wiki/entities/`=concrete people/tools/companies/projects; `wiki/concepts/`=abstract ideas/
>    frameworks/theories/methods); matches the live vault. The "swap" notes at doc lines ~103 + ~317
>    are STALE leftovers for the USER to delete. Not a blocker.
> 2. **Frontmatter = MERGE live + frozen (SUPERSET).** Keep ALL live fields (`type`, `entity_type`,
>    `status: seed|developing|mature|evergreen`, `aliases`, `related`, `sources`, `key_claims`, `url`,
>    `confidence`, ...) AND ADD the frozen tags incl. bi-temporal `timeline:` (entities; sources where
>    useful) + `company`/`last_interaction`. Additive union, no field dropped. USER updates the
>    `docs/vault-schema.md` frontmatter section to the merged superset.
> 3. **meta/ = files for single artifacts** (`ingest_index.json`, `cost_report.md`,
>    `health_report.md`); FOLDERS only for accumulating series (`nightly_report/`, `meta/eval/`,
>    wiki-init dry-run reports).
> 4. **wiki/hot:** collapse live `wiki/hot/hot.md` -> `wiki/hot.md` (file) per schema.
> 5. **Drop `daily/` + `output/` now** (output/ returns with Phase 5); `research/*` deferred to the
>    Phase 2 reconcile.
> 6. **Query answers:** `wiki-query` returns inline in P1; query history -> `research/query/` in P2;
>    NO `wiki/questions/` folder.
> 7. **think/challenge/connect:** SHARED thinking tools, default-owned by `wiki`, also invokable by
>    `research`.
> 8. **Locking:** build + test BOTH layers in P0; wire Layer-2 per-note locks now; wire the Layer-1
>    lease into the nightly path in P4.
> 9. **v0.1 commands:** KEEP `obsidian-sync`; RETIRE `daily`/`task`/`capture`.

---

## 1. Quick Phase 0 - foundational infra

Four work items. Each: change + acceptance check. All ship with a hermetic test. No vault knowledge
edits. Repo writes only (`scripts/`, `agents/`, `.claude/`, `tests/`).

### P0.1 - `claude_agent.sh` usage-capture fix (the cost-accounting unblocker)
Today: `exec claude -p "$@"` discards usage. Change:
- Drop the bare `exec`. Run `claude -p --output-format json "$@"`, capture stdout to a var.
- Parse with the venv python (jq may be absent): read `.total_cost_usd`, `.usage.input_tokens`,
  `.usage.output_tokens`, `.usage.cache_*`, `.num_turns`, `.session_id`.
- Call `python -m agents.cost_tracker record --action "<--agent or 'agent-sdk'>" --role "" --provider
  anthropic --in <in> --out <out> --cost <total_cost_usd> --source agent-sdk-credit`.
- Re-emit ONLY `.result` to stdout (so callers see the same text as before). Preserve exit code.
- Add `--agent <id>` passthrough: strip it from the claude args, use it as the `--action`/role tag so
  per-agent spend is attributable (feeds P4 window budgeting).
- Guard: if `--output-format json` was ALSO requested by the caller, do not double-wrap (pass through
  raw). If parse fails, still emit `.result` best-effort and record a zero-cost row with a `parse_error`
  note (never silently drop a call).
- Acceptance: `scripts/claude_agent.sh --agent backend "say hi"` -> prints "hi"-ish text AND appends a
  `cost_ledger.jsonl` row with non-null input/output tokens + `source=agent-sdk-credit`. Hermetic test
  `tests/test_claude_agent_capture.sh` mocks `claude` with a fixture JSON on PATH, asserts the ledger
  row + that only `.result` reached stdout. (This is the hard prerequisite; build first.)

### P0.2 - Layer-2 `wiki-lock.sh` port + the two CO tests
- PORT `.co_reference/scripts/wiki-lock.sh` verbatim into `scripts/wiki-lock.sh` (age-based noclobber
  lockfiles `.vault-meta/locks/<sha1(path)>.lock`, STALE_AFTER_SEC=60, acquire/release/list/
  clear-stale/peek, path traversal/newline/symlink guards, `WIKI_LOCK_VAULT` test override, flock
  `with_meta_lock`). One adaptation: `VAULT_ROOT` resolves to `.vault-meta/` INSIDE the active vault
  (our locks live in the vault, not the repo) - default to `$VAULT_ROOT` if set, else the script-parent
  fallback. Keep the `WIKI_LOCK_VAULT` override so tests stay hermetic.
- PORT `tests/test_wiki_lock.sh` + `tests/test_concurrent_write.sh` verbatim (they self-sandbox via
  `WIKI_LOCK_VAULT` + mktemp; no path edits needed).
- Acceptance: `bash tests/test_wiki_lock.sh` and `bash tests/test_concurrent_write.sh` both print
  "All ... tests passed" (10 workers -> exactly 11 lines, every tag once, 0 orphan locks,
  clear-stale=0, no garbled lines).

### P0.3 - Layer-1 cross-host git lease (BUILD NEW) + new contention test
New `scripts/vault_lease.sh` (or python `agents/vault_lease.py` - prefer bash for parity with
wiki-lock and to run inside nightly). Lease file `<vault>/.vault-meta/vault-lease` (committed),
JSON `{holder, host, pid, acquired_at, expires_at}`. Verbs:
- `acquire --holder <id> --ttl <sec>`: `git pull --rebase --autostash`; read lease; if absent/expired
  -> write lease + `git add .vault-meta/vault-lease && git commit && git push`. A non-fast-forward
  push rejection = lost the race -> exit 75 (back off). TTL default 1800s.
- `release --holder <id>`: clear lease (only if we hold it), commit+push.
- `status`: print holder/expiry/now.
- POLICY (humans never block): the lease is consulted ONLY by automated writers. Verb `acquire
  --mode auto` defers (exit 75, harmless skip) when held; interactive/manual work NEVER calls acquire
  and never waits. Document this in the header and the nightly wiring.
- TTL reaping: an `acquire` that finds `now > expires_at` treats the lease as free and takes it
  (reaps a crashed holder).
- Acceptance: NEW `tests/test_vault_lease.sh` - two `git clone`s of a bare repo contend: clone A
  acquires, clone B `acquire --mode auto` gets exit 75 (mutual exclusion); after TTL=0 expiry B
  acquires (reaping); a simulated human write (no acquire) is never blocked. Hermetic (local bare repo
  under mktemp, no network).
> NOTE: Layer-1 is foundational but its only P1 consumer is the nightly/orchestrator path (P4). For
> P1 the per-note locks (P0.2) are what wiki-ingest/save use. Build P0.3 now (foundational) but its
> live wiring matures in P4. Confirm we still want it fully wired in P0 vs P4.

### P0.4 - wire lock acquisition into writer paths
- `claude_agent.sh` (or the nightly orchestrator that calls it) acquires the Layer-1 lease in `auto`
  mode before a headless batch; releases after. Interactive runs skip it (humans never block).
- Skills take Layer-2 per-note locks before touching the SHARED append targets every agent writes:
  `wiki/index.md`, `wiki/log.md`, `wiki/hot/hot.md` (live path), `meta/ingest_index/*`,
  (later) `objective/index`. Locking is expressed as a shared snippet in each SKILL.md (acquire ->
  write -> release; sorted-path order for multi-file; on rc=75 retry once after 2s then log+skip).
  This is documented procedure inside the SKILL.md bodies, not enforced by a hook (the PreToolUse RBAC
  hook is separate, P1/P2).
- Acceptance: a scripted 2-process append to `wiki/log.md` via the lock snippet loses no lines
  (covered by the concurrent-write test pattern, applied to the live append-target path shape).

---

## 2. Phase 1 - build order, per-skill spec

Stand up the `wiki` agent + its skill set + backend `wiki-health`/`wiki-stats`. Dependency order
below. Each skill = `skills/<name>/SKILL.md` (kepano frontmatter: `name`, `description` with trigger
phrases inline, optional `allowed-tools`) + optional `references/`. Every skill obeys
`skills/references/{ai-first-rules,write-rules}.md` and the locking snippet (P0.4) on shared targets.

### Wiki agent definition (prerequisite)
`.claude/agents/wiki.md` - `id: wiki`, `memory: .claude/memory/wiki/`, model + maxTurns per the
verifier template, `tools: Read, Edit, Write, Grep, Glob, Bash` (NO WebSearch/WebFetch). Body: Role
(librarian; owns ingest + wiki health; reasons over vault contents; no web), Task scope (writes
`wiki/`, `raw/<type>/` after approval, `meta/ingest_index/`; reads all; cannot write objective/ or
research/ or meta/{health,cost}_report), Memory protocol. Seed `.claude/memory/wiki/MEMORY.md`.
`PreToolUse` RBAC hook (new, no CO precedent) denies Edit/Write outside the wiki allowlist - shapes
from CO hooks.json; guard logic new. Acceptance: a Write attempt to `objective/` by the wiki agent is
denied by the hook.

### LLM routing (applies to every wiki skill)
- embeddings + rerank -> Ollama `bulk` ($0, `rerank.py`/contextual-prefix tier-3).
- ingest + synthesis reasoning -> the `wiki` agent on the Agent-SDK credit pool (`claude_agent.sh`).
- cross-checks (wiki-cite light fact-check, reconcile contradiction adjudication) -> Gemini Flash
  `validation` via the LiteLLM proxy (metered; the only paid route, kept small).

| # | Skill | Owner | Source | LLM route | Test it ships with |
|---|-------|-------|--------|-----------|--------------------|
| 1 | wiki-init | wiki | CO `skills/wiki` (scaffold) NEW reconcile | none (deterministic) | `tests/test_wiki_init.py` (dry-run-on-copy produces expected diff on a fixture vault) |
| 2 | wiki-ingest | wiki | CO `wiki-ingest` MINUS DragonScale | ingest=wiki credit pool | re-ingest fixture source -> N pages + index/log row; idempotent on re-run |
| 3 | wiki-save | wiki | CO `save` | wiki credit pool | conversation fixture -> note in right folder + index/log/hot |
| 4 | wiki-defuddle | wiki | CO `defuddle` | none (defuddle-cli) | mock defuddle on PATH -> clean md to raw/articles |
| 5 | wiki-cite | wiki | NEW (on write-rules) | fact-check=Gemini Flash validation | claim w/ + w/o source support -> linked/flagged correctly |
| 6 | wiki-retrieve | wiki | CO `bm25-index.py`+`retrieve.py`+`rerank.py`+`contextual-prefix.py` | embeddings/rerank=Ollama bulk | port `test_bm25_index.py`,`test_retrieve.py`,`test_contextual_prefix.py` |
| 7 | wiki-query | wiki | CO `wiki-query` | synthesis=wiki credit pool | quick/standard/deep on fixture; cites pages; exit-10 fallback |
| 8 | wiki-reconcile | wiki | OSB `obsidian-reconcile` | adjudication=Gemini Flash validation | contradiction fixture -> timeline append, no overwrite |
| 9 | wiki-synth | wiki | OSB `obsidian-synthesize` | synthesis=wiki credit pool | multi-source pattern fixture -> synthesis page |
| 10 | wiki-lint | wiki | CO `wiki-lint` MINUS DragonScale/tiling/canvas | none | fixture vault w/ orphan+dead-link -> flagged in report |
| 11 | wiki-health | backend | OUR `agents/vault_health.py` + OSB checks | none ($0) | port-merge test: code-fence-wrap + unfilled-template detected |
| 12 | wiki-stats | backend | NEW wrapper over OSB `vault_stats.py` | none ($0) | aggregates by type/status; writes stats block |
| 13 | think | wiki+research | CO `think` | n/a (reference loop) | n/a (doc skill) |
| 14 | challenge | wiki+research | OSB `obsidian-challenge` | reasoning=wiki/research pool | vault-history fixture -> pushback w/ citations |
| 15 | connect | wiki+research | OSB `obsidian-connect` | reasoning pool | two-domain fixture -> bridge note |

Per-skill ADAPTATIONS (RBAC/schema):
- ALL: remap CO `.raw/` -> our `raw/<type>/`; CO `wiki/comparisons` -> `wiki/synthesis/`; CO
  `wiki/questions` -> file answers to `wiki/synthesis/` (we have no questions/ folder - confirm);
  `wiki/hot.md` -> live path is `wiki/hot/hot.md` (folder) - confirm we keep folder or flatten to file
  per docs. Drop transport selection (filesystem only), drop wiki-mode routing, drop DragonScale
  address/manifest (ingest_index v3 replaces `.raw/.manifest.json`), drop canvas + tiling.
- wiki-ingest: consumes `pending`/`approved` from ingest_index (NOT `waiting_approval`/`rejected`);
  user-dropped raw files still auto-ingest; immutable raw; contradiction callouts on both pages;
  bi-temporal timeline appends (no overwrite of role/status). Records via `ingest_index mark`.
- wiki-cite: every wiki claim links its `[[sources/X]]` page AND a light fact-check that the raw
  source supports the claim (Gemini Flash). NOT the heavy grounding benchmark (P6). Flags unsupported
  claims with `> [!gap]` / routes to wiki-reconcile. Wikilinks ONLY (markdown `[text](path)` dropped).
- wiki-retrieve: BM25 always-on; contextual-prefix tier-3 synthetic (local) by default; egress tier-1
  only with `--allow-egress`; rerank localhost-only unless `--allow-remote-ollama`.
- wiki-health/wiki-stats: backend-owned per RBAC; write `meta/health_report/` (not `wiki/meta/`).

---

## 3. wiki-init dry-run-on-copy reconcile procedure (LOCKED)

`wiki-init` has two modes: scaffold (fresh vault) and reconcile (existing live vault). The reconcile
NEVER touches the live vault directly. Concrete steps:

1. **Snapshot copy.** `cp -a "$VAULT_ROOT" /tmp/wiki-init-dryrun-<ts>` (or `git worktree`/`git
   clone --local` of the vault's own git repo for a cheaper copy). Resolve `$VAULT_ROOT` via
   `agents.vault_config path`.
2. **Run reconcile on the COPY.** Apply the target schema to the copy: create missing folders
   (`objective/` is P2, skip in P1; create `raw/{opinions,code,notebooklm}`, `research/query`,
   `meta/{health_report,cost_report,ingest_index,nightly_report}/` as folders if we adopt
   folder-form), (re)write `_template.md` per repetitive item to the agreed frontmatter, relocate the
   stray root `stepfun-mfa.md` into `wiki/concepts|entities`, normalize `wiki/hot` (folder vs file
   decision), and leave `daily/`/`output/` decisions per user (section 8).
3. **Diff.** `diff -ruN "$VAULT_ROOT" /tmp/wiki-init-dryrun-<ts>` -> write a human-readable change
   report to `meta/health_report/wiki-init-dryrun-<ts>.md` (backend-owned folder). List: folders to
   create, templates to add/change, files to move, frontmatter migrations per page.
4. **Review gate.** Present the diff to the user. STOP. No live write until explicit approval. This is
   where the concepts<->entities and frontmatter-status conflicts get resolved.
5. **Apply.** On approval, re-run the same reconcile against the LIVE vault (idempotent; additive
   where possible). Commit on a clean boundary (Layer-1 lease + git). Frontmatter migration is
   additive and `.get()`-safe (never drops existing fields).

Acceptance: on a fixture vault, the dry-run produces the diff file and makes zero changes to the
source; a second apply run is a no-op (idempotent).

---

## 4. `ingest_index.py` v2 -> v3 spec (additive, `.get()`-safe)

Bump `SCHEMA_VERSION = 3`. All changes additive; `_load` already `.get()`-safe; add `_migrate(data)`
called inside `_load` after JSON parse: for each row, `row.setdefault(...)` the new fields, leave
status untouched (existing `ingested|pending|deleted` rows stay valid). Never rewrite existing values.

### New statuses
Add `waiting_approval` and `rejected` to the existing `pending|ingested|deleted`. Status set becomes:
`pending | ingested | deleted | waiting_approval | rejected`.
- `waiting_approval`: web/research-discovered candidate, NOT yet on disk in `raw/`. No file.
- `rejected`: user rejected (sticky). No file. `enqueue` of a rejected id short-circuits (no-op) so it
  is never re-proposed.

### New fields (per row, all optional / defaulted)
`relevance_score` (float|null), `rationale` (str), `discovered_by` (agent id), `objective_ids`
(list), `proposed_at` (iso|null), `rejected_at` (iso|null), `rejection_reason` (str).
Existing fields unchanged.

### New verbs
- `enqueue <id|url> [--title --rationale --score --discovered-by --objective-ids ...]`: create a
  `waiting_approval` row (no file). If id already `rejected` -> NO-OP (sticky). If already present in
  any other status -> update metadata only, do not regress status.
- `queue` (alias `waiting`): list `waiting_approval` rows (for the newsletter/approval surface).
- `approve <id>`: flip `waiting_approval` -> `pending` (the source will be fetched into `raw/<type>/`
  by the wiki agent, then `wiki-ingest` consumes `pending`).
- `reject <id> --reason <text>`: flip -> `rejected` (sticky), set `rejected_at` + `rejection_reason`.

### Deletion-sweep guard (the critical fix)
`scan()` marks ids absent from `raw/` as `deleted`. v3 MUST skip `waiting_approval` and `rejected`
rows in that sweep (they intentionally have no file on disk - sweeping them to `deleted` would corrupt
the queue). Guard: in the deletion loop, `if sid not in present_ids and row.get("status") not in
{"deleted","waiting_approval","rejected"}: -> deleted`.

### Store location
Keep the JSON store + md mirror; reconcile the `meta/ingest_index.json` (file) vs schema's
`meta/ingest_index/` (folder) divergence (section 8). `pending()` consumed by `wiki-ingest`.

Acceptance: `enqueue` an id -> `queue` lists it; `scan` does NOT flip it to `deleted`; `reject` makes
re-`enqueue` a no-op; `approve` -> `pending` shows in `pending`; a v2 store loads and `_migrate`s
without losing rows; render_md shows the new status marks.

---

## 5. Templates (`_template.md` per repetitive item)

Convention: keep CO-style `_template.md` INSIDE each wiki subfolder (already in use in the live vault),
not a central `templates/` dir. Set to (re)create, ALIGNED TO THE AGREED FRONTMATTER (pending the
section-8 frozen-vs-live decision):
- `wiki/entities/_template.md` - per the resolved entity schema (concrete people/tools/companies/
  projects per live convention OR the reversed frozen mapping - DECISION). Include bi-temporal
  `timeline:` if we adopt the frozen schema's bi-temporal rule.
- `wiki/concepts/_template.md` - abstract ideas/frameworks (or reversed).
- `wiki/synthesis/_template.md` - comparison/analysis/lit-review.
- `wiki/sources/_template.md` - one summary per ingested raw source; `sources:` lists the `[[raw/...]]`
  file(s); must carry the `[[sources/X]]` link target wiki-cite checks.
- `wiki/hot/_template.md` (or `wiki/hot.md`) - hot-cache format (Last Updated / Key Recent Facts /
  Recent Changes / Active Threads, <500 words).
- (P2) objective node templates; (P5) newsletter, export, eval fixture - out of P1 scope.
Every template carries the `## For future Claude` preamble + `ai-first: true` per ai-first-rules.

---

## 6. Acceptance: functional test + live demo

### Functional (hermetic, $0)
All P0 tests green; ingest_index v3 acceptance green; each skill's shipped test green. A combined
`tests/run_phase1.sh` runs them.

### Live demo over Inference-Disagg (guideline #2)
1. Pick one existing `raw/papers/*` (e.g. `Photons_to_Tokens.pdf` or `2507.19635v1.pdf`). Force a
   re-ingest through the NEW `wiki-ingest` -> show wiki pages created/updated + `[[sources/X]]`
   citations on claims (wiki-cite light fact-check passes/flags).
2. Run `wiki-query` on a real Iris-Tetra question (e.g. "How does co-packaged optics change the
   attention/FFN disaggregation tradeoff?") -> cited answer drawing on existing concept/entity pages.
3. Run `wiki-health` + `wiki-stats` -> reports to `meta/health_report/` (flags the stray root
   `stepfun-mfa.md`, any orphans/dead links, type/status aggregates).

### "Phase 1 done" looks like
- `.claude/agents/wiki.md` + `memory/wiki/` exist; PreToolUse RBAC denies out-of-scope wiki writes.
- All 12 build skills present as `skills/<name>/SKILL.md` with shipped tests passing.
- Retrieval pipeline provisioned (`.vault-meta/{chunks,bm25}` built) and `wiki-query` uses it with
  graceful exit-10 fallback.
- ingest_index v3 live (the live store migrated additively, no row loss).
- The live demo runs end-to-end on the real vault; `claude_agent.sh` records cost rows for every
  agent call. Backend memory updated with the file map + decisions.

---

## 7. Dependency graph / sequence (and parallelizable sub-agents)

```
P0.1 claude_agent.sh capture ──┐  (HARD PREREQ, do first)
P0.2 wiki-lock + tests ────────┤  (parallel with P0.1)
P0.3 vault-lease + test ───────┘  (parallel; depends on nothing)
P0.4 wire locks ──────────── needs P0.2 + P0.3

ingest_index v3 ──────────── independent (parallel with P0)

wiki.md agent + RBAC hook ── needs nothing; gate for all wiki skills

Phase 1 skills:
  wiki-init ──> (scaffold/reconcile; gates the rest if schema changes)
  wiki-retrieve pipeline (bm25/rerank/contextual/retrieve + setup) ── independent, port early
  wiki-ingest ──> needs ingest_index v3 + wiki-init schema + locks
  wiki-defuddle ── independent (tool only)
  wiki-cite ──> needs wiki-ingest output (sources pages) + Gemini validation route
  wiki-query ──> needs wiki-retrieve
  wiki-save ── independent (parallel)
  wiki-reconcile / wiki-synth ──> need wiki pages to operate on
  wiki-lint ── independent
  wiki-health / wiki-stats (backend) ── independent (parallel)
  think / challenge / connect ── independent doc/reference skills
```

Parallel backend sub-agents (guideline #4): (A) P0.1 capture-fix; (B) P0.2+P0.3 locking+lease+tests;
(C) ingest_index v3; (D) wiki-retrieve pipeline port + its 3 tests; (E) wiki-health/wiki-stats
(backend-owned, no dependency on wiki agent). After the wiki.md agent + wiki-init land, fan out the
remaining wiki skills (ingest, save, defuddle, cite, query, reconcile, synth, lint) as separate
sub-agents that report back. Backend integrates + owns memory.

---

## 8. Open risks / decisions - ALL RESOLVED 2026-06-19 (see the RESOLVED block at the top)

The list below is kept for traceability; every item is resolved in the top RESOLVED block.
1. concepts<->entities reversal (frozen doc vs live vault + both references) - HARD.
2. Frozen vs live frontmatter (status enum, bi-temporal timeline, `type:` vs tag-typing,
   `url` vs `source_url`) - which wins; docs authoritative but live richer + in use.
3. meta/ as files vs folders (ingest_index/cost_report/health_report).
4. wiki/hot folder vs `wiki/hot.md` file.
5. `daily/` + `output/` + `research/{afd-simulator,daily,notebooklm,youtube}` not in frozen schema.
6. No `wiki/questions/` folder - where wiki-query/save file answers (synthesis/?).
7. challenge/connect/think owner: docs=research, plan-map=wiki -> propose shared.
8. Layer-1 lease fully wired in P0 vs deferred to P4.
9. Drop v0.1 personal-PKM commands (daily/task/capture/sync)?
