# Objective-graph edges applied + vault pushed (2026-06-22)

User asked to "push objective nodes changes to inspect newly created edges". Built+applied the
edge backfill and PUSHED the vault.

## What landed
- Code: `objectives.py` gained a `relink` verb (commit `eeb5a96`, NOT pushed). Idempotent;
  dry-run default + `--apply`. Hermetic test `tests/test_objectives_relink.py` (12). Full suite green.
- Vault: `relink --apply` over the live vault → all 23 objective nodes got `aliases: [<id>]`,
  `written_by` (USER for purpose/topic/research_question/decision; `research` for direction/
  research_question_proposal), and an auto `## Links` block between `<!-- links:auto -->` markers,
  placed after the For-future-Claude block. Plain-id frontmatter fields (topic/serves_question/
  topics/related_questions/scope) LEFT UNTOUCHED so frontier/parse_directions still work.
- Vault commit `db31121`, PUSHED: `origin/master` (`github.com/jjpietrak/secondbrain-matter`),
  `ddfa93a..db31121` (7 previously-unpushed vault commits went up; upstream now tracked).

## Edge model (per type, in the ## Links block)
- purpose: hub, NO outgoing links (inbound only). topic: `part of [[PURPOSE]]` + `question [[Q-…]]`
  (from related_questions). research_question: `topic [[T-…]]` (primary `topic` field + any
  `Secondary topic: T-…` in body). direction: `serves [[Q-…]]` + `topic [[T-…]]` per `topics`.
  research_question_proposal: `relates to [[Q-…]]`/`topic [[T-…]]` for every Q-/T-id in from_gap+body.
  decision: `governs [[PURPOSE]]` (or the specific node id if scope is one).

## KEY FINDING — auditors must be alias-aware
- `vault_health` resolves wikilinks by filename STEM and is NOT alias-aware, and its orphan check is
  inbound-only. So `--area objective` reports 62 "dead links" + 22 "orphaned" that ALL resolve in
  Obsidian via aliases (DAG leaves legitimately have no inbound). => the deferred Phase-2.5
  vault-health enhancement MUST add (a) Obsidian-alias resolution for dead-link checks and (b)
  degree-based / required-upward-edge orphan detection (not inbound-only) to validate the objective graph.

## REMAINING Phase 2.5 (still to build)
- Templates + skills so NEW objective nodes get aliases/written_by/## Links automatically (today only
  `relink --apply` adds them — run it after objective writes meanwhile).
- Decide frontmatter-wikilink-ification (+ parser updates) vs keep plain-id + body-links (current).
- wiki/ + research/ `written_by` backfill; full `generated_by`→`written_by` unify (update write-side
  scripts: deep_synth_helper, obj_reconcile_helper, wiki_gaps_fill, obj_init).
- vault-health alias-aware + degree-based checks (above), then re-verify orphans→0.
- The user's truncated 3rd request was a typo — dropped (2026-06-22).

## UPDATE 2026-06-22 — aliases REVERSED → full path-qualified wikilinks (user feedback)
User found per-node `aliases:` too noisy. Changed `relink`: (a) STOP adding aliases + remove the
auto-added own-id alias; (b) emit FULL path-qualified wikilinks in `## Links`, e.g.
`[[objective/topic/T-0004-layer-expert-disaggregation]]` (id->relpath map across all nodes; purpose ->
`[[objective/purpose/PURPOSE]]`; dangling ids fall back to `[[<id>]]`). Re-applied + PUSHED
(code 72d012c not pushed; vault 7fed0ee -> origin/master). 14 relink tests, full suite 333 green.
RESOLVES the dead-link false-positives — vault_health resolves full wikilinks by last-path-segment
stem, so objective dead-links 62->2 (--area all). vault_health NO LONGER needs alias-awareness.
ONLY remaining vault_health objective upgrade = degree-based (not inbound-only) orphan detection: the
9 "orphaned" under --area objective are DAG leaves (outbound-but-no-inbound), not true orphans.
2 genuine dangling refs remain (low priority, agent-written body content, NOT relink): DIR-0004 +
QP-0001 bodies link `[[hot.md]]` (skippable/ambiguous) — clean via a wiki-lint/research pass.

## PHASE 2.5 COMPLETE (2026-06-22) — code 821759e, vault 2839e08 (PUSHED to origin/master)
All remaining Phase-2.5 tasks done:
- `objective/hot.md` prose ids linkified to full wikilinks; `generated_by`->`written_by` (relink).
- `relink` now DROPS `generated_by` from objective nodes (node-side unify complete).
- `vault_health`: degree-based orphan for objective/research (objective orphaned 22->0) + a
  `missing_written_by` check (now 0 across wiki/objective/research). wiki keeps inbound-only orphan.
- 13 templates (objective/research/wiki `_template.md`) carry `written_by`, `generated_by` removed.
- write-side helpers (deep_synth_helper, obj_reconcile_helper, obj_init, question_lifecycle,
  wiki_gaps_fill) emit `written_by`. obj-synth/deep-synthesis/question-promote/obj-reconcile SKILLs
  now run `relink --apply` at the end so NEW nodes self-link.
- `scripts/written_by_backfill.py` (dry-run/apply) stamped `written_by` on 72 wiki/research pages
  (wiki->wiki, research report->research, research/notes->USER) + dropped residual generated_by.
- Full suite 374 green. Aliases fully removed (reverted earlier). PROVENANCE UNIFY DONE: only
  `written_by` remains (no `generated_by` anywhere).
RESIDUAL (pre-existing / low-pri, NOT regressions): 2 objective dead-links = `[[hot.md]]` body refs
in DIR-0004/QP-0001; wiki 173 dead-links = known wanted-but-uncreated forward-refs; research/notes
2 isolated + 2 missing-frontmatter (hand-authored, non-standard fm).
