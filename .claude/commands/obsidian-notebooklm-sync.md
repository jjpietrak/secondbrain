---
description: Bi-directional sync between an Obsidian vault folder and a REAL Google NotebookLM notebook via the `nlm` CLI. Vault .md push up as notebook sources; notebook notes pull down as .md. Costs $0 (Google cookies, no API pool).
category: research
triggers_en: ["notebooklm sync", "sync notebook", "sync my notebook", "push to notebooklm", "pull from notebooklm", "share with notebooklm"]
---

> NOTE: v0.1; superseded by the `nlm` skill in Phase 5. The `nlm` skill is not yet built; this command remains available in the interim.

Bi-directionally sync a real Google NotebookLM notebook with the vault folder
`$VAULT_ROOT/research/notebooklm/<notebook-slug>/`, using the installed `nlm` CLI.

This is the REAL-notebook counterpart to `/obsidian-notebooklm` (which is ephemeral
Gemini File Search and creates no persistent notebook). Use THIS command when you want
files to flow both ways with an actual notebook at notebooklm.google.com.

Execute for `$ARGUMENTS` (a notebook id, an `nlm` alias, or empty for the default alias):

1. **Resolve the notebook.** If `$ARGUMENTS` is given, pass it through as the `--notebook`
   value (a raw id or an `nlm` alias). If empty, use the active vault's configured notebook:
   `python -m agents.vault_config notebook` (reads `notebooklm_notebook` from
   `config/vaults/<vault>/vault.yaml`, falling back to the global `NLM_SYNC_NOTEBOOK` env).

2. **Check auth.** `nlm` sessions last ~20 minutes (browser cookies). The script gates on
   `nlm login --check` and exits 2 if expired. If it reports not authenticated, tell the
   user to run `nlm login` in a terminal (it opens a Chromium-family browser), then retry.
   Do NOT attempt `nlm login` yourself — it needs an interactive browser.

3. **Run the sync from the repo root** (`/home/jpietrak/second_brain/`):
   ```bash
   uv run -m scripts.research.notebooklm_sync --notebook "<id-or-alias>"
   ```
   First time, or when unsure, dry-run it: add `--dry-run` to see planned actions without
   changing anything. Other flags: `--pull-only`, `--push-only`, `--prune` (delete remote
   sources whose local `push/` file was removed), `--probe` (dump raw JSON shapes).

4. **What the sync does (split authority by subfolder — conflict-free):**
   - `push/`  — every `.md`/`.txt` here is pushed UP as a NotebookLM **source** (vault wins;
     these GROUND the notebook AI). Changed files are re-added (sources are not editable in
     place); change is detected by content hash via the manifest. Keep `push/` aligned with
     the vault **PURPOSE** (`_CLAUDE.md` → "## Vault purpose") — push on-purpose notes so the
     notebook's grounding stays within the vault's subject.
   - `notes/` — NotebookLM **notes** are pulled DOWN as `.md` (notebook wins). A local edit is
     never destroyed silently — it is backed up to `.conflicts/` before the notebook version
     overwrites it.
   - `.nlm-sync.json` — manifest mapping vault paths <-> source/note ids + hashes + timestamps.

5. **After sync, propagate (same as other research commands).** For each newly pulled note in
   `notes/`, treat it as conversation context for `/obsidian-save`: extract entities/concepts,
   update/create wiki pages, and link the notebook folder from today's daily note
   (`$VAULT_ROOT/daily/`).

6. **Report back:** "Synced [[research/notebooklm/<slug>]] <-> NotebookLM. Pulled N notes,
   pushed M sources." List any CONFLICT or PRUNE lines the script emitted.

---

**Setup (one-time, manual):**
- `nlm login` in a terminal (Chromium-family browser) — authenticates via cookies.
- Set the vault's notebook in `config/vaults/<vault>/vault.yaml` → `notebooklm_notebook:`
  (a notebook id — the last path segment of its URL — or an `nlm` alias). Each vault syncs
  with its own notebook; the nightly step resolves it per-vault.
- To push vault notes into the notebook, drop or symlink `.md` files into
  `research/notebooklm/<slug>/push/` and run the sync.

**Why this and not `/obsidian-notebooklm`:**
- `/obsidian-notebooklm` (Gemini File Search): one-shot grounded synthesis, no persistent
  notebook, ~$0.01-0.05 metered. Use for a quick vault-grounded answer.
- `/obsidian-notebooklm-sync` (this, `nlm`): a persistent, shareable real notebook with
  audio/mind-map/report generation, $0. Use when you want an ongoing two-way notebook.

**Cost:** $0. `nlm` uses your Google account session, not any of the three billing pools
(subscription / Agent SDK credit / pay-as-you-go). Touches no `ANTHROPIC_API_KEY` budget.

**Anti-fabrication:** never invent note or source ids; the manifest is the source of truth for
what has been synced. If `nlm` returns nothing, report that — do not assume a notebook is empty.
