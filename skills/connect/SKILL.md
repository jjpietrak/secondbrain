---
name: connect
description: "Bridge two unrelated vault domains using the link graph to force creative friction and spark new ideas, then optionally file a bridge note. Maps the link cluster around each domain, finds shared links/tags/entities or the closest semantic overlap, and produces concrete structural analogies, transfer opportunities, and collision ideas. Shared thinking tool: usable by the wiki agent and the research agent. Triggers on: connect these domains, cross-pollinate, bridge ideas, find an unexpected link, what do X and Y have in common, force a connection between, creative friction."
allowed-tools: Read, Grep, Glob, Bash
---

# connect: bridge two unrelated vault domains

Take two domains, topics, or notes that do not obviously relate and force a connection through
the vault's link graph. The value is in UNEXPECTED links - the kind that make the user say "I
never thought of it that way". If the connection is obvious, dig deeper.

Adapted from the reference `obsidian-connect` command. Differences for this vault: the bridge
note is filed to `wiki/synthesis/` (the reference used `Ideas/`, which does not exist here);
there is no `daily/` note to log to; citations are `[[wikilinks]]` ONLY.

## Scope: shared thinking tool (wiki + research)

`connect` is a SHARED reasoning skill, default-owned by the `wiki` agent and also invokable by
the `research` agent. It is read-heavy: it maps clusters and reasons over them, then returns the
connections inline. It writes ONLY if the user asks to keep the bridge note (see "Filing the
bridge note"). When it writes, the target is `wiki/synthesis/` (wiki agent's RBAC). A research
agent invoking this skill must hand the file-write back to the wiki agent rather than write into
`wiki/` itself; reading across `wiki/` is in scope for both agents. A connect run may surface a
research direction worth pursuing - that belongs in `objective/direction` (research agent's RBAC),
filed by the research agent, not written from this skill.

---

## Procedure

1. **Parse the two domains.** Two are required - the two topics, domains, or note names to
   connect (e.g. `connect "co-packaged optics" "memory hierarchy"`). If only one or none is
   given, ask the user for both before proceeding.

2. **Load orientation.** Read `objective/purpose` (the vault PURPOSE) so the connections you
   surface are steered toward what this vault is actually for, and `wiki/hot.md` for current
   focus and open threads. A connection that serves the PURPOSE beats a clever but irrelevant one.

3. **Map each domain's cluster.** For each domain, search the vault exhaustively (by title,
   `tags:`, body text, aliases) across `wiki/concepts/`, `wiki/entities/`, `wiki/synthesis/`,
   and `wiki/sources/`. Trace backlinks and outgoing `[[links]]` to build the local cluster
   around each domain. If retrieval is provisioned, use `wiki-retrieve` to widen each cluster;
   fall back to grep/Glob if it is not.

4. **Find the bridge.**
   - Look for shared `[[links]]`, shared `tags:`, or shared entities between the two clusters.
   - If a direct path exists in the link graph, trace it and explain each hop (`[[A]]` ->
     `[[shared]]` -> `[[B]]`).
   - If no direct path exists, find the closest semantic overlap - a shared concept, a
     structural metaphor, or a mechanism that recurs in both.

5. **Generate creative connections.** Produce 3-5 SPECIFIC, actionable items (not vague
   analogies):
   - **Structural analogy** - how a pattern in domain A maps to domain B (e.g. "load balancing
     across attention heads is like power-delivery balancing across an optical interposer - both
     distribute a contended resource before the bottleneck").
   - **Transfer opportunities** - what works in A that could be applied to B.
   - **Collision ideas** - new concepts that only exist at the intersection of both.

6. **Present, then offer to file.** Return the connections inline. Offer to save the best ones
   as a bridge note in `wiki/synthesis/` linking back to both source domains. If a connection
   points at an unexplored research trajectory, note it as a candidate for `objective/direction`
   (filed by the research agent).

7. **Honesty.** If the two clusters are genuinely empty or there is no defensible link, say so
   rather than fabricating a bridge. Never invent a `[[Page]]`, a shared tag, or a relationship
   that is not actually in the vault (anti-fabrication hard rule). Search exhaustively before
   claiming a domain has no notes (false-absence is the most common failure mode).

---

## Filing the bridge note

If the user wants to keep it, file a bridge note to `wiki/synthesis/` as a `type: connect` note.
It MUST follow `references/ai-first-rules.md` and `references/write-rules.md`:

- `## For future Claude` preamble; `ai-first: true`; `type: connect`; `date: YYYY-MM-DD`; the
  type in `tags:`; `sources:` listing the `[[vault notes]]` from both clusters that informed it.
- Both source domains linked as `[[wikilinks]]`, plus every concept/entity referenced (mandatory;
  no `[text](path)`). Recency markers + verbatim source URLs on every external claim. Confidence
  levels on inferences (collision ideas are usually `confidence: speculation`).
- ASCII only: ` - ` for dashes, straight quotes, `>=`/`!=` for math. No em-dash, curly quotes, or
  Unicode math (caught by the write-time validator).
- Propagation: per write-rules, the new note must be reachable - add the `[[bridge note]]` link
  back from each source domain page (or at least leave the bidirectional `[[links]]` so the graph
  is traversable).

### Locking shared append targets

Filing a synthesis note also touches the SHARED append targets `wiki/index.md`, `wiki/log.md`,
and `wiki/hot.md`. Each shared-target write MUST take a per-note lock first (Layer-2,
`scripts/wiki-lock.sh`). Acquire in sorted-path order across all files you touch, write, then
release. On `rc=75` (held), retry once after 2s, then log and skip the optional file rather than
block:

```sh
# Resolve VAULT_ROOT via: python -m agents.vault_config path
for f in "$VAULT_ROOT/wiki/index.md" "$VAULT_ROOT/wiki/log.md" "$VAULT_ROOT/wiki/hot.md"; do
  scripts/wiki-lock.sh acquire "$f" || { sleep 2; scripts/wiki-lock.sh acquire "$f" || exit 75; }
done
# ... write the bridge note + append the index/log/hot rows + backlink the source pages ...
for f in "$VAULT_ROOT/wiki/index.md" "$VAULT_ROOT/wiki/log.md" "$VAULT_ROOT/wiki/hot.md"; do
  scripts/wiki-lock.sh release "$f"
done
```

If you also edit the two source domain pages to add the backlink, lock those too (sorted-path
order, same pattern). The new bridge note file itself is new and needs no lock.

---

## How to think (10-principle mapping)

`connect` is principle 5 (CONNECT-lateral) turned into a procedure, with principle 6 (CONNECT-
system) for the wiring of the bridge note back into the graph.

- **OBSERVE:** read both clusters in full before linking; do not link on titles alone.
- **LISTEN:** let the vault PURPOSE steer which connections matter.
- **CONNECT (lateral):** the core - find the hidden relationship between distinct variables.
- **CONNECT (system):** wire the bridge note back via `[[links]]` so future-Claude can traverse
  it; lock the shared index/log/hot on the way.
- **ACCEPT:** if the best link is weak, say so; mark collision ideas as speculation.

---

## Anti-patterns

- Stopping at a vague analogy. Produce concrete, actionable connections or dig deeper.
- Linking on title-match alone without reading the cluster.
- Fabricating a shared tag, a `[[Page]]`, or a link-graph path that does not exist.
- Claiming a domain "has no notes" from a single query - search exhaustively first.
- Filing the bridge note without backlinking the source pages (leaves an orphan).
- Filing without taking the shared-target locks.
- Writing markdown `[text](path)` links instead of `[[wikilinks]]`.
