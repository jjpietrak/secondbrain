# Wiki agent memory index

Role-scoped durable facts for the Wiki Agent (librarian). Read this first. Add one fact per
file and link it here with a one-line pointer. Do not duplicate what is derivable from the
vault contents or the code.

## Index
- (none yet - seeded in Phase 1, stream D)

## Standing reminders
- No web. Ingest only what is in `raw/` and approved; new-source discovery is the Research
  Agent's job.
- Write allowlist: `wiki/**`, `raw/<type>/` (approved only, raw is immutable), `meta/ingest_index*`.
  NEVER `objective/`, `research/`, `meta/{health,cost}_report/`, `meta/nightly_report/`, `docs/`.
  A PreToolUse RBAC hook enforces this.
- Frontmatter = merged superset (live home-grown fields + bi-temporal `timeline:` + frozen
  type tags), carried in each `wiki/<type>/_template.md`.
- Bi-temporal rule: never overwrite role/status/company/fact; append to `timeline:`.
- Shared append targets (`wiki/index.md`, `wiki/log.md`, `wiki/hot.md`, `meta/ingest_index*`)
  require a Layer-2 lock (`scripts/wiki-lock.sh`) before writing.
- Citations are `[[wikilinks]]` only. Every note follows ai-first-rules + write-rules. ASCII only.
