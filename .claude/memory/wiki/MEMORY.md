# Wiki agent memory

Wiki agent: librarian for the vault. Owns ingest (`wiki-ingest`, `wiki-cite`), query
(`wiki-retrieve`, `wiki-query`), synthesis (`wiki-gaps`, `wiki-synth`), and maintenance
(`wiki-health`, `wiki-lint`) over `wiki/` and `raw/`. Does not browse the web.

Add one fact per file and link it here with a one-line pointer. Never duplicate facts
that are derivable from vault contents or the code.

## Index

_(link fact files here as you add them)_

## Standing conventions

- Write allowlist: `wiki/`, `raw/<type>/` (approved only), `meta/ingest_index*`. Never
  `objective/`, `research/`, `meta/{health,cost}_report/`, `meta/nightly_report/`, `docs/`.
  A PreToolUse RBAC hook enforces this -- see [[rbac]] in backend memory.
- Shared append targets (`wiki/index.md`, `wiki/log.md`, `wiki/hot/hot.md`,
  `meta/ingest_index*`) require a Layer-2 lock before writing -- see [[locking]] in
  backend memory.
- Frontmatter is the merged superset carried in each `wiki/<type>/_template.md`.
  Bi-temporal rule: never overwrite role/status/company/fact; append to `timeline:`.
- Citations are `[[wikilinks]]` only. Every note follows ai-first-rules + write-rules.
  ASCII only (no em-dashes, curly quotes, Unicode math).
