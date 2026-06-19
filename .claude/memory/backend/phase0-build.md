# Fact: Quick Phase 0 build (foundational infra) - SHIPPED 2026-06-19

Branch `claude/v2-prototype`. Four items + tests, all green. Repo writes only; one vault
infra dir created (`.vault-meta/locks/` + `.gitkeep`, allowed). No vault knowledge edits.

## File map
- scripts/claude_agent.sh        (REWRITTEN - usage capture, P0.1)
- scripts/wiki-lock.sh           (PORTED verbatim from .co_reference + 1 adaptation, P0.2)
- scripts/vault_lease.sh         (NEW Layer-1 git lease, P0.3)
- skills/references/locking.md   (NEW canonical lock snippet reference, P0.4)
- tests/test_claude_agent_capture.sh   (P0.1)
- tests/test_wiki_lock.sh              (ported verbatim, P0.2)
- tests/test_concurrent_write.sh       (ported verbatim, P0.2)
- tests/test_vault_lease.sh            (NEW, P0.3)
- tests/test_lock_snippet_append.sh    (NEW, P0.4)
- tests/run_phase0.sh                  (NEW - runs all 5; "ALL PHASE 0 TESTS GREEN")
- <vault>/.vault-meta/locks/.gitkeep   (vault infra; live vault = Inference-Disagg)

## P0.1 - claude_agent.sh contract (the cost-accounting unblocker)
- Signature: `scripts/claude_agent.sh [--agent <id>] [claude -p flags] "prompt"`.
- Loads CLAUDE_CODE_OAUTH_TOKEN from .env, `unset ANTHROPIC_API_KEY` (unchanged).
- CAPTURE MODE (default): runs `claude -p --output-format json "$@"`, captures stdout,
  parses with the venv python (no jq): input_tokens (+cache_creation+cache_read folded
  into input), output_tokens, total_cost_usd. Calls
  `cost_tracker.record(action=<--agent or 'agent-sdk'>, role='', provider='anthropic',
  in, out, cost_usd, source='agent-sdk-credit')`. Re-emits ONLY `.result` to stdout.
  Preserves claude's exit code (CLAUDE_RC).
- PASSTHROUGH MODE: if the caller already passed `--output-format`, `exec claude -p` raw
  (no double-wrap, no parse, no cost capture).
- PARSE-FAILURE GUARD: emits the raw payload best-effort + records a zero-cost row with
  role='parse_error' (a call is never silently dropped).
- `--agent <id>` is stripped from the claude args and used as the cost `action`/role tag
  (feeds P4 per-agent window budgeting). Defaults to `agent-sdk`.
- Backward-compatible: nightly_run.sh calls `bash $AGENT "${CLAUDE_FLAGS[@]}" "$1"` with
  no --agent/--output-format -> capture mode, action=agent-sdk. No breakage.
- Env overrides used by tests: CODE_PATH (redirects ledger to a sandbox logs/), PY.

## P0.2 - wiki-lock.sh (Layer-2 per-file advisory lock)
- Verbatim port of `.co_reference/scripts/wiki-lock.sh`. ONE adaptation: VAULT_ROOT now
  resolves to the ACTIVE VAULT (not the script-parent). Order: $WIKI_LOCK_VAULT (test
  override, wins) > $VAULT_ROOT env > `agents.vault_config path` > script-parent fallback.
  Locks live at `<vault>/.vault-meta/locks/<sha1(path)>.lock`.
- Interface (exit codes): acquire <rel-path> (0 ok / 75 held-fresh / 4 bad-path),
  release <rel-path> (idempotent rm), list, clear-stale [--max-age N] (prints count),
  peek <rel-path>. STALE_AFTER_SEC=60 per-acquire reap; clear-stale default 3600 admin.
  `--stale-after-sec N` overrides per-acquire. flock'd meta-lock serializes mutations.
- Both CO tests ported verbatim (self-sandbox via WIKI_LOCK_VAULT + mktemp): 16 + 6 OK.

