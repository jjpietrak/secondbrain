# RELEASE — `v02-main` (team distribution)

## Branches
- **`claude/v2-prototype`** — ACTIVE DEVELOPMENT (backend agents). New work happens here.
- **`v02-main`** — the SHIPPED release for the team. Clean: no web scraping, no machine-specific
  paths, no secrets, no dev-agent memory.

## What's IN `v02-main`
- Agents: **wiki**, **research**, **backend**.
- Skills: `wiki-*` (init/ingest/save/defuddle/cite/retrieve/query/reconcile/synth/lint),
  `wiki-health`, `wiki-stats`, `obj-query/synth/reconcile`, `deep-synthesis`,
  `question-promote/solve`, `wiki-gaps`, `vault-health`, `vault-push`, `cost-report`,
  `think/challenge/connect`, `setup`.
- Core scripts (ingest_index, objectives, retrieval pipeline, locking, cost_tracker, …), the
  `config/vaults/example/` template, `scripts/setup.sh`, and the hermetic test suite (475 tests).

## What's NOT in `v02-main` (cut from v2-prototype)
- **All web scraping / web-fetching** — the web agent + crawl pipeline (`web_*.py`, `agent_learn`,
  `report_approve`, `.claude/web`, web skills), the OSB `obsidian-research(-deep)` commands,
  `scripts/research/` (+ `lib/sources`), youtube/x/notebooklm tools. **Under review.**
- `agent-learn` / `wiki-approve` (the web feedback loop).
- Dev artifacts: `plans/`, the maintainer's active vault config, `ursa/`, the LiteLLM service,
  `nightly_run.sh`, `setup_cron.sh`.
- Dev-agent memory — backend ships only curated **architecture** facts
  (`retrieval-pipeline`/`locking`/`rbac`/`cost-ledger`); wiki/research ship clean skeletons.

## Install (team)
See `README.md`. tl;dr: `git clone` → `uv sync` → `cp .env.example .env` →
`scripts/setup.sh --vault-path <abs-path> --name <vault-name>` → restart Claude Code.

## Dual-merge workflow (during the deployment phase)
Development continues on `v2-prototype`. A change that should ALSO ship goes to BOTH branches:
1. Build + commit on `v2-prototype`.
2. If it is shippable, web-free, and path-clean:
   `git checkout v02-main && git cherry-pick <sha>` (or merge the specific commits).
3. Re-run the `v02-main` guards before committing the cherry-pick:
   - `uv run pytest tests/` (or `pytest tests/`) — green.
   - Path audit over TRACKED files = 0:
     `git ls-files -z | xargs -0 grep -lE 'jpietrak|/mnt/c/Obsidian|<active-vault-name>'`
   - No web component, secret, or dev-memory introduced.
4. Preserve the clean invariants (no machine paths / secrets / dev memory / web).

## After v02 ships
Keep developing on `v2-prototype` (the web-pipeline revision, Phase 4B nightly + budgeting, etc.).
Promote selected, cleaned features to `v02-main` via the dual-merge above. Optionally tag `v0.2.0`
or promote `v02-main` to the repo default for the team.

## Provenance
`v02-main` cut from `v2-prototype` @ `c39c336`. Cleanup commits: `52c81e1` (strip web + dev artifacts
+ portable + example vault), `2d868d7` (setup flow + README/HOWTO + curated memory + clean CLAUDE.md),
and the pytest-dev-dep + this RELEASE.md.
