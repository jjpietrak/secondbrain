# Fact: wiki-init structural cleanup (Change1 + Change2) - DONE 2026-06-19

Branch `claude/v2-prototype` (code repo) + live vault Inference-Disagg. SCHEMA_VERSION
bumped 1 -> 2 in scripts/wiki_init.py. All 15 on-disk verification checks pass. Not pushed.

## Change 1: templates/ removal + producer _template.md

### What changed (wiki_init.py)
- `DROP_FOLDERS` expanded: `["daily", "output", "templates"]`.
  `templates/` = old Obsidian Templater dir holding "Daily Note.md" only; not part of v0.2
  schema. The v0.2 convention is a colocated `_template.md` INSIDE each producer folder.
- `TEMPLATES` dict expanded with 4 producer-folder templates:
  - `meta/health_report/_template.md` - mirrors vault_health.py render_report structure
  - `meta/nightly_report/_template.md` - nightly digest (pending approval / research / lint)
  - `research/deep/_template.md` - deep research report (question/methodology/findings/synthesis)
  - `research/query/_template.md` - saved Q&A result (question/answer/cited-pages/gaps)
- Added 4 factory functions: `_health_report_template`, `_nightly_report_template`,
  `_deep_research_template`, `_query_template`. Each: ASCII, `## For future Claude` preamble,
  `ai-first: true`, YAML frontmatter aligned to what the producing code/skill writes.
- `meta/cost_report.md` is a SINGLE regenerated FILE - no folder template needed (left as is).
- `Plan.templates_written` now covers all 8 template paths (4 wiki/ + 4 producer).

## Change 2: vault _CLAUDE.md deletion

### What changed (wiki_init.py)
- Added `VAULT_CLAUDE_FILE = "_CLAUDE.md"` constant.
- `Plan` gained `vault_claude_deleted: bool` field (also included in `is_noop()` check).
- `reconcile_tree()` step 6: deletes vault root `_CLAUDE.md` if present.
  Rationale: agent rules live in the CODE repo (CLAUDE.md / docs/ / .claude/).
  Vault PURPOSE lives in `config/vaults/<v>/vault.yaml` (agents.vault_config purpose).
  The vault `_CLAUDE.md` was a v0.1 artefact and must not be recreated in the vault.
- `render_report()` updated: adds "Producer-folder templates" + "vault _CLAUDE.md removal"
  sections to the dry-run report.
- `print_summary()` updated: prints `vault _CLAUDE.md del` line.
- `SCHEMA_VERSION` bumped 1 -> 2.

### Other code changes
- `scripts/wiki_lint_extra.py`: removed `_CLAUDE.md` from `ROOT_ALLOW`. Added comment
  explaining the v0.2 rationale. If a vault `_CLAUDE.md` somehow reappears, lint will flag it.
- `agents/vault_config.py`: docstring updated to remove v0.1 `_CLAUDE.md` reference; now
  says agent rules live in the code repo + vault.yaml has purpose.

## Test updates
- `tests/test_wiki_init.py`:
  - `make_fixture`: added `templates/Daily Note.md` + vault `_CLAUDE.md` to fixture.
  - `test_dry_run_makes_zero_source_changes`: added assertions for `templates` + `_CLAUDE.md`
    in report; `plan.vault_claude_deleted is True`; 4 producer template `plan.template_actions`.
  - `test_apply_is_idempotent`: added `templates/` gone + `_CLAUDE.md` gone + 4 producer
    template presence + preamble/ai-first checks.
- `tests/test_wiki_lint_extra.py`:
  - Removed `_CLAUDE.md` from fixture (it was only created to assert it was NOT stray; now
    _CLAUDE.md IS stray if found - no fixture entry needed).
  - Removed `("_CLAUDE.md not flagged stray", ...)` check from checks list. Added comment.

## On-disk verification (live vault, all PASS)
templates/ removed, meta/health_report/_template.md exists (+preamble+ai-first),
meta/nightly_report/_template.md exists (+preamble+ai-first), research/deep/_template.md
exists (+preamble+ai-first), research/query/_template.md exists (+preamble+ai-first),
_CLAUDE.md removed, PURPOSE resolvable via agents.vault_config purpose (full purpose string
returned), all 4 wiki/ _template.md untouched. 15/15 pass.

## Commit chain (NOT pushed)
- Vault safety checkpoint: ddf3a6b (pre-wiki-init-v2, captures mla-multi-head stub + new PDF)
- Vault apply commit: df2c5df (wiki-init v2 apply: templates/ drop, producer _template.md,
  vault _CLAUDE.md remove) - full chain now ddf3a6b -> df2c5df, revertible.
- Code commit: see git log on claude/v2-prototype after this session.

## Flagged for user (docs/ is frozen; backend cannot edit)
`docs/vault-schema.md` should be updated to:
1. Drop the `templates/` folder from the vault tree diagram.
2. Drop the `_CLAUDE.md` entry from the vault root section.
3. Add a note that producer folders (meta/health_report, meta/nightly_report, research/deep,
   research/query) each carry a colocated `_template.md` (the v0.2 per-producer convention).
This is purely a docs maintenance item - no code blocker.

## Skills NOT changed
`skills/references/claude-md-template.md`, `claude-md-assistant-template.md`, `vault-schema.md`
`ai-first-rules.md` - these are reference skill files about the _CLAUDE.md pattern for
OTHER vaults / prior convention. They are documentation, not functional callers of the vault
_CLAUDE.md. Left as-is (do not change reference docs without user approval).
`.claude/commands/obsidian-*.md` - pre-v0.2 commands that reference vault _CLAUDE.md. These
are being superseded by skills/ but are not broken. Reconciliation deferred (not in scope).
