# Decision: claude-obsidian is the backend implementation model

Date: 2026-06-18. Confirmed by user preference + the reference-comparison study.

For v0.2 backend implementation we model on **claude-obsidian (CO)**, not obsidian-second-brain (OSB).
CO is the better-engineered substrate (atomic writes, fcntl locks, exit-code contracts, graceful
degradation, hermetic tests, audit-referenced fixes in code).

We COPY from CO: directory layout (`skills/<name>/SKILL.md` + `references/`/`templates/`,
`.claude/agents/<id>.md`, `hooks/`, `scripts/`, `_templates/`), the SKILL.md format (kepano
`name`+`description` with trigger phrases inline, optional `allowed-tools`), the retrieval pipeline,
hot-cache + hooks shapes, the agent-definition template (`agents/verifier.md` -> our `backend`),
the wiki-ingest skill (minus DragonScale), autoresearch, and the CLAUDE.md structure.

We DROP CO over-reach: plugin/marketplace/marketing, methodology modes + `wiki-mode.py`, DragonScale
(folds/addresses/tiling/boundary), canvas, multi-host adapters, obsidian-bases/markdown skills, MCP
transport zoo. `wiki-lock.sh` is DEFERRED (our RBAC path-partitioning avoids same-file contention).

We DROP OSB over-reach: the entire personal-productivity command sprawl (daily/task/board/project/
person/meeting/calendar/agenda/schedule/recurring/recap/review/world/log/capture/goals/ADR/devlog),
podcast/x-pulse/idea-discovery/emerge/graduate/decide/panel/visualize/create-command, and the
adapter+build+dist machinery (we are Claude-Code-only).

Full report: `plans/reference-comparison.md`.