## P0.3 - vault_lease.sh (Layer-1 cross-host git lease) - BUILT+TESTED, WIRING DEFERRED TO P4
- Lease file `<vault>/.vault-meta/vault-lease` (committed JSON
  {holder,host,pid,acquired_at,expires_at}; ISO-8601 UTC).
- Verbs: `acquire --holder <id> --ttl <sec> [--mode auto]`, `release --holder <id>`,
  `status`. Default ttl=1800. Exit 75 = held-by-valid-holder OR lost-push-race (skip).
- acquire flow: `git pull --rebase --autostash` (only if upstream) -> if valid lease held
  by someone else -> 75; else (absent / EXPIRED reap-crashed-holder / ours) write lease,
  commit, push. Non-fast-forward push rejection -> reset HEAD~1, re-pull, exit 75.
- POLICY humans-never-block: only automated writers call acquire; `--mode auto` defers
  (75) instead of waiting; interactive/human writes never call it and are never blocked.
- status pulls first so it reflects other hosts' leases.
- Test (test_vault_lease.sh): two local clones of a bare repo (mktemp, no network) -
  A acquires; B --mode auto -> 75 (mutual exclusion); after TTL=0 expiry B reaps+acquires;
  human commit with no acquire is never blocked; release frees. 7 OK.
- DEFERRED (RESOLVED decision #8): NOT wired into any runtime path. The nightly/orchestrator
  (P4) is its only consumer; locking.md documents the exact P4 wrapper to add then.

## P0.4 - Layer-2 wiring mechanism (the snippet, NOT the wiki skills)
- Created `<vault>/.vault-meta/locks/` + `.gitkeep`.
- skills/references/locking.md = the canonical reusable snippet Phase-1 skills EMBED when
  touching shared append targets (wiki/index.md, wiki/log.md, wiki/hot.md [live
  wiki/hot/hot.md], meta/ingest_index*). Contract: acquire -> write -> release; multi-file
  in SORTED-PATH order (deadlock-free); on rc=75 retry ONCE after 2s then log+skip.
  Also documents the P4 Layer-1 lease wrapper (do-not-call-from-P1) + clear-stale hygiene.
  Locking is documented procedure inside SKILL.md bodies, NOT a hook (PreToolUse RBAC is
  separate, P1/P2).
- test_lock_snippet_append.sh: 10 workers append to a wiki/log.md-shaped target via the
  canonical acquire_or_skip snippet -> exactly seed+10 lines, each tag once, no garble,
  no orphan locks. 5 OK.

## Environment gotchas learned
- The Bash tool is wrapped by PowerShell: inline `$VAR` in `wsl.exe -- bash -lc '...'` gets
  EATEN by PowerShell (expands to empty) BEFORE reaching WSL. `$()` and `$VAR` work fine
  INSIDE a script file read from disk by bash. => put any var-using logic in a tests/*.sh
  file and run `wsl.exe -- bash -lc 'bash /abs/path.sh'`, not inline.
- git via the Bash tool on the UNC path hits "dubious ownership"; always run git/python via
  `wsl.exe -- bash -lc 'cd /home/jpietrak/second_brain && ...'` (native Linux).
- A git WORKTREE exists at .claude/worktrees/wizardly-shamir-20dff5 on a divergent stripped
  branch (commit e9cdc0b "test"); the REAL content + claude/v2-prototype live in the main
  checkout /home/jpietrak/second_brain. All Phase-0 work was done in the main checkout. A
  `git reset --hard` of the worktree was denied by the sandbox; pivoted to the main checkout.
- vault_config: `agents.vault_config path` -> $VAULT_ROOT; `env` exports VAULT/VAULT_ROOT/
  VAULT_PATH. cost_tracker.record() does NOT touch the vault dir, so it works even when the
  vault path is absent (good for hermetic tests via CODE_PATH override).

## Deferred / flagged
- P4: wire vault_lease.sh into the nightly orchestrator (acquire --mode auto around the
  batch; release after). Snippet ready in locking.md.
- P4: add `--agent <id>` tags at every nightly claude_agent.sh call site for per-agent spend.
- cost_tracker has no env override for the ledger path; tests redirect via CODE_PATH. If a
  future test needs to keep CODE_PATH real, consider a COST_LEDGER env override (minor).
