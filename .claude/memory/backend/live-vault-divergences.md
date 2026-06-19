# Fact: LIVE Inference-Disagg vault vs frozen docs/vault-schema.md (divergences)

Surveyed 2026-06-19. Active vault = `/mnt/c/Obsidian/Inference-Disagg`. The `wiki-init` reconcile
(dry-run-on-copy) must account for these. The vault is well-populated with real Iris-Tetra /
disaggregated-inference content (good for the live demo).

## Folders PRESENT on disk
Top: `.git .obsidian daily/ meta/ output/ raw/ research/ templates/ wiki/` + root files
`_CLAUDE.md`, `stepfun-mfa.md` (STRAY - a wiki page orphaned at vault root; lint should flag/relocate).
- wiki/: concepts/ entities/ sources/ synthesis/ hot/ + index.md + log.md. (hot/ is a FOLDER with
  hot.md + _template.md, NOT the schema's `wiki/hot.md` file - divergence.)
- raw/: papers/ articles/ assets/ notes/ transcripts/  (MISSING vs schema: opinions/ code/
  notebooklm/). raw/papers has 4 PDFs (2504.02263v4, 2507.19427v1, 2507.19635v1, Photons_to_Tokens),
  all already ingested per ingest_index.
- meta/: cost_report.md, health_report.md, ingest_index.{json,md}, topics.md  -> all FILES, but schema
  defines meta/{health_report,cost_report,ingest_index,nightly_report}/ as FOLDERS. Divergence: single
  files vs per-area folders. Also MISSING nightly_report/.
- research/: afd-simulator/ daily/ deep/ notebooklm/ youtube/  -> schema defines research/{deep,query}
  + research/<topic>. EXTRA: afd-simulator/, daily/, notebooklm/, youtube/. MISSING: query/.
- daily/ (top-level) and output/ exist but are NOT in the frozen schema at all.
- objective/ DOES NOT EXIST yet (P2 scaffolds it).

## Folders in schema, ABSENT on disk
- objective/* (entire area - P2).
- raw/opinions/, raw/code/, raw/notebooklm/.
- research/query/.
- meta/nightly_report/.

## Frontmatter divergence (templates exist but differ from frozen schema)
Live wiki templates use a RICHER home-grown schema:
- entities/_template.md: `type: entity`, `entity_type: person|organization|tool|company|project|place`,
  `role`, `status: seed|developing|mature|evergreen`, `aliases`, `related`, `sources`, `tags:[entity]`.
- concepts/_template.md: `type: concept`, `complexity`, `domain`, `status`, `aliases`, `related`,
  `sources`.
- sources/_template.md: `type: source`, `source_type: article|paper|transcript|video|data|note`,
  `author`, `date_published`, `url`, `confidence`, `key_claims`, `related`, `sources` (raw files).
- synthesis/_template.md: `type: synthesis`, `synthesis_type: comparison|analysis|literature-review`,
  `subjects`, `dimensions`, `verdict`, `status`.
FROZEN docs/vault-schema.md Entity schema instead has: `tags:[entity,person]`, `role`, `company`,
`last_interaction`, bi-temporal `timeline:` (from/until/learned/source). Concept: `tags:[concept]`,
`status: active|graduated|archived`, `related_projects`. Source: `tags:[source]`, `source_type`,
`source_url`, `content_hash`.
=> CONFLICT: live `status` enum (seed/developing/mature/evergreen) vs schema `active/graduated/
archived`; live has NO bi-temporal `timeline:`; live uses `type:` not the schema's tag-based typing;
live source uses `url`/`date_published` vs schema `source_url`. wiki-init must reconcile - and this is
a USER DECISION (which frontmatter wins; docs are authoritative but the live templates are richer &
already in use). FLAG.

## templates/ dir
Only `templates/Daily Note.md` (Obsidian Templater, personal-PKM). The real per-type templates live
INSIDE each wiki subfolder as `_template.md`. Schema requires a `_template` per repetitive item;
plan says scaffold `templates/`. Decide: keep `_template.md`-in-folder convention (already working,
CO-style `_templates/`) vs a central `templates/` dir. Recommend keep in-folder.

## concepts<->entities mapping - NO conflict (corrected 2026-06-19)
The frozen matrix (docs/vault-schema.md lines 67-68) is CONVENTIONAL and CORRECT:
wiki/entities = "people, tools, companies, projects" (concrete); wiki/concepts = "ideas, frameworks,
theories, methods" (abstract). This MATCHES the live vault. The earlier "reversal" reading was a
FALSE ALARM caused by STALE leftover notes at doc lines ~103 and ~317 (the formatter's old
"apparent swap" flags, written before the user fixed the matrix). The user should delete those stale
notes from docs/vault-schema.md. There is NO skill/pathing blocker; mapping is conventional.

## RESOLVED (grill-me 2026-06-19) - reconciliation decisions for the Phase 1 build
1. concepts<->entities: conventional/correct (above). Stale doc notes ~103/~317 = user cleanup.
2. Frontmatter: MERGE live + frozen into a SUPERSET - keep all live fields + ADD bi-temporal
   `timeline:` (entities; sources where useful) + frozen tags. Additive, drop nothing. User updates
   docs/vault-schema.md frontmatter to the merged superset.
3. meta/: single-artifact FILES (ingest_index.json, cost_report.md, health_report.md); folders only
   for accumulating series (nightly_report/, meta/eval/).
4. wiki/hot: collapse `wiki/hot/hot.md` -> `wiki/hot.md` (file).
5. DROP `daily/` + `output/` now (output/ returns Phase 5); research/* deferred to Phase 2.
6. wiki-query answers inline in P1; query history -> research/query/ (P2); no wiki/questions/.
7. think/challenge/connect = shared, default-owned by wiki, invokable by research.
8. Locking: build+test both layers in P0; wire Layer-2 now; wire Layer-1 lease in P4.
9. Keep obsidian-sync; retire daily/task/capture.
This RESOLVED block supersedes the "USER DECISION / FLAG" notes above in this file.
