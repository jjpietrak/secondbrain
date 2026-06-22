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
- The user's truncated 3rd request ("I also want each h…") is still unanswered.
