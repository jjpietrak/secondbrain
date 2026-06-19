# Fact: current Second Brain code inventory (v0.1 state, branch claude/v2-prototype)

Read 2026-06-19. Exact current state the v0.2 build extends.

## agents/ (Python, run via .venv/bin/python -m agents.<mod>)
- **ingest_index.py** SCHEMA_VERSION=2. Statuses: `pending|ingested|deleted`. Keyed by stable
  source id (precedence: explicit fm id > arXiv > DOI > YouTube > canonical URL > sha256 hash; arXiv
  vN stripped). Store `<vault>/meta/ingest_index.json` + mirror `meta/ingest_index.md`.
  Row fields: id, id_type, status, title, filename, url, source_type, content_hash, first_seen,
  ingested_at, source_page (+deleted_at). Functions: `_upsert` (rename-proof; deleted->pending on
  reappear), `pending` (new or hash-changed, deduped by id), `scan` (register new + sweep deletions:
  any id not on disk -> deleted), `mark` (-> ingested). Verbs: pending/status/scan/deleted/mark/id/
  get/report/list, `--vault`/`--json`/`--source-page`. SCAN_EXTS set; SKIP_DIRS={assets}.
  NOTE store is at `meta/ingest_index.json` but schema says `meta/ingest_index/` (folder) - divergence.
- **vault_config.py**: active-vault resolution (name arg > $VAULT > default_vault). Per-vault config
  `config/vaults/<name>/{vault,topics,budget}.yaml`; global `config/secondbrain.yaml`. Verbs: name/
  path/purpose/notebook/engine/topics-due/list/env. `vault_path()` = $VAULT_ROOT. NEVER hard-code.
- **cost_tracker.py**: JSONL ledger `logs/cost_ledger.jsonl` (rows tagged by vault). `record(action,
  role,provider,in,out,cost,source)`; report -> `meta/cost_report.md`; check -> exit 1 if today's
  paid spend >= budget.yaml daily_usd_cap. Agent-SDK calls recorded cost_usd=0 source=agent-sdk-credit.
  NO window_tokens / 5h-window fields yet (P4 adds). report writes single file; schema says
  `meta/cost_report/` folder - divergence.
- **vault_health.py**: OUR port (not OSB's). Checks orphans/dead-links/missing-fm(type,created,updated,
  sources)/stale(>90d)/stub(<50 chars). Writes `meta/health_report.md`. $0. Base for `wiki-health`.
- **nightly_run.sh**: multi-vault loop (VAULT=all -> every enabled); per-vault encapsulation, catch-up
  guard, DRY_RUN, logs/nightly-YYYYMMDD.log. Calls scripts/claude_agent.sh for $0 claude -p. P4 reshape.

## scripts/
- **claude_agent.sh**: loads CLAUDE_CODE_OAUTH_TOKEN from .env, `unset ANTHROPIC_API_KEY`, then
  `exec claude -p "$@"`. DISCARDS usage - the P0 blocker. Must capture `--output-format json`
  (.total_cost_usd, .usage) and call cost_tracker.record before re-emitting .result. Add --agent.
- **research/**: research.py, research_deep.py, notebooklm.py, notebooklm_sync.py, youtube_extract.py,
  x_read.py, x_pulse.py, podcast_extract.py, lib/ (P2/P5 sources).
- **prompts/pipeline_prompts.py**: ALREADY committed - the objective-flow GAP/ANALYSIS/SYNTHESIS
  prompt templates + parsers (P2). pdf_extract.py (PyMuPDF). architect_scan.py, mine_commit_decisions.py.
- obsidian_api.sh, setup_cron.sh.

## .claude/
- commands/ (v0.1 slash commands to migrate->skills): obsidian-ingest, obsidian-save, obsidian-query,
  obsidian-lint, obsidian-reconcile, obsidian-research[-deep], obsidian-notebooklm[-sync],
  obsidian-youtube, obsidian-architect, obsidian-capture, obsidian-daily, obsidian-task, obsidian-sync,
  cost-report. (daily/task/capture/sync are v0.1 personal-PKM holdovers - likely DROP, confirm.)
- agents/backend.md (only one so far). memory/backend/. settings.json (model claude-opus-4-8,
  additionalDirectories /mnt/c/Obsidian). settings.local.json. NO hooks yet, NO PreToolUse RBAC guards.
- skills/ has ONLY references/ (ai-first-rules, write-rules, vault-schema, bases, templates). No
  skills/<name>/SKILL.md yet - Phase 1 creates the first ones.

## Command->skill migration map (v0.1 command -> v0.2 skill)
obsidian-ingest->wiki-ingest; obsidian-save->wiki-save; obsidian-query->wiki-query; obsidian-lint->
wiki-lint(+wiki-health); obsidian-reconcile->wiki-reconcile; obsidian-research[-deep]->research[-deep]
(P2); obsidian-notebooklm*->nlm (P5); obsidian-youtube->youtube (P5); obsidian-architect->code agent
(backlog). DROP/confirm: daily, task, capture, sync, cost-report(->backend skill).
