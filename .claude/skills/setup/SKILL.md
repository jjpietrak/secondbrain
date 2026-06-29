---
name: setup
description: >
  One-command vault setup for new team members. Creates config/vaults/<name>/, registers the
  vault in secondbrain.yaml, and scaffolds the vault folder (wiki + objective + PURPOSE.md).
  Triggers: "set up a new vault", "create a vault", "onboard a new team member", "scaffold
  a new vault", "run setup", "initialise a vault for <name>".
allowed-tools: Read, Bash, Grep, Glob
---

**Ownership: `backend` agent.** If you are NOT the `backend` subagent, DISPATCH it.

# setup

Thin wrapper over `scripts/setup.sh`. Idempotent: safe to re-run on an existing vault.

## Procedure

1. Confirm the required inputs with the user if not already provided:
   - `--vault-path`: absolute path to the Obsidian vault folder on disk.
   - `--name`: short vault identifier (letters, digits, hyphens, underscores).
   - `--purpose` (optional): one-line research purpose statement.

2. Run the setup script:
   ```bash
   REPO="$(python -m agents.vault_config path 2>/dev/null | xargs dirname | xargs dirname || \
       cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
   bash "$REPO/scripts/setup.sh" \
       --vault-path "<vault-path>" \
       --name "<vault-name>" \
       --purpose "<purpose>"
   ```
   Or invoke directly from the repo root:
   ```bash
   bash scripts/setup.sh --vault-path <abs-path> --name <name> [--purpose "<purpose>"]
   ```

3. Read and relay the printed next steps to the user.

## What it does

- Creates `config/vaults/<name>/{vault,topics,budget}.yaml` from the example template.
- Registers `<name>` in `config/secondbrain.yaml` and sets it as `default_vault`.
- Runs `wiki_init scaffold` (wiki/ + raw/ + research/ + meta/ structure + _template.md files).
- Runs `obj_init --apply` (objective/ folder tree + _template.md files).
- Writes `<vault>/objective/purpose/PURPOSE.md` from `scripts/templates/purpose_template.md`.

## After setup

The user should:
1. Edit `config/vaults/<name>/vault.yaml` to confirm the vault path and PURPOSE.
2. Edit `<vault>/objective/purpose/PURPOSE.md` with the research purpose.
3. Copy `.env.example` to `.env` and fill in `CLAUDE_CODE_OAUTH_TOKEN`.
4. Restart Claude Code.
5. Drop a PDF into `<vault>/raw/papers/` and run `wiki-ingest`.
