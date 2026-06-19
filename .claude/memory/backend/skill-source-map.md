# Fact: verified skill -> source-repo map (docs/skills-description.md is accurate)

All source citations in `docs/skills-description.md` were verified against the actual reference repos
(2026-06-18). Every cited CO `skills/<name>/SKILL.md` path and OSB `commands/*` / `scripts/*` path
exists and matches. Corrections/clarifications:

- **think = CO** (`skills/think/SKILL.md`, 10-principle loop). **challenge = OSB** (`commands/obsidian-challenge.md`).
  **connect = OSB** (`commands/obsidian-connect.md`). The catalog row "challenge/connect/think | both
  repos" resolves to: think -> CO, challenge+connect -> OSB. CO has NO challenge command.
- wiki-init: catalog cites CO `skills/wiki-cli/SKILL.md`; the actual scaffold logic lives in CO
  `skills/wiki/SKILL.md` + `references/`. Both apply.
- wiki-lint: CO `skills/wiki-lint/SKILL.md` (skill-side) complements OSB `scripts/vault_health.py`
  (script-side, our backend `wiki-health`).
- web-rank reuses CO `scripts/rerank.py` (same file as wiki-retrieve).
- research / research-deep -> OSB `scripts/research/{research,research_deep}.py`; both have a free
  key-less mode (emit JSON, Claude synthesizes) = our $0 route.
- agent-learn is NEW; OSB `obsidian-learn` (prune vault learnings) is only a loose analogue, not
  agent-memory updating.

NEW (no reference): wiki-cite, obj-query/obj-synth/obj-reconcile, web-scrape, agent-learn, newsletter.
DROP candidates documented in `plans/reference-comparison.md` section 4.
